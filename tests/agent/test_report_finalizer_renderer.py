from pathlib import Path

from agent.report_finalizer_renderer import render_task_report_html
from scripts.validate_html_report import validate_html_report


def test_report_finalizer_renderer_writes_theme_aware_html(tmp_path):
    artifact = render_task_report_html(
        final_response=(
            "Статус: READY\n\n"
            "## Итог\nГотово.\n\n"
            "## Проверено\n- pytest\n- browser smoke"
        ),
        output_dir=tmp_path,
        session_id="session-1",
        turn_exit_reason="completed",
        model="test-model",
    )

    assert artifact.status == "READY"
    assert artifact.path.exists()
    html = artifact.path.read_text(encoding="utf-8")
    assert "Hermes Report Finalizer" in html
    assert "prefers-color-scheme" in html
    assert 'data-theme="dark"' in html
    assert "@media (max-width" in html
    assert "Что не сделано" not in html
    assert "max-width: 960px" not in html  # renderer uses the equivalent width:min() rule
    assert "min(960px" in html
    assert "gradient(" not in html
    assert "Appendix: technical details" not in html
    assert validate_html_report(artifact.path) == []


def test_report_finalizer_renderer_escapes_user_content(tmp_path):
    artifact = render_task_report_html(
        final_response="Статус: PARTIAL\n<script>alert(1)</script>\n[bad](https://example.com)",
        output_dir=tmp_path,
    )
    html = artifact.path.read_text(encoding="utf-8")
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html
    assert validate_html_report(artifact.path) == []


def test_report_finalizer_renderer_maps_incomplete_to_partial(tmp_path):
    artifact = render_task_report_html(
        final_response="INCOMPLETE\nЛимит шагов исчерпан",
        output_dir=tmp_path,
    )
    html = artifact.path.read_text(encoding="utf-8")
    assert artifact.status == "PARTIAL"
    assert 'data-status="PARTIAL"' in html
    assert "INCOMPLETE" not in html
    assert validate_html_report(artifact.path) == []


def test_html_report_validator_rejects_unsafe_external_assets(tmp_path):
    bad = Path(tmp_path) / "bad.html"
    bad.write_text(
        "<!doctype html><html lang=\"ru\"><head><meta name=\"viewport\" content=\"width=device-width\">"
        "<style>@media (max-width: 760px){} @media (prefers-color-scheme: dark){} "
        "html[data-theme=\"dark\"]{} html[data-theme=\"light\"]{}</style></head>"
        "<body><div data-status=\"READY\">Hermes Report Finalizer READY</div>"
        "<img src=\"https://example.com/a.png\"></body></html>",
        encoding="utf-8",
    )
    errors = validate_html_report(bad)
    assert any("external URL" in error for error in errors)
