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
    if "prefers-color-scheme" not in lowered or "data-theme=\"dark\"" not in lowered or "data-theme=\"light\"" not in lowered:
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
