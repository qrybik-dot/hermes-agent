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

from gateway.messaging_contract import public_response_text, public_verdict

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
        return public_verdict(match.group(1), has_usable_result=True)
    if completed is True:
        return "READY"
    return "PARTIAL"


def _status_class(status: str) -> str:
    return {
        "READY": "ready",
        "PARTIAL": "partial",
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

    body = _paragraphize(public_response_text(final_response))
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
<html lang="ru" data-theme="dark" data-report-theme-contract="v2">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark light">
  <title>{safe_title}</title>
  <style>
    /* report-theme:dark */
    :root {{
      color-scheme: dark;
      --report-bg: #08131f; --report-surface: #0f2033; --report-surface-raised: #14283d; --report-surface-soft: #0b1928;
      --report-text: #e8f0f7; --report-muted: #a7b7c7; --report-border: #2d4358; --report-border-strong: #58738d;
      --report-link: #9fc7ed; --report-code-bg: #07121d; --report-code-text: #d5e2ee; --report-code-border: #38516a;
      --report-success-bg: #123325; --report-success-text: #8bc9a8; --report-success-border: #3f765a;
      --report-warning-bg: #352b18; --report-warning-text: #e4c17c; --report-warning-border: #7d6535;
      --report-danger-bg: #381f25; --report-danger-text: #e6a0a7; --report-danger-border: #81454e;
      --report-info-bg: #142d45; --report-info-text: #9cc2e6; --report-info-border: #456b8c;
    }}
    /* report-theme:light */
    html:has(#theme-toggle:checked) {{
      color-scheme: light;
      --report-bg: #edf2f6; --report-surface: #f8fafc; --report-surface-raised: #f1f5f8; --report-surface-soft: #e9f0f5;
      --report-text: #172a3d; --report-muted: #4d6478; --report-border: #b8c6d2; --report-border-strong: #6f879c;
      --report-link: #245f8e; --report-code-bg: #e5edf3; --report-code-text: #18344f; --report-code-border: #8099ad;
      --report-success-bg: #e7f3ec; --report-success-text: #226746; --report-success-border: #6d9b7f;
      --report-warning-bg: #f7efdc; --report-warning-text: #765516; --report-warning-border: #a58a50;
      --report-danger-bg: #f8e9eb; --report-danger-text: #8b3542; --report-danger-border: #aa7580;
      --report-info-bg: #e7f1f8; --report-info-text: #245f8e; --report-info-border: #789bb8;
    }}
    @supports (color: oklch(from red l c h)) {{
      :root {{
        --report-bg: oklch(from #08131f l c h); --report-surface: oklch(from #0f2033 l c h);
        --report-text: oklch(from #e8f0f7 l c h); --report-muted: oklch(from #a7b7c7 l c h); --report-link: oklch(from #9fc7ed l c h);
      }}
      html:has(#theme-toggle:checked) {{
        --report-bg: oklch(from #edf2f6 l c h); --report-surface: oklch(from #f8fafc l c h);
        --report-text: oklch(from #172a3d l c h); --report-muted: oklch(from #4d6478 l c h); --report-link: oklch(from #245f8e l c h);
      }}
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; background: var(--report-bg); }}
    body {{ margin: 0; font: 16px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--report-bg); color: var(--report-text); }}
    a {{ color: var(--report-link); text-underline-offset: .15em; }}
    :where(a, summary, input):focus-visible {{ outline: 3px solid var(--report-warning-text); outline-offset: 3px; }}
    main {{ width: min(960px, calc(100vw - 32px)); margin: 20px auto 36px; }}
    .report-toolbar {{ display: flex; justify-content: flex-end; min-height: 44px; margin-bottom: 10px; }}
    .theme-control {{ display: none; align-items: center; gap: 9px; min-height: 44px; padding: 7px 12px; border: 1px solid var(--report-border-strong); border-radius: 999px; background: var(--report-surface); color: var(--report-text); cursor: pointer; font-size: .82rem; font-weight: 700; }}
    .theme-control input {{ width: 1.1rem; height: 1.1rem; margin: 0; accent-color: var(--report-info-text); }}
    @supports selector(html:has(*)) {{ .theme-control {{ display: inline-flex; }} }}
    .hero, .card {{ min-width: 0; max-width: 100%; background: var(--report-surface); border: 1px solid var(--report-border); border-radius: 18px; }}
    .hero {{ padding: 28px; display: grid; gap: 14px; }}
    .eyebrow {{ color: var(--report-muted); text-transform: uppercase; letter-spacing: .09em; font-size: 12px; font-weight: 700; }}
    h1 {{ margin: 0; font-size: 2.35rem; line-height: 1.08; text-wrap: balance; overflow-wrap: anywhere; }}
    .status {{ display: inline-flex; align-items: center; gap: 8px; width: fit-content; max-width: 100%; border: 1px solid; border-radius: 999px; padding: 7px 11px; font-weight: 800; letter-spacing: .02em; overflow-wrap: anywhere; }}
    .status::before {{ content: ""; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }}
    .status.ready {{ color: var(--report-success-text); background: var(--report-success-bg); border-color: var(--report-success-border); }}
    .status.partial {{ color: var(--report-warning-text); background: var(--report-warning-bg); border-color: var(--report-warning-border); }}
    .status.blocked {{ color: var(--report-danger-text); background: var(--report-danger-bg); border-color: var(--report-danger-border); }}
    .grid {{ display: grid; grid-template-columns: 1fr 320px; gap: 20px; margin-top: 20px; }}
    .card {{ padding: 24px; overflow: hidden; }}
    h2, h3, h4 {{ margin: 1.2em 0 .45em; line-height: 1.15; overflow-wrap: anywhere; }}
    p {{ max-width: 72ch; margin: .7em 0; text-wrap: pretty; overflow-wrap: anywhere; }} ul {{ padding-left: 1.2rem; }} li {{ margin: .3em 0; overflow-wrap: anywhere; }}
    code {{ color: var(--report-code-text); background: var(--report-code-bg); border: 1px solid var(--report-code-border); border-radius: 5px; padding: .16em .42em; overflow-wrap: anywhere; }}
    pre {{ overflow: auto; border: 1px solid var(--report-code-border); border-radius: 12px; padding: 14px; background: var(--report-code-bg); color: var(--report-code-text); }}
    pre code {{ border: 0; padding: 0; }}
    details {{ margin-top: 18px; border-top: 1px solid var(--report-border); padding-top: 14px; }} summary {{ cursor: pointer; font-weight: 700; }}
    dl {{ display: grid; grid-template-columns: minmax(92px, auto) 1fr; gap: 10px 14px; margin: 0; }}
    dt {{ color: var(--report-muted); }} dd {{ margin: 0; word-break: break-word; }}
    .muted {{ color: var(--report-muted); }}
    @media (max-width: 760px) {{ main {{ width: min(100vw - 20px, 430px); margin: 10px auto; }} .grid {{ grid-template-columns: 1fr; }} .hero, .card {{ border-radius: 14px; }} dl {{ grid-template-columns: 1fr; gap: 3px; }} }}
    @media (max-width: 420px) {{ h1 {{ font-size: 1.85rem; }} .hero, .card {{ padding: 18px; }} .status {{ border-radius: 12px; }} }}
    @media (prefers-reduced-motion: reduce) {{ html {{ scroll-behavior: auto; }} }}
    @media (prefers-contrast: more) {{ :root {{ --report-border: var(--report-border-strong); }} }}
    @media (forced-colors: active) {{ .status, .theme-control, .hero, .card {{ border: 1px solid CanvasText; }} }}
    @media print {{
      @page {{ size: A4; margin: 12mm; }}
      /* report-theme:print */
      html:root, html:root:has(#theme-toggle:checked) {{
        color-scheme: light;
        --report-bg: #edf2f6; --report-surface: #f8fafc; --report-surface-raised: #f1f5f8; --report-surface-soft: #e9f0f5;
        --report-text: #172a3d; --report-muted: #4d6478; --report-border: #b8c6d2; --report-border-strong: #6f879c;
        --report-link: #245f8e; --report-code-bg: #e5edf3; --report-code-text: #18344f; --report-code-border: #8099ad;
        --report-success-bg: #e7f3ec; --report-success-text: #226746; --report-success-border: #6d9b7f;
        --report-warning-bg: #f7efdc; --report-warning-text: #765516; --report-warning-border: #a58a50;
        --report-danger-bg: #f8e9eb; --report-danger-text: #8b3542; --report-danger-border: #aa7580;
        --report-info-bg: #e7f1f8; --report-info-text: #245f8e; --report-info-border: #789bb8;
      }}
      html, body {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      main {{ width: 100%; margin: 0; }} .report-toolbar {{ display: none; }}
      .hero {{ break-inside: avoid; }} .card {{ break-inside: auto; }} h2, h3, h4 {{ break-after: avoid; }}
    }}
  </style>
</head>
<body><!-- Hermes Report Finalizer -->
  <main>
    <div class="report-toolbar" aria-label="Настройки отчёта">
      <label class="theme-control" for="theme-toggle"><input id="theme-toggle" type="checkbox">Светлая тема</label>
    </div>
    <section class="hero" aria-label="Task report summary">
      <div class="eyebrow">Hermes Report Finalizer</div>
      <h1>{safe_title}</h1>
      <div class="status {klass}" data-status="{status}">{status}</div>
    </section>
    <section class="grid">
      <article class="card" aria-label="Final response">
        {body}
      </article>
      <aside class="card" aria-label="Run metadata">
        <h2>Метаданные</h2>
        <dl>{meta_rows}</dl>
      </aside>
    </section>
  </main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
    return TaskReportArtifact(path=path, status=status, report_id=report_id)


__all__ = ["TaskReportArtifact", "render_task_report_html"]
