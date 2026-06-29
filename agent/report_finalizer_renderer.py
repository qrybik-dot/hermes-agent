"""Deterministic HTML renderer for post-task reports.

The renderer is intentionally self-contained: no external assets, no scripts, no
network dependencies. It produces a standalone artifact that can be opened by a
local browser/Playwright for visual smoke tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import re
import uuid

_STATUS_RE = re.compile(r"(?im)^\s*(?:Статус|Status|Итог|Verdict)?\s*:?[\s#>*-]*(READY|PARTIAL|BLOCKED|INCOMPLETE)\b")
_SECTION_RE = re.compile(r"(?m)^#{1,3}\s+(.+?)\s*$|^(Итог|Сделано|Проверено|Файлы|Риски|Следующий шаг)\s*:\s*$")


@dataclass(frozen=True)
class TaskReportArtifact:
    """Rendered task report artifact metadata."""

    path: Path
    status: str
    report_id: str


def _detect_status(text: str, *, completed: bool | None = None, failed: bool | None = None) -> str:
    if failed:
        return "BLOCKED"
    match = _STATUS_RE.search(text or "")
    if match:
        label = match.group(1).upper()
        return label
    if completed is True:
        return "READY"
    return "PARTIAL"


def _status_class(status: str) -> str:
    return {
        "READY": "ready",
        "PARTIAL": "partial",
        "INCOMPLETE": "partial",
        "BLOCKED": "blocked",
    }.get(status, "partial")


def _paragraphize(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").strip().splitlines()]
    if not lines:
        return "<p class=\"muted\">Нет данных.</p>"

    html: list[str] = []
    in_list = False
    in_code = False
    code_lines: list[str] = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html.append("</ul>")
            in_list = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            close_list()
            if in_code:
                html.append("<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            close_list()
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            close_list()
            level = min(len(heading.group(1)) + 1, 4)
            html.append(f"<h{level}>" + escape(heading.group(2)) + f"</h{level}>")
            continue
        item = re.match(r"^[-*]\s+(.+)$", stripped)
        if item:
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append("<li>" + escape(item.group(1)) + "</li>")
            continue
        close_list()
        html.append("<p>" + escape(stripped) + "</p>")

    close_list()
    if in_code:
        html.append("<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
    return "\n".join(html)


def render_task_report_html(
    *,
    final_response: str,
    output_dir: str | Path | None = None,
    title: str = "Hermes Task Report",
    completed: bool | None = None,
    failed: bool | None = None,
    session_id: str | None = None,
    turn_exit_reason: str | None = None,
    model: str | None = None,
) -> TaskReportArtifact:
    """Render a standalone production HTML task report and return its path."""

    status = _detect_status(final_response, completed=completed, failed=failed)
    report_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    out_dir = Path(output_dir or Path.home() / ".hermes" / "reports" / "task-finalizer")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"task-report-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{report_id}.html"

    body = _paragraphize(final_response)
    klass = _status_class(status)
    safe_title = escape(title)
    meta_rows = "".join(
        f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>"
        for k, v in (
            ("Status", status),
            ("Generated", now),
            ("Session", session_id or "not recorded"),
            ("Exit reason", turn_exit_reason or "not recorded"),
            ("Model", model or "not recorded"),
            ("Report ID", report_id),
        )
    )
    html = f"""<!doctype html>
<html lang="ru" data-theme="auto">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{safe_title}</title>
  <style>
    :root {{
      --bg: #f7f8fb; --panel: #ffffff; --text: #14171f; --muted: #647084;
      --line: #dce2ec; --accent: #476cff; --ready: #0f8f5f; --partial: #b57600; --blocked: #c8353e;
      --shadow: 0 18px 48px rgba(20, 23, 31, .10);
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{ --bg: #0d1117; --panel: #151b23; --text: #eef2f8; --muted: #9aa7b8; --line: #2b3442; --accent: #8ea2ff; --shadow: 0 18px 48px rgba(0,0,0,.35); }}
    }}
    html[data-theme="light"] {{ --bg: #f7f8fb; --panel: #ffffff; --text: #14171f; --muted: #647084; --line: #dce2ec; --accent: #476cff; --shadow: 0 18px 48px rgba(20, 23, 31, .10); }}
    html[data-theme="dark"] {{ --bg: #0d1117; --panel: #151b23; --text: #eef2f8; --muted: #9aa7b8; --line: #2b3442; --accent: #8ea2ff; --shadow: 0 18px 48px rgba(0,0,0,.35); }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 16px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: radial-gradient(circle at top left, color-mix(in srgb, var(--accent) 16%, transparent), transparent 34rem), var(--bg); color: var(--text); }}
    main {{ width: min(1120px, calc(100vw - 32px)); margin: 32px auto; }}
    .hero, .card {{ background: color-mix(in srgb, var(--panel) 96%, transparent); border: 1px solid var(--line); border-radius: 24px; box-shadow: var(--shadow); }}
    .hero {{ padding: clamp(20px, 4vw, 40px); display: grid; gap: 16px; }}
    .eyebrow {{ color: var(--muted); text-transform: uppercase; letter-spacing: .12em; font-size: 12px; font-weight: 700; }}
    h1 {{ margin: 0; font-size: clamp(30px, 6vw, 58px); line-height: 1; }}
    .status {{ display: inline-flex; align-items: center; width: fit-content; border-radius: 999px; padding: 8px 14px; color: white; font-weight: 800; letter-spacing: .04em; }}
    .status.ready {{ background: var(--ready); }} .status.partial {{ background: var(--partial); }} .status.blocked {{ background: var(--blocked); }}
    .grid {{ display: grid; grid-template-columns: 1fr 320px; gap: 20px; margin-top: 20px; }}
    .card {{ padding: clamp(18px, 3vw, 28px); overflow: hidden; }}
    h2, h3, h4 {{ margin: 1.2em 0 .45em; line-height: 1.15; }}
    p {{ margin: .7em 0; }} ul {{ padding-left: 1.2rem; }} li {{ margin: .3em 0; }}
    pre {{ overflow: auto; border: 1px solid var(--line); border-radius: 16px; padding: 14px; background: color-mix(in srgb, var(--bg) 74%, var(--panel)); }}
    details {{ margin-top: 18px; border-top: 1px solid var(--line); padding-top: 14px; }} summary {{ cursor: pointer; font-weight: 700; }}
    dl {{ display: grid; grid-template-columns: minmax(92px, auto) 1fr; gap: 10px 14px; margin: 0; }}
    dt {{ color: var(--muted); }} dd {{ margin: 0; word-break: break-word; }}
    .muted {{ color: var(--muted); }}
    @media (max-width: 760px) {{ main {{ width: min(100vw - 20px, 430px); margin: 10px auto; }} .grid {{ grid-template-columns: 1fr; }} .hero, .card {{ border-radius: 18px; }} dl {{ grid-template-columns: 1fr; gap: 3px; }} }}
  </style>
</head>
<body>
  <main>
    <section class="hero" aria-label="Task report summary">
      <div class="eyebrow">Hermes Report Finalizer</div>
      <h1>{safe_title}</h1>
      <div class="status {klass}" data-status="{status}">{status}</div>
    </section>
    <section class="grid">
      <article class="card" aria-label="Final response">
        <h2>Краткий результат</h2>
        {body}
        <h2>Что сделано</h2>
        <p class="muted">См. подтверждённые пункты в финальном ответе выше.</p>
        <h2>Что не сделано</h2>
        <p class="muted">Если статус не READY, незавершённая часть указана в финальном ответе.</p>
        <h2>Проверки и доказательства</h2>
        <p class="muted">Доказательства сохранены в тексте отчёта и метаданных.</p>
        <h2>Риски</h2>
        <p class="muted">Актуальные риски указаны в финальном ответе.</p>
        <h2>Следующий шаг</h2>
        <p class="muted">Следующее действие указано в финальном ответе.</p>
      </article>
      <aside class="card" aria-label="Run metadata">
        <h2>Метаданные</h2>
        <dl>{meta_rows}</dl>
        <details>
          <summary>Appendix: technical details</summary>
          <p class="muted">Полный prompt и runlog не включаются в основной поток отчёта.</p>
        </details>
      </aside>
    </section>
  </main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
    return TaskReportArtifact(path=path, status=status, report_id=report_id)


__all__ = ["TaskReportArtifact", "render_task_report_html"]
