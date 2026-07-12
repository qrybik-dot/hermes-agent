import datetime as dt
from pathlib import Path

import pytest

from hermes_cli.artifact_store import (
    ArtifactStore,
    Limits,
    StorageLimitExceeded,
    UnsupportedArtifact,
)


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(
        tmp_path / "artifacts",
        Limits(hard=10_000, warning=7_000, critical=8_000, emergency=9_000),
    )


def make_file(tmp_path: Path, name: str, data: bytes = b"document") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_capture_deduplicates_exact_bytes(store, tmp_path):
    source = make_file(tmp_path, "report.docx", b"PK-docx")
    first = store.capture(source, source_workspace="chat-a")
    second = store.capture(source, source_workspace="chat-b")

    assert first["saved"] is True
    assert second["saved"] is False
    assert second["deduplicated"] is True
    assert second["artifact_id"] == first["artifact_id"]
    assert store.health()["artifact_count"] == 1


def test_explicit_version_group_creates_chain(store, tmp_path):
    source = make_file(tmp_path, "screening.docx", b"version-one")
    first = store.capture(source, source_workspace="recruitment", version_group="candidate-screening")
    source.write_bytes(b"version-two")
    second = store.capture(source, source_workspace="recruitment", version_group="candidate-screening")

    assert second["version"] == 2
    assert second["supersedes"] == first["artifact_id"]
    old = store.get(first["artifact_id"])
    assert old["status"] == "superseded"
    assert old["is_current"] is False
    assert store.versions(second["artifact_id"])[0]["artifact_id"] == second["artifact_id"]


def test_derived_group_does_not_merge_across_workspaces(store, tmp_path):
    first_file = make_file(tmp_path, "report_final.docx", b"one")
    first = store.capture(first_file, source_workspace="workspace-a")
    second_file = make_file(tmp_path, "report_updated.docx", b"two")
    second = store.capture(second_file, source_workspace="workspace-b")

    assert first["version"] == 1
    assert second["version"] == 1
    assert second["supersedes"] is None


def test_images_and_empty_files_are_excluded(store, tmp_path):
    image = make_file(tmp_path, "chart.png", b"png")
    empty = make_file(tmp_path, "empty.pdf", b"")

    with pytest.raises(UnsupportedArtifact, match="images"):
        store.capture(image)
    with pytest.raises(UnsupportedArtifact, match="empty"):
        store.capture(empty)


def test_hard_limit_rejects_without_partial_registry(store, tmp_path):
    first = make_file(tmp_path, "one.pdf", b"a" * 7_000)
    second = make_file(tmp_path, "two.pdf", b"b" * 4_000)
    store.capture(first)

    with pytest.raises(StorageLimitExceeded):
        store.capture(second)

    assert store.health()["artifact_count"] == 1
    assert not any(path.name.endswith("two.pdf") for path in store.files_dir.rglob("*"))


def test_soft_delete_and_restore_preserve_registry(store, tmp_path):
    source = make_file(tmp_path, "brief.xlsx", b"xlsx")
    captured = store.capture(source)
    usage_before = store.health()["usage_bytes"]
    deleted = store.soft_delete(captured["artifact_id"])

    assert deleted["status"] == "trash"
    assert Path(deleted["stored_path"]).is_file()
    assert store.health()["usage_bytes"] == usage_before

    restored = store.restore(captured["artifact_id"])
    assert restored["status"] == "final"
    assert restored["local_available"] is True
    assert Path(restored["stored_path"]).is_file()


def test_recapture_restores_identical_trashed_bytes_instead_of_copying(store, tmp_path):
    source = make_file(tmp_path, "same.pdf", b"same-bytes")
    captured = store.capture(source)
    store.soft_delete(captured["artifact_id"])

    recaptured = store.capture(source)

    assert recaptured["artifact_id"] == captured["artifact_id"]
    assert recaptured["deduplicated"] is True
    assert store.get(captured["artifact_id"])["status"] == "final"
    assert store.health()["artifact_count"] == 1


def test_pinned_artifact_cannot_be_deleted(store, tmp_path):
    source = make_file(tmp_path, "important.md", b"important")
    captured = store.capture(source, pinned=True)

    with pytest.raises(Exception, match="pinned"):
        store.soft_delete(captured["artifact_id"])


def test_cleanup_only_removes_expired_safe_categories(store, tmp_path):
    draft_file = make_file(tmp_path, "draft.md", b"draft")
    final_file = make_file(tmp_path, "final.md", b"final")
    draft = store.capture(draft_file, status="draft", retention_days=1)
    final = store.capture(final_file, status="final", retention_days=1)

    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    preview = store.cleanup(dry_run=True, now=future)
    assert draft["artifact_id"] in preview["artifacts"]
    assert final["artifact_id"] not in preview["artifacts"]

    applied = store.cleanup(dry_run=False, now=future)
    assert draft["artifact_id"] in applied["deleted"]
    assert store.get(draft["artifact_id"])["local_available"] is False
    assert store.get(final["artifact_id"])["local_available"] is True


def test_search_matches_metadata_and_project(store, tmp_path):
    source = make_file(tmp_path, "screening.docx", b"candidate")
    captured = store.capture(
        source,
        title="HR-скрининг Владислава",
        summary="Java Senior для Т-Банка",
        knowledge_project="recruitment",
        metadata={"people": ["Владислав Майсеев"], "tags": ["java"]},
    )

    assert store.search("Майсеев java")[0]["artifact_id"] == captured["artifact_id"]
    assert store.search("recruitment")[0]["artifact_id"] == captured["artifact_id"]


def test_new_version_after_current_was_trashed_keeps_monotonic_version(store, tmp_path):
    source = make_file(tmp_path, "sequence.docx", b"v1")
    first = store.capture(source, source_workspace="same", version_group="sequence")
    store.soft_delete(first["artifact_id"])
    source.write_bytes(b"v2")

    second = store.capture(source, source_workspace="same", version_group="sequence")

    assert second["version"] == 2
    assert second["supersedes"] == first["artifact_id"]
    assert store.get(first["artifact_id"])["status"] == "trash"


def test_restore_old_version_does_not_create_two_current_versions(store, tmp_path):
    source = make_file(tmp_path, "versioned.docx", b"v1")
    first = store.capture(source, source_workspace="same", version_group="versioned")
    source.write_bytes(b"v2")
    second = store.capture(source, source_workspace="same", version_group="versioned")
    store.soft_delete(first["artifact_id"])

    restored = store.restore(first["artifact_id"])

    assert restored["status"] == "superseded"
    assert restored["is_current"] is False
    assert store.get(second["artifact_id"])["is_current"] is True


def test_reconcile_marks_missing_bytes(store, tmp_path):
    source = make_file(tmp_path, "lost.pdf", b"lost")
    captured = store.capture(source)
    Path(captured["stored_path"]).unlink()

    result = store.reconcile()
    assert captured["artifact_id"] in result["missing_marked"]
    assert store.get(captured["artifact_id"])["local_available"] is False


def test_reconcile_registers_completed_orphan_after_crash(store):
    store.ensure_layout()
    orphan_id = "art_1234567890abcdef1234"
    orphan_dir = store.files_dir / "2026" / "07" / "12"
    orphan_dir.mkdir(parents=True)
    orphan = orphan_dir / f"{orphan_id}--recovered.md"
    orphan.write_bytes(b"completed-before-crash")

    result = store.reconcile()
    recovered = store.get(orphan_id)

    assert orphan_id in result["orphan_registered"]
    assert recovered["status"] == "draft"
    assert recovered["metadata"]["recovered_orphan"] is True
    assert recovered["local_available"] is True
