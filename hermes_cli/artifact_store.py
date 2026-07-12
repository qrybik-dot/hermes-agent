"""Durable user-facing artifact storage for Hermes.

The physical file registry is intentionally separate from Hermes Knowledge.
ArtifactStore owns bytes, checksums, versions, retention, and storage limits.
Hermes Knowledge owns semantic cards and graph relations, linked by artifact_id.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(os.environ.get("HERMES_ARTIFACT_ROOT", "/srv/hermes-artifacts"))
DEFAULT_HARD_LIMIT = 4 * 1024**3
DEFAULT_WARNING_LIMIT = 3 * 1024**3
DEFAULT_CRITICAL_LIMIT = int(3.6 * 1024**3)
DEFAULT_EMERGENCY_LIMIT = int(3.9 * 1024**3)

ALLOWED_EXTENSIONS = frozenset(
    {
        ".md", ".txt", ".rtf", ".doc", ".docx", ".odt",
        ".xls", ".xlsx", ".csv", ".ods",
        ".pdf", ".html", ".htm", ".ppt", ".pptx", ".odp",
        ".json", ".yaml", ".yml", ".xml",
    }
)
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg"})
TECHNICAL_NAME_HINTS = frozenset(
    {
        "preview", "render", "converted", "conversion", "tmp", "temp",
        "cache", "thumbnail", "intermediate", "scratch",
    }
)
STATUS_VALUES = frozenset({"final", "draft", "temporary", "superseded", "trash", "archived"})


@dataclass(frozen=True)
class Limits:
    hard: int = DEFAULT_HARD_LIMIT
    warning: int = DEFAULT_WARNING_LIMIT
    critical: int = DEFAULT_CRITICAL_LIMIT
    emergency: int = DEFAULT_EMERGENCY_LIMIT

    @classmethod
    def from_env(cls) -> "Limits":
        def value(name: str, default: int) -> int:
            raw = os.environ.get(name)
            if not raw:
                return default
            try:
                parsed = int(raw)
            except ValueError:
                return default
            return parsed if parsed > 0 else default

        limits = cls(
            hard=value("HERMES_ARTIFACT_HARD_LIMIT_BYTES", DEFAULT_HARD_LIMIT),
            warning=value("HERMES_ARTIFACT_WARNING_LIMIT_BYTES", DEFAULT_WARNING_LIMIT),
            critical=value("HERMES_ARTIFACT_CRITICAL_LIMIT_BYTES", DEFAULT_CRITICAL_LIMIT),
            emergency=value("HERMES_ARTIFACT_EMERGENCY_LIMIT_BYTES", DEFAULT_EMERGENCY_LIMIT),
        )
        if not (0 < limits.warning < limits.critical < limits.emergency < limits.hard):
            return cls()
        return limits


class ArtifactStoreError(RuntimeError):
    pass


class UnsupportedArtifact(ArtifactStoreError):
    pass


class StorageLimitExceeded(ArtifactStoreError):
    pass


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sanitize_filename(name: str, limit: int = 160) -> str:
    name = Path(name or "artifact").name.replace("\x00", "")
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", name).strip(" ._")
    if not name:
        name = "artifact"
    stem, suffix = os.path.splitext(name)
    suffix = suffix[:16]
    available = max(16, limit - len(suffix))
    return stem[:available].rstrip(" ._") + suffix


def normalize_version_group(name: str) -> str:
    stem = Path(name).stem.lower().replace("ё", "е")
    stem = re.sub(r"\b(?:final|draft|updated|update|copy|копия|черновик|финал|обновлено|обновленный|обновленная)\b", " ", stem)
    stem = re.sub(r"(?:^|[-_ ])v(?:er(?:sion)?)?\s*\d+(?:\.\d+)*\b", " ", stem)
    stem = re.sub(r"\b20\d{2}[-_.]\d{1,2}[-_.]\d{1,2}\b", " ", stem)
    stem = re.sub(r"\(\d+\)$", " ", stem)
    stem = re.sub(r"[^a-zа-я0-9]+", "-", stem, flags=re.I).strip("-")
    return stem[:160] or "artifact"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_storage_enabled() -> bool:
    return os.environ.get("HERMES_ARTIFACT_STORE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


class ArtifactStore:
    def __init__(self, root: Path | str = DEFAULT_ROOT, limits: Limits | None = None):
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.staging_dir = self.root / "staging"
        self.trash_dir = self.root / "trash"
        self.state_dir = self.root / "state"
        self.db_path = self.root / "artifact_registry.sqlite3"
        self.lock_path = self.state_dir / ".artifact-store.lock"
        self.limits = limits or Limits.from_env()

    def ensure_layout(self) -> None:
        for directory in (self.root, self.files_dir, self.staging_dir, self.trash_dir, self.state_dir):
            directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o2770)
            for directory in (self.files_dir, self.staging_dir, self.trash_dir, self.state_dir):
                os.chmod(directory, 0o2770)
        except PermissionError:
            pass

    @contextlib.contextmanager
    def lock(self):
        self.ensure_layout()
        with self.lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield

    def connect(self) -> sqlite3.Connection:
        self.ensure_layout()
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            os.chmod(self.db_path, 0o660)
        except PermissionError:
            pass
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS artifacts(
              artifact_id TEXT PRIMARY KEY,
              original_name TEXT NOT NULL,
              stored_path TEXT NOT NULL UNIQUE,
              extension TEXT NOT NULL,
              size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
              sha256 TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_accessed_at TEXT NOT NULL,
              source_system TEXT NOT NULL,
              source_workspace TEXT,
              source_chat TEXT,
              title TEXT,
              summary TEXT,
              knowledge_project TEXT,
              version_group TEXT NOT NULL,
              version INTEGER NOT NULL DEFAULT 1,
              is_current INTEGER NOT NULL DEFAULT 1,
              retention_days INTEGER,
              delete_after TEXT,
              pinned INTEGER NOT NULL DEFAULT 0,
              local_available INTEGER NOT NULL DEFAULT 1,
              duplicate_of TEXT,
              supersedes TEXT,
              superseded_by TEXT,
              knowledge_candidate_id TEXT,
              knowledge_card_path TEXT,
              knowledge_sync_status TEXT NOT NULL DEFAULT 'pending',
              metadata_json TEXT NOT NULL DEFAULT '{}',
              deleted_at TEXT,
              archived_to TEXT,
              FOREIGN KEY(duplicate_of) REFERENCES artifacts(artifact_id),
              FOREIGN KEY(supersedes) REFERENCES artifacts(artifact_id),
              FOREIGN KEY(superseded_by) REFERENCES artifacts(artifact_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS artifacts_all_live_sha
              ON artifacts(sha256) WHERE local_available=1;
            CREATE INDEX IF NOT EXISTS artifacts_name ON artifacts(original_name);
            CREATE INDEX IF NOT EXISTS artifacts_group_current ON artifacts(version_group,is_current);
            CREATE INDEX IF NOT EXISTS artifacts_status_created ON artifacts(status,created_at);
            CREATE INDEX IF NOT EXISTS artifacts_knowledge_sync ON artifacts(knowledge_sync_status,created_at);
            CREATE TABLE IF NOT EXISTS artifact_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              artifact_id TEXT,
              ts TEXT NOT NULL,
              event TEXT NOT NULL,
              data_json TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
            );
            CREATE INDEX IF NOT EXISTS artifact_events_artifact ON artifact_events(artifact_id,id);
            CREATE TABLE IF NOT EXISTS storage_alerts(
              threshold TEXT PRIMARY KEY,
              active INTEGER NOT NULL DEFAULT 0,
              first_crossed_at TEXT,
              last_notified_at TEXT,
              cleared_at TEXT,
              usage_bytes INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(artifacts)").fetchall()}
        if "status_before_trash" not in columns:
            conn.execute("ALTER TABLE artifacts ADD COLUMN status_before_trash TEXT")
        if "is_current_before_trash" not in columns:
            conn.execute("ALTER TABLE artifacts ADD COLUMN is_current_before_trash INTEGER")
        conn.commit()
        for state_file in (self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")):
            if state_file.exists():
                try:
                    os.chmod(state_file, 0o660)
                except PermissionError:
                    pass
        return conn

    def _event(self, conn: sqlite3.Connection, artifact_id: str | None, event: str, data: dict[str, Any] | None = None) -> None:
        conn.execute(
            "INSERT INTO artifact_events(artifact_id,ts,event,data_json) VALUES(?,?,?,?)",
            (artifact_id, utcnow(), event, json_dumps(data or {})),
        )

    def _row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["is_current"] = bool(result.get("is_current"))
        result["pinned"] = bool(result.get("pinned"))
        result["local_available"] = bool(result.get("local_available"))
        try:
            result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            result["metadata"] = {}
        return result

    def usage_bytes(self, conn: sqlite3.Connection | None = None) -> int:
        own = conn is None
        conn = conn or self.connect()
        # Physical quota includes trash: soft-deleted bytes still occupy disk
        # and must not allow the 4 GiB hard limit to be bypassed.
        value = conn.execute(
            "SELECT COALESCE(SUM(size_bytes),0) FROM artifacts WHERE local_available=1"
        ).fetchone()[0]
        if own:
            conn.close()
        return int(value or 0)

    def health(self) -> dict[str, Any]:
        with self.connect() as conn:
            usage = self.usage_bytes(conn)
            counts = {
                row["status"]: int(row["count"])
                for row in conn.execute(
                    "SELECT status,COUNT(*) AS count FROM artifacts GROUP BY status"
                ).fetchall()
            }
            total = int(conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])
            pending = int(conn.execute(
                "SELECT COUNT(*) FROM artifacts WHERE knowledge_sync_status IN ('pending','failed')"
            ).fetchone()[0])
        level = "ok"
        if usage >= self.limits.emergency:
            level = "emergency"
        elif usage >= self.limits.critical:
            level = "critical"
        elif usage >= self.limits.warning:
            level = "warning"
        return {
            "status": level,
            "enabled": artifact_storage_enabled(),
            "root": str(self.root),
            "usage_bytes": usage,
            "usage_gib": round(usage / 1024**3, 3),
            "limits": asdict(self.limits),
            "remaining_bytes": max(0, self.limits.hard - usage),
            "artifact_count": total,
            "counts_by_status": counts,
            "knowledge_sync_pending": pending,
        }

    def _validate_source(self, source: Path, status: str) -> tuple[str, int]:
        if status not in STATUS_VALUES:
            raise ArtifactStoreError(f"unsupported artifact status: {status}")
        if not source.is_file():
            raise ArtifactStoreError(f"artifact file does not exist: {source}")
        extension = source.suffix.lower()
        if extension in IMAGE_EXTENSIONS:
            raise UnsupportedArtifact("images are intentionally excluded from artifact storage")
        if extension not in ALLOWED_EXTENSIONS:
            raise UnsupportedArtifact(f"unsupported artifact extension: {extension or '<none>'}")
        size = source.stat().st_size
        if size <= 0:
            raise UnsupportedArtifact("empty files are not stored")
        max_file = int(os.environ.get("HERMES_ARTIFACT_MAX_FILE_BYTES", str(100 * 1024**2)))
        if size > max_file:
            raise UnsupportedArtifact(f"file exceeds per-artifact limit: {size} > {max_file}")
        return extension, size

    def _default_retention(self, status: str) -> int | None:
        return {
            "temporary": 7,
            "draft": 30,
            "superseded": 90,
            "trash": 14,
            "final": 365,
            "archived": None,
        }.get(status)

    def _delete_after(self, retention_days: int | None) -> str | None:
        if retention_days is None:
            return None
        return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=retention_days)).replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")

    def _strong_version_match(self, existing: sqlite3.Row, *, version_group: str, source_workspace: str | None, extension: str) -> bool:
        if existing["version_group"] != version_group or existing["extension"] != extension:
            return False
        old_workspace = existing["source_workspace"] or ""
        new_workspace = source_workspace or ""
        return bool(old_workspace and new_workspace and old_workspace == new_workspace)

    def _restore_trashed_row(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        now: str,
        event: str,
    ) -> sqlite3.Row:
        source = Path(row["stored_path"])
        if not source.is_file():
            raise ArtifactStoreError("trash bytes are no longer available")
        date = dt.datetime.now(dt.timezone.utc)
        target_dir = self.files_dir / f"{date:%Y}" / f"{date:%m}" / f"{date:%d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{row['artifact_id']}--{sanitize_filename(row['original_name'])}"
        other_current = conn.execute(
            "SELECT artifact_id FROM artifacts WHERE version_group=? AND is_current=1 "
            "AND artifact_id!=? AND status NOT IN ('trash','archived') LIMIT 1",
            (row["version_group"], row["artifact_id"]),
        ).fetchone()
        prior_status = row["status_before_trash"] or "final"
        prior_current = bool(row["is_current_before_trash"] if row["is_current_before_trash"] is not None else 1)
        if other_current:
            restored_status = "superseded"
            restored_current = 0
            restored_expiry = self._delete_after(self._default_retention("superseded"))
        else:
            restored_status = prior_status if prior_status in STATUS_VALUES - {"trash"} else "final"
            restored_current = int(prior_current)
            restored_expiry = None if row["pinned"] else self._delete_after(row["retention_days"])
        os.replace(source, target)
        conn.execute(
            "UPDATE artifacts SET status=?,stored_path=?,local_available=1,is_current=?,updated_at=?,"
            "deleted_at=NULL,delete_after=?,status_before_trash=NULL,is_current_before_trash=NULL "
            "WHERE artifact_id=?",
            (
                restored_status, str(target), restored_current, now, restored_expiry,
                row["artifact_id"],
            ),
        )
        self._event(conn, row["artifact_id"], event, {"restored_status": restored_status})
        return conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id=?",
            (row["artifact_id"],),
        ).fetchone()

    def capture(
        self,
        file_path: str | Path,
        *,
        status: str = "final",
        source_system: str = "hermes",
        source_workspace: str | None = None,
        source_chat: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        knowledge_project: str | None = None,
        version_group: str | None = None,
        retention_days: int | None = None,
        pinned: bool = False,
        metadata: dict[str, Any] | None = None,
        require_enabled: bool = False,
    ) -> dict[str, Any]:
        if require_enabled and not artifact_storage_enabled():
            return {"status": "disabled", "saved": False}
        source = Path(file_path).expanduser().resolve()
        extension, size = self._validate_source(source, status)
        original_name = sanitize_filename(source.name)
        digest = sha256_file(source)
        now = utcnow()
        group = normalize_version_group(version_group or original_name)
        retention = retention_days if retention_days is not None else self._default_retention(status)
        delete_after = None if pinned else self._delete_after(retention)
        metadata_value = dict(metadata or {})
        metadata_value.setdefault("original_source_path_name", source.name)

        with self.lock():
            conn = self.connect()
            created_target: Path | None = None
            committed = False
            try:
                existing = conn.execute(
                    "SELECT * FROM artifacts WHERE sha256=? AND local_available=1 LIMIT 1",
                    (digest,),
                ).fetchone()
                if existing and existing["status"] == "trash":
                    try:
                        existing = self._restore_trashed_row(
                            conn,
                            existing,
                            now=now,
                            event="restored_on_deduplicate",
                        )
                        conn.execute(
                            "UPDATE artifacts SET last_accessed_at=? WHERE artifact_id=?",
                            (now, existing["artifact_id"]),
                        )
                        conn.commit()
                    except ArtifactStoreError:
                        conn.execute(
                            "UPDATE artifacts SET local_available=0,updated_at=? WHERE artifact_id=?",
                            (now, existing["artifact_id"]),
                        )
                        self._event(conn, existing["artifact_id"], "trash_bytes_missing_on_capture")
                        conn.commit()
                        existing = None
                if existing:
                    conn.execute(
                        "UPDATE artifacts SET last_accessed_at=?,updated_at=? WHERE artifact_id=?",
                        (now, now, existing["artifact_id"]),
                    )
                    self._event(conn, existing["artifact_id"], "deduplicated", {"source_name": original_name})
                    conn.commit()
                    refreshed = conn.execute(
                        "SELECT * FROM artifacts WHERE artifact_id=?",
                        (existing["artifact_id"],),
                    ).fetchone()
                    result = self._row(refreshed) or {}
                    result.update({"saved": False, "deduplicated": True, "status": "duplicate"})
                    return result

                usage = self.usage_bytes(conn)
                if usage + size > self.limits.hard:
                    self.cleanup(dry_run=False, safe_only=True, _locked=True, _connection=conn)
                    usage = self.usage_bytes(conn)
                if usage + size > self.limits.hard:
                    raise StorageLimitExceeded(
                        f"artifact storage hard limit would be exceeded: {usage + size} > {self.limits.hard}"
                    )

                prior = conn.execute(
                    "SELECT * FROM artifacts WHERE version_group=? AND is_current=1 AND local_available=1 "
                    "AND status NOT IN ('trash','archived') ORDER BY version DESC LIMIT 1",
                    (group,),
                ).fetchone()
                latest = prior or conn.execute(
                    "SELECT * FROM artifacts WHERE version_group=? ORDER BY version DESC,created_at DESC LIMIT 1",
                    (group,),
                ).fetchone()
                explicit_group = bool(version_group)
                strong_match = bool(latest and (explicit_group or self._strong_version_match(
                    latest, version_group=group, source_workspace=source_workspace, extension=extension
                )))
                version = int(latest["version"] + 1) if strong_match else 1
                supersedes = latest["artifact_id"] if strong_match else None

                artifact_id = "art_" + uuid.uuid4().hex[:20]
                date = dt.datetime.now(dt.timezone.utc)
                target_dir = self.files_dir / f"{date:%Y}" / f"{date:%m}" / f"{date:%d}"
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f"{artifact_id}--{original_name}"
                fd, temporary_name = tempfile.mkstemp(prefix=f".{artifact_id}.", suffix=".tmp", dir=self.staging_dir)
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
                        shutil.copyfileobj(inp, out, length=1024 * 1024)
                        out.flush()
                        os.fsync(out.fileno())
                    if temporary.stat().st_size != size or sha256_file(temporary) != digest:
                        raise ArtifactStoreError("artifact staging read-back checksum mismatch")
                    os.chmod(temporary, 0o660)
                    os.replace(temporary, target)
                    created_target = target
                finally:
                    temporary.unlink(missing_ok=True)

                conn.execute(
                    """
                    INSERT INTO artifacts(
                      artifact_id,original_name,stored_path,extension,size_bytes,sha256,status,
                      created_at,updated_at,last_accessed_at,source_system,source_workspace,source_chat,
                      title,summary,knowledge_project,version_group,version,is_current,retention_days,
                      delete_after,pinned,local_available,supersedes,metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        artifact_id, original_name, str(target), extension, size, digest, status,
                        now, now, now, source_system, source_workspace, source_chat,
                        title or Path(original_name).stem, summary, knowledge_project,
                        group, version, 1, retention, delete_after, int(pinned), 1,
                        supersedes, json_dumps(metadata_value),
                    ),
                )
                if strong_match and latest:
                    next_status = "trash" if latest["status"] == "trash" else "superseded"
                    conn.execute(
                        "UPDATE artifacts SET is_current=0,status=?,superseded_by=?,updated_at=?,"
                        "delete_after=CASE WHEN pinned=1 THEN NULL WHEN ?='trash' THEN delete_after ELSE ? END "
                        "WHERE artifact_id=?",
                        (
                            next_status, artifact_id, now, next_status,
                            self._delete_after(self._default_retention("superseded")),
                            latest["artifact_id"],
                        ),
                    )
                    self._event(conn, latest["artifact_id"], "superseded", {"by": artifact_id})
                self._event(conn, artifact_id, "captured", {"source_system": source_system, "size_bytes": size})
                conn.commit()
                committed = True
                row = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
                result = self._row(row) or {}
                result.update({"saved": True, "deduplicated": False})
                return result
            except Exception:
                conn.rollback()
                if created_target is not None and not committed:
                    created_target.unlink(missing_ok=True)
                raise
            finally:
                conn.close()

    def get(self, artifact_id: str, *, touch: bool = True) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
            if row and touch:
                conn.execute(
                    "UPDATE artifacts SET last_accessed_at=? WHERE artifact_id=?",
                    (utcnow(), artifact_id),
                )
                conn.commit()
            return self._row(row)

    def search(self, query: str, *, limit: int = 20, current_only: bool = False) -> list[dict[str, Any]]:
        # SQLite's built-in LOWER() is ASCII-only in the stock build, so SQL
        # LIKE silently misses Cyrillic names. The registry is intentionally
        # small (4 GiB cap), therefore a bounded metadata scan plus Unicode
        # casefold is both correct and cheap.
        terms = [term.casefold() for term in re.findall(r"[\wА-Яа-яЁё-]+", query or "") if term]
        clauses = ["status != 'trash'"]
        if current_only:
            clauses.append("is_current=1")
        sql = (
            "SELECT * FROM artifacts WHERE " + " AND ".join(clauses) +
            " ORDER BY is_current DESC,updated_at DESC LIMIT 2000"
        )
        result: list[dict[str, Any]] = []
        max_items = max(1, min(int(limit), 100))
        with self.connect() as conn:
            for row in conn.execute(sql).fetchall():
                item = self._row(row)
                if not item:
                    continue
                haystack = " ".join(
                    str(value or "")
                    for value in (
                        item.get("original_name"), item.get("title"), item.get("summary"),
                        item.get("knowledge_project"), json_dumps(item.get("metadata") or {}),
                    )
                ).casefold()
                if terms and not all(term in haystack for term in terms):
                    continue
                result.append(item)
                if len(result) >= max_items:
                    break
        return result

    def versions(self, artifact_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT version_group FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
            if not row:
                return []
            return [
                self._row(item)
                for item in conn.execute(
                    "SELECT * FROM artifacts WHERE version_group=? ORDER BY version DESC,created_at DESC",
                    (row["version_group"],),
                ).fetchall()
                if item
            ]

    def pin(self, artifact_id: str, pinned: bool = True) -> dict[str, Any]:
        with self.lock(), self.connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
            if not row:
                raise ArtifactStoreError("artifact not found")
            conn.execute(
                "UPDATE artifacts SET pinned=?,delete_after=?,updated_at=? WHERE artifact_id=?",
                (int(pinned), None if pinned else self._delete_after(row["retention_days"]), utcnow(), artifact_id),
            )
            self._event(conn, artifact_id, "pinned" if pinned else "unpinned")
            conn.commit()
            return self.get(artifact_id) or {}

    def soft_delete(self, artifact_id: str, *, reason: str = "user") -> dict[str, Any]:
        with self.lock(), self.connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
            if not row:
                raise ArtifactStoreError("artifact not found")
            if row["pinned"]:
                raise ArtifactStoreError("pinned artifact must be unpinned before deletion")
            source = Path(row["stored_path"])
            target = self.trash_dir / f"{artifact_id}--{sanitize_filename(row['original_name'])}"
            if row["local_available"] and source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
            now = utcnow()
            conn.execute(
                "UPDATE artifacts SET status_before_trash=status,is_current_before_trash=is_current,"
                "status='trash',stored_path=?,is_current=0,updated_at=?,deleted_at=?,"
                "delete_after=? WHERE artifact_id=?",
                (str(target), now, now, self._delete_after(14), artifact_id),
            )
            self._event(conn, artifact_id, "soft_deleted", {"reason": reason})
            conn.commit()
            return self._row(conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()) or {}

    def restore(self, artifact_id: str) -> dict[str, Any]:
        with self.lock(), self.connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
            if not row or row["status"] != "trash":
                raise ArtifactStoreError("trash artifact not found")
            restored = self._restore_trashed_row(
                conn,
                row,
                now=utcnow(),
                event="restored",
            )
            conn.commit()
            return self._row(restored) or {}

    def mark_knowledge_sync(
        self,
        artifact_id: str,
        status: str,
        *,
        candidate_id: str | None = None,
        card_path: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE artifacts SET knowledge_sync_status=?,knowledge_candidate_id=COALESCE(?,knowledge_candidate_id),"
                "knowledge_card_path=COALESCE(?,knowledge_card_path),updated_at=? WHERE artifact_id=?",
                (status, candidate_id, card_path, utcnow(), artifact_id),
            )
            self._event(conn, artifact_id, "knowledge_sync_" + status, {"error": error} if error else {})
            conn.commit()

    def pending_knowledge(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE knowledge_sync_status IN ('pending','failed') "
                "AND status != 'trash' ORDER BY created_at LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
            return [self._row(row) for row in rows if row]

    def cleanup(
        self,
        *,
        dry_run: bool = True,
        safe_only: bool = True,
        now: dt.datetime | None = None,
        _locked: bool = False,
        _connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        now = now or dt.datetime.now(dt.timezone.utc)
        own_connection = _connection is None
        conn = _connection or self.connect()
        candidates: list[sqlite3.Row] = []
        try:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE pinned=0 AND local_available=1 "
                "AND status IN ('temporary','draft','superseded','trash') ORDER BY created_at"
            ).fetchall()
            for row in rows:
                expiry = parse_utc(row["delete_after"])
                if expiry and expiry <= now:
                    candidates.append(row)
            projected = sum(int(row["size_bytes"]) for row in candidates)
            result = {
                "dry_run": dry_run,
                "safe_only": safe_only,
                "candidate_count": len(candidates),
                "reclaimable_bytes": projected,
                "artifacts": [row["artifact_id"] for row in candidates[:200]],
                "deleted": [],
                "errors": [],
            }
            if dry_run:
                return result

            lock_context = contextlib.nullcontext() if _locked else self.lock()
            with lock_context:
                for row in candidates:
                    artifact_id = row["artifact_id"]
                    path = Path(row["stored_path"])
                    try:
                        path.unlink(missing_ok=True)
                        conn.execute(
                            "UPDATE artifacts SET local_available=0,updated_at=?,archived_to=COALESCE(archived_to,'deleted-retention') "
                            "WHERE artifact_id=?",
                            (utcnow(), artifact_id),
                        )
                        self._event(conn, artifact_id, "bytes_deleted_retention")
                        result["deleted"].append(artifact_id)
                    except OSError as exc:
                        result["errors"].append({"artifact_id": artifact_id, "error": str(exc)})
                conn.commit()
            return result
        finally:
            if own_connection:
                conn.close()

    def reconcile(self) -> dict[str, Any]:
        missing: list[str] = []
        recovered: list[str] = []
        orphan_registered: list[str] = []
        duplicate_orphans_removed: list[str] = []
        stale_staging_removed: list[str] = []
        now = utcnow()
        artifact_name = re.compile(r"^(art_[a-f0-9]{20})--(.+)$")
        with self.lock(), self.connect() as conn:
            rows = {
                row["artifact_id"]: row
                for row in conn.execute("SELECT * FROM artifacts").fetchall()
            }
            actual_by_id: dict[str, Path] = {}
            for root, inferred_status in ((self.files_dir, None), (self.trash_dir, "trash")):
                for path in root.rglob("*"):
                    if not path.is_file():
                        continue
                    match = artifact_name.match(path.name)
                    if not match:
                        continue
                    artifact_id, original_name = match.groups()
                    actual_by_id[artifact_id] = path
                    row = rows.get(artifact_id)
                    if row:
                        if Path(row["stored_path"]) != path or not row["local_available"]:
                            new_status = inferred_status or row["status"]
                            conn.execute(
                                "UPDATE artifacts SET stored_path=?,local_available=1,status=?,updated_at=? "
                                "WHERE artifact_id=?",
                                (str(path), new_status, now, artifact_id),
                            )
                            self._event(conn, artifact_id, "reconcile_recovered_path", {"path": str(path)})
                            recovered.append(artifact_id)
                        continue

                    digest = sha256_file(path)
                    duplicate = conn.execute(
                        "SELECT artifact_id FROM artifacts WHERE sha256=? AND local_available=1 LIMIT 1",
                        (digest,),
                    ).fetchone()
                    if duplicate:
                        path.unlink(missing_ok=True)
                        duplicate_orphans_removed.append(str(path))
                        continue
                    extension = Path(original_name).suffix.lower()
                    if extension not in ALLOWED_EXTENSIONS or path.stat().st_size <= 0:
                        continue
                    created = dt.datetime.fromtimestamp(
                        path.stat().st_mtime,
                        tz=dt.timezone.utc,
                    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    status = inferred_status or "draft"
                    retention = 14 if status == "trash" else 30
                    conn.execute(
                        """
                        INSERT INTO artifacts(
                          artifact_id,original_name,stored_path,extension,size_bytes,sha256,status,
                          created_at,updated_at,last_accessed_at,source_system,title,version_group,
                          version,is_current,retention_days,delete_after,pinned,local_available,
                          knowledge_sync_status,metadata_json,status_before_trash,is_current_before_trash
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            artifact_id, sanitize_filename(original_name), str(path), extension,
                            path.stat().st_size, digest, status, created, now, now,
                            "artifact-reconciliation", Path(original_name).stem,
                            normalize_version_group(original_name), 1, int(status != "trash"),
                            retention, self._delete_after(retention), 0, 1, "pending",
                            json_dumps({"recovered_orphan": True}),
                            "draft" if status == "trash" else None,
                            1 if status == "trash" else None,
                        ),
                    )
                    self._event(conn, artifact_id, "reconcile_registered_orphan", {"path": str(path)})
                    orphan_registered.append(artifact_id)

            for artifact_id, row in rows.items():
                exists = Path(row["stored_path"]).is_file() or artifact_id in actual_by_id
                if row["local_available"] and not exists:
                    conn.execute(
                        "UPDATE artifacts SET local_available=0,updated_at=? WHERE artifact_id=?",
                        (now, artifact_id),
                    )
                    self._event(conn, artifact_id, "reconcile_missing_bytes")
                    missing.append(artifact_id)
                elif not row["local_available"] and exists and artifact_id not in recovered:
                    conn.execute(
                        "UPDATE artifacts SET local_available=1,updated_at=? WHERE artifact_id=?",
                        (now, artifact_id),
                    )
                    self._event(conn, artifact_id, "reconcile_recovered_bytes")
                    recovered.append(artifact_id)

            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
            for path in self.staging_dir.rglob("*"):
                if not path.is_file():
                    continue
                modified = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
                if modified < cutoff:
                    path.unlink(missing_ok=True)
                    stale_staging_removed.append(str(path))
            conn.commit()
        return {
            "missing_marked": missing,
            "recovered_marked": sorted(set(recovered)),
            "orphan_registered": orphan_registered,
            "duplicate_orphans_removed": duplicate_orphans_removed,
            "stale_staging_removed": stale_staging_removed,
        }


def capture_outbound_artifact(
    file_path: str | Path,
    *,
    source_system: str = "hermes",
    source_workspace: str | None = None,
    source_chat: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    knowledge_project: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Best-effort capture used by delivery paths.

    Delivery must never fail because archival failed. Callers should log the
    returned error and continue sending the original file.
    """
    if not artifact_storage_enabled():
        return {"status": "disabled", "saved": False}
    try:
        return ArtifactStore().capture(
            file_path,
            status="final",
            source_system=source_system,
            source_workspace=source_workspace,
            source_chat=source_chat,
            title=title,
            summary=summary,
            knowledge_project=knowledge_project,
            metadata=metadata,
            require_enabled=True,
        )
    except UnsupportedArtifact as exc:
        return {"status": "skipped", "saved": False, "reason": str(exc)}
    except Exception as exc:
        return {"status": "error", "saved": False, "error": f"{type(exc).__name__}: {exc}"}
