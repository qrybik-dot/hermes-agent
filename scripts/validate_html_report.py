#!/usr/bin/env python3
"""Validate standalone Hermes task report HTML artifacts.

This validator is intentionally conservative: production task reports must be
self-contained, safe to open locally, responsive, and theme-aware.
"""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
import re
import sys


_THEME_MARKER_RE = re.compile(
    r"/\*\s*report-theme:(dark|light|print)\s*\*/\s*[^{}]*\{([^{}]*)\}",
    re.IGNORECASE | re.DOTALL,
)
_TOKEN_RE = re.compile(r"(--report-[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\b")
_REQUIRED_THEME_TOKENS = {
    "--report-bg",
    "--report-surface",
    "--report-text",
    "--report-muted",
    "--report-border-strong",
    "--report-link",
    "--report-code-bg",
    "--report-code-text",
    "--report-success-bg",
    "--report-success-text",
    "--report-warning-bg",
    "--report-warning-text",
    "--report-danger-bg",
    "--report-danger-text",
    "--report-info-bg",
    "--report-info-text",
}
_TEXT_CONTRAST_PAIRS = (
    ("--report-text", "--report-surface", 4.5),
    ("--report-muted", "--report-surface", 4.5),
    ("--report-link", "--report-surface", 4.5),
    ("--report-code-text", "--report-code-bg", 4.5),
    ("--report-success-text", "--report-success-bg", 4.5),
    ("--report-warning-text", "--report-warning-bg", 4.5),
    ("--report-danger-text", "--report-danger-bg", 4.5),
    ("--report-info-text", "--report-info-bg", 4.5),
    ("--report-border-strong", "--report-surface", 3.0),
)


class ReportHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.attrs: list[tuple[str, str, str]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag.lower())
        for name, value in attrs:
            self.attrs.append((tag.lower(), name.lower(), value or ""))

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.text.append(data.strip())


def _relative_luminance(hex_color: str) -> float:
    values = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in values]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first: str, second: str) -> float:
    first_luminance = _relative_luminance(first)
    second_luminance = _relative_luminance(second)
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _validate_v2_theme_contract(html: str, lowered: str) -> list[str]:
    errors: list[str] = []
    if 'data-theme="dark"' not in lowered:
        errors.append("v2 report must default to dark theme")
    if 'id="theme-toggle"' not in lowered or ":has(#theme-toggle:checked)" not in lowered:
        errors.append("v2 report is missing the CSS-only light theme toggle")
    if "@media print" not in lowered:
        errors.append("v2 report is missing a dedicated print theme")
    if "html:root" not in lowered or not re.search(r"print-color-adjust\s*:\s*exact\b", lowered):
        errors.append("v2 print theme must override screen tokens with print-safe specificity")
    if "@page" not in lowered or not re.search(r"size\s*:\s*a4\b", lowered):
        errors.append("v2 print theme must define an A4 page contract")

    theme_blocks = {
        name.lower(): {token: value.lower() for token, value in _TOKEN_RE.findall(body)}
        for name, body in _THEME_MARKER_RE.findall(html)
    }
    for theme_name in ("dark", "light", "print"):
        tokens = theme_blocks.get(theme_name)
        if tokens is None:
            errors.append(f"missing report-theme:{theme_name} token block")
            continue
        missing = sorted(_REQUIRED_THEME_TOKENS - tokens.keys())
        if missing:
            errors.append(f"report-theme:{theme_name} missing tokens: {', '.join(missing)}")
            continue
        for foreground, background, minimum in _TEXT_CONTRAST_PAIRS:
            ratio = _contrast_ratio(tokens[foreground], tokens[background])
            if ratio + 1e-9 < minimum:
                errors.append(
                    f"report-theme:{theme_name} contrast {foreground}/{background} "
                    f"is {ratio:.2f}:1, requires {minimum:.1f}:1"
                )
    return errors


def validate_html_report(path: str | Path) -> list[str]:
    file_path = Path(path)
    errors: list[str] = []
    if not file_path.exists():
        return [f"file not found: {file_path}"]
    html = file_path.read_text(encoding="utf-8")
    parser = ReportHTMLParser()
    parser.feed(html)

    lowered = html.lower()
    if "<!doctype html" not in lowered[:128]:
        errors.append("missing <!doctype html>")
    if "<html" not in lowered or "lang=\"ru\"" not in lowered:
        errors.append("missing Russian html root")
    if "<meta name=\"viewport\"" not in lowered:
        errors.append("missing viewport meta")
    is_v2_theme = 'data-report-theme-contract="v2"' in lowered
    if is_v2_theme:
        errors.extend(_validate_v2_theme_contract(html, lowered))
    elif "prefers-color-scheme" not in lowered or "data-theme=\"dark\"" not in lowered or "data-theme=\"light\"" not in lowered:
        errors.append("missing light/dark theme support")
    if "@media (max-width" not in lowered:
        errors.append("missing mobile responsive media query")
    if "data-status=" not in lowered:
        errors.append("missing status badge")
    if "hermes report finalizer" not in lowered:
        errors.append("missing Report Finalizer marker")
    if "script" in parser.tags:
        errors.append("scripts are not allowed")
    for tag, name, value in parser.attrs:
        if name.startswith("on"):
            errors.append(f"event handler attribute is not allowed: {tag}.{name}")
        if name in {"src", "href"} and re.match(r"(?i)https?://|//", value):
            errors.append(f"external URL is not allowed: {tag}.{name}={value}")
    if not any(token in " ".join(parser.text) for token in ("READY", "PARTIAL", "BLOCKED", "INCOMPLETE")):
        errors.append("missing terminal task status")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", help="Path to rendered task report HTML")
    args = parser.parse_args(argv)
    errors = validate_html_report(args.html)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"OK: {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
