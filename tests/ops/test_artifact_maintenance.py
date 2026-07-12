import datetime as dt
import importlib.util
from pathlib import Path

from hermes_cli.artifact_store import ArtifactStore, Limits


MODULE_PATH = Path(__file__).resolve().parents[2] / "ops" / "artifacts" / "maintenance.py"
spec = importlib.util.spec_from_file_location("artifact_maintenance", MODULE_PATH)
MAINT = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(MAINT)


def test_threshold_alert_fires_once_and_clears_after_safe_cleanup(tmp_path):
    store = ArtifactStore(
        tmp_path / "store",
        Limits(hard=1_000, warning=700, critical=800, emergency=900),
    )
    source = tmp_path / "temporary.md"
    source.write_bytes(b"x" * 750)
    store.capture(source, status="temporary", retention_days=0)

    first = MAINT.update_alerts(store, dry_run=True)
    second = MAINT.update_alerts(store, dry_run=True)

    assert first["crossed"] == ["warning"]
    assert len(first["delivery"]) == 1
    assert second["crossed"] == []
    assert second["delivery"] == []

    store.cleanup(
        dry_run=False,
        now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1),
    )
    cleared = MAINT.update_alerts(store, dry_run=True)

    assert "warning" in cleared["cleared"]
    assert len(cleared["delivery"]) == 1
