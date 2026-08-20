#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9119
MAX_BODY_BYTES = 4096
TASK_RE = re.compile(r"^[A-Za-z0-9_.-]{8,96}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{43,256}$")
VALUE_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
TOTP_RE = re.compile(r"^\d{6}$")


class BridgeError(Exception):
    pass


def default_root() -> Path:
    run_user = Path("/run/user") / str(os.getuid())
    if run_user.exists() and os.access(run_user, os.W_OK):
        return run_user / "mosreg-gateway-totp"
    shm = Path("/dev/shm")
    if shm.exists() and os.access(shm, os.W_OK):
        return shm / f"mosreg-gateway-totp-{os.getuid()}"
    return Path(tempfile.gettempdir()) / f"mosreg-gateway-totp-{os.getuid()}"


def now_ts() -> int:
    return int(time.time())


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def validate_task_id(task_id: str) -> str:
    task = str(task_id or "")
    if not TASK_RE.fullmatch(task):
        raise BridgeError("invalid")
    return task


def validate_secret(secret: str) -> str:
    value = str(secret or "")
    if not SECRET_RE.fullmatch(value):
        raise BridgeError("invalid")
    return value


def safe_response(ok: bool, status: str = "ok", **extra: Any) -> dict[str, Any]:
    data = {"ok": ok, "status": status}
    data.update(extra)
    return data


class TotpBridgeStore:
    def __init__(self, root: Path | None = None):
        self.root = root or default_root()
        self.sessions = self.root / "sessions"
        self.values = self.root / "values"

    def setup(self) -> None:
        for path in (self.root, self.sessions, self.values):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)

    def _session_path(self, task_id: str) -> Path:
        return self.sessions / f"{validate_task_id(task_id)}.json"

    def _value_path(self, task_id: str) -> Path:
        return self.values / f"{validate_task_id(task_id)}.json"

    @staticmethod
    def _write_private_atomic(path: Path, payload: dict[str, Any], *, replace: bool = True) -> bool:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(tmp, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
                fh.write("\n")
            os.chmod(tmp, 0o600)
            if replace:
                os.replace(tmp, path)
                os.chmod(path, 0o600)
                return True
            try:
                os.link(tmp, path)
                os.chmod(path, 0o600)
                return True
            except FileExistsError:
                return False
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def register_session(self, data: dict[str, Any], now: int | None = None) -> dict[str, Any]:
        self.setup()
        now = now_ts() if now is None else now
        task_id = validate_task_id(data.get("task_id", ""))
        session_secret = validate_secret(data.get("session_secret", ""))
        challenge = data.get("challenge") if isinstance(data.get("challenge"), dict) else {}
        expires_at = int(challenge.get("expires_at", now + 90))
        ttl = max(0, min(90, expires_at - now))
        payload = {
            "task_id": task_id,
            "session_secret_hash": secret_hash(session_secret),
            "telegram_user_id": str(challenge.get("telegram_user_id", "")),
            "chat_id": str(challenge.get("chat_id", "")),
            "reply_to_message_id": str(challenge.get("reply_to_message_id", "")),
            "created_at": now,
            "expires_at": now + ttl,
        }
        self._write_private_atomic(self._session_path(task_id), payload, replace=True)
        return safe_response(True, "registered", ttl=ttl)

    def delete_session(self, data: dict[str, Any]) -> dict[str, Any]:
        task_id = str(data.get("task_id", ""))
        session_secret = str(data.get("session_secret", ""))
        if not self._authorized(task_id, session_secret, now_ts()):
            return safe_response(True, "deleted")
        for path in (self._session_path(task_id), self._value_path(task_id)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return safe_response(True, "deleted")

    def _load_session(self, task_id: str, now: int) -> dict[str, Any] | None:
        try:
            data = json.loads(self._session_path(task_id).read_text(encoding="utf-8"))
        except Exception:
            return None
        if now > int(data.get("expires_at", 0)):
            for path in (self._session_path(task_id), self._value_path(task_id)):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            return None
        return data if isinstance(data, dict) else None

    def _authorized(self, task_id: str, session_secret: str, now: int) -> bool:
        try:
            task_id = validate_task_id(task_id)
            session_secret = validate_secret(session_secret)
        except BridgeError:
            return False
        session = self._load_session(task_id, now)
        if not session:
            return False
        return hmac.compare_digest(str(session.get("session_secret_hash", "")), secret_hash(session_secret))

    def put_value(self, data: dict[str, Any], now: int | None = None) -> dict[str, Any]:
        self.setup()
        now = now_ts() if now is None else now
        task_id = str(data.get("task_id", ""))
        try:
            task_id = validate_task_id(task_id)
        except BridgeError:
            return safe_response(False, "rejected")
        session = self._load_session(task_id, now)
        if not session:
            return safe_response(False, "rejected")
        if str(data.get("telegram_user_id", "")) != str(session.get("telegram_user_id", "")):
            return safe_response(False, "rejected")
        if str(data.get("chat_id", "")) != str(session.get("chat_id", "")):
            return safe_response(False, "rejected")
        if str(data.get("reply_to_message_id", "")) != str(session.get("reply_to_message_id", "")):
            return safe_response(False, "rejected")
        value = str(data.get("value", ""))
        value_kind = str(data.get("value_kind", "totp"))
        if value_kind == "totp" and not TOTP_RE.fullmatch(value):
            return safe_response(False, "rejected")
        if value_kind != "totp" and (not VALUE_RE.fullmatch(value) or TOTP_RE.fullmatch(value)):
            return safe_response(False, "rejected")
        expires_at = min(int(session.get("expires_at", now)), now + 90)
        payload = {"task_id": task_id, "value": value, "value_kind": value_kind, "created_at": now, "expires_at": expires_at}
        stored = self._write_private_atomic(self._value_path(task_id), payload, replace=False)
        return safe_response(stored, "stored" if stored else "rejected")

    def put_reply_value(self, data: dict[str, Any], now: int | None = None) -> dict[str, Any]:
        self.setup()
        now = now_ts() if now is None else now
        user_id = str(data.get("telegram_user_id", ""))
        chat_id = str(data.get("chat_id", ""))
        reply_to = str(data.get("reply_to_message_id", ""))
        matched: dict[str, Any] | None = None
        active_for_chat = False
        for path in self.sessions.glob("*.json"):
            try:
                session = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if now > int(session.get("expires_at", 0)):
                task_id = str(session.get("task_id", ""))
                for stale in (path, self.values / f"{task_id}.json"):
                    try:
                        stale.unlink()
                    except (FileNotFoundError, OSError):
                        pass
                continue
            same_actor = (
                str(session.get("telegram_user_id", "")) == user_id
                and str(session.get("chat_id", "")) == chat_id
            )
            if same_actor:
                active_for_chat = True
                if str(session.get("reply_to_message_id", "")) == reply_to:
                    matched = session
                    break
        if not matched:
            return safe_response(False, "wrong_reply_target" if active_for_chat else "no_active_challenge")
        item = dict(data)
        task_id = str(matched.get("task_id", ""))
        item["task_id"] = task_id
        result = self.put_value(item, now=now)
        if result.get("ok") is not True and self._value_path(task_id).exists():
            return safe_response(False, "already_submitted")
        return result

    def pop_value(self, data: dict[str, Any], now: int | None = None) -> dict[str, Any]:
        now = now_ts() if now is None else now
        task_id = str(data.get("task_id", ""))
        session_secret = str(data.get("session_secret", ""))
        if not self._authorized(task_id, session_secret, now):
            return safe_response(False, "empty")
        path = self._value_path(task_id)
        claimed = path.with_name(f".{path.name}.{os.getpid()}.pop")
        try:
            os.replace(path, claimed)
        except FileNotFoundError:
            return safe_response(False, "empty")
        try:
            item = json.loads(claimed.read_text(encoding="utf-8"))
        finally:
            try:
                claimed.unlink()
            except FileNotFoundError:
                pass
        if now > int(item.get("expires_at", 0)):
            return safe_response(False, "empty")
        value = item.get("value")
        if not isinstance(value, str) or not VALUE_RE.fullmatch(value):
            return safe_response(False, "empty")
        # A successful pop is the single consumption point for this challenge.
        # Close the session immediately so a late/repeated Telegram Reply cannot
        # be reported as accepted after the browser worker already consumed it.
        try:
            self._session_path(task_id).unlink()
        except FileNotFoundError:
            pass
        return safe_response(True, "popped", value=value, value_kind=item.get("value_kind", "totp"))

    def health(self) -> dict[str, Any]:
        self.setup()
        return {"ok": True, "service": "mosreg_totp_bridge", "root_mode": oct(self.root.stat().st_mode & 0o777)}


class BridgeHandler(BaseHTTPRequestHandler):
    store: TotpBridgeStore

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > MAX_BODY_BYTES:
            raise BridgeError("invalid")
        parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(parsed, dict):
            raise BridgeError("invalid")
        return parsed

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, self.store.health())
            return
        self._json(404, safe_response(False, "not_found"))

    def do_POST(self) -> None:
        try:
            data = self._read_body()
            if self.path == "/v1/session/register":
                self._json(200, self.store.register_session(data))
            elif self.path == "/v1/session/delete":
                self._json(200, self.store.delete_session(data))
            elif self.path == "/v1/totp/put":
                self._json(200, self.store.put_value(data))
            elif self.path == "/v1/totp/pop":
                self._json(200, self.store.pop_value(data))
            else:
                self._json(404, safe_response(False, "not_found"))
        except Exception:
            self._json(200, safe_response(False, "rejected"))




import logging
import threading

logger = logging.getLogger(__name__)


class MosregTotpBridgeServer:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, root: Path | None = None):
        self.host = host
        self.port = port
        self.root = root
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self._is_running = False

    def start(self) -> bool:
        if self.host not in {"127.0.0.1", "::1"}:
            logger.error("[MosregTOTPBridge] Refusing to start on non-loopback host: %s", self.host)
            return False
        try:
            handler = BridgeHandler
            handler.store = TotpBridgeStore(self.root)
            handler.store.setup()
            self.server = ThreadingHTTPServer((self.host, self.port), handler)
        except Exception as exc:
            logger.error(
                "[MosregTOTPBridge] Failed to bind HTTP server on %s:%d: %s.",
                self.host,
                self.port,
                exc,
            )
            self.server = None
            return False

        self._is_running = True
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
            name="mosreg-totp-bridge",
        )
        self.thread.start()
        logger.info(
            "[MosregTOTPBridge] Successfully started TOTP bridge server on http://%s:%d",
            self.host,
            self.port,
        )
        return True

    def stop(self) -> None:
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception as exc:
                logger.warning("[MosregTOTPBridge] Error during bridge shutdown: %s", exc)
            finally:
                self.server = None
        self._is_running = False


_global_bridge_server: MosregTotpBridgeServer | None = None


def start_global_bridge(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, root: Path | None = None) -> bool:
    global _global_bridge_server
    if _global_bridge_server is not None and _global_bridge_server._is_running:
        return True
    server = MosregTotpBridgeServer(host, port, root)
    if server.start():
        _global_bridge_server = server
        return True
    return False


def stop_global_bridge() -> None:
    global _global_bridge_server
    if _global_bridge_server is not None:
        _global_bridge_server.stop()
        _global_bridge_server = None


def serve(host: str, port: int, root: Path | None = None) -> None:
    BridgeHandler.store = TotpBridgeStore(root)
    server = ThreadingHTTPServer((host, port), BridgeHandler)
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Mosreg local-forward TOTP bridge")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1"}:
        raise SystemExit("loopback_only")
    serve(args.host, args.port, args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
