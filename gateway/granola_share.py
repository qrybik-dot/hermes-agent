"""Safe parser for public Granola share pages."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html as html_lib
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

_GRANOLA_SHARE_RE = re.compile(
    r"https://notes\.granola\.ai/t/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:[?#][^\s]*)?",
    re.I,
)
_FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)')
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)
_DOCUMENT_ID_RE = re.compile(r'"documentId":"([0-9a-f-]{36})"', re.I)
_CREATED_RE = re.compile(r'"created_at":"([^"]+)"', re.I)
_ZOOM_RE = re.compile(r'https://[^\s"<>]*zoom\.us/[^\s"<>]+', re.I)
_ANY_URL_RE = re.compile(r"https?://\S+", re.I)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")


def extract_granola_share_url(text: str) -> str | None:
    match = _GRANOLA_SHARE_RE.search(str(text or ""))
    return match.group(0).rstrip(".,;:!?)]}>") if match else None


def is_granola_share_url(text: str) -> bool:
    value = str(text or "").strip()
    url = extract_granola_share_url(value)
    return bool(url and value.rstrip(".,;:!?)]}>") == url)


def _validate_url(url: str) -> str:
    candidate = extract_granola_share_url(url)
    if not candidate:
        raise ValueError("not a Granola public share URL")
    parsed = urlsplit(candidate)
    if parsed.scheme != "https" or parsed.hostname != "notes.granola.ai":
        raise ValueError("Granola share host is not allowed")
    if not re.fullmatch(r"/t/[0-9a-fA-Fe-]{36}", parsed.path):
        raise ValueError("invalid Granola share path")
    return candidate


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr"}:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        value = html_lib.unescape("".join(self.parts))
        lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _plain_text(fragment: str) -> str:
    parser = _TextExtractor()
    parser.feed(fragment)
    return parser.text()


def _meta(page_html: str, name: str) -> str:
    patterns = [
        rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)["\']',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:name|property)=["\"]{re.escape(name)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_html, re.I | re.S)
        if match:
            return html_lib.unescape(match.group(1)).strip()
    return ""


def _decode_flight_chunks(page_html: str) -> list[str]:
    chunks: list[str] = []
    for match in _FLIGHT_RE.finditer(page_html):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(value, str):
            chunks.append(value)
    return chunks


def _summary_from_chunks(chunks: list[str]) -> str:
    candidates: list[str] = []
    for chunk in chunks:
        decoded = html_lib.unescape(chunk)
        if "<h3" not in decoded and "<ul" not in decoded:
            continue
        start = decoded.find("<h")
        if start < 0:
            continue
        fragment = decoded[start:]
        for marker in ('","created_at"', '","suggested_questions"', '\n"])'):
            pos = fragment.find(marker)
            if pos > 0:
                fragment = fragment[:pos]
        text = _plain_text(fragment)
        if len(text) >= 80:
            candidates.append(text)
    return max(candidates, key=len) if candidates else ""


def _knowledge_safe_text(value: str) -> str:
    text = _ANY_URL_RE.sub("", str(value or ""))
    text = _PHONE_RE.sub("", text)
    text = re.sub(r"(?i)\b(?:код доступа|пароль|password|passcode)\b[^\n]*", "", text)
    text = re.sub(r"(?i)банковск\w*\s+СБ", "корпоративной СБ", text)
    lines = [re.sub(r"\s+", " ", line).strip(" •") for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


@dataclass(frozen=True)
class GranolaShare:
    source_url: str
    title: str
    description: str
    summary: str
    document_id: str
    created_at: str
    conferencing_url: str
    content_hash: str

    def knowledge_summary(self, *, limit: int = 3900) -> str:
        prefix = []
        if self.created_at:
            prefix.append(f"Дата Granola: {self.created_at}")
        if self.document_id:
            prefix.append(f"Granola document ID: {self.document_id}")
        prefix.append(f"Источник: {self.source_url}")
        body = _knowledge_safe_text(self.summary or self.description)
        value = "\n".join(prefix) + ("\n\n" + body if body else "")
        return value[:limit].rstrip()


def parse_granola_share_html(source_url: str, page_html: str) -> GranolaShare:
    url = _validate_url(source_url)
    title = _meta(page_html, "og:title") or _meta(page_html, "twitter:title")
    if not title:
        match = _TITLE_RE.search(page_html)
        title = html_lib.unescape(match.group(1)).strip() if match else "Встреча Granola"
    description = _meta(page_html, "og:description") or _meta(page_html, "twitter:description")
    chunks = _decode_flight_chunks(page_html)
    joined = "\n".join(chunks)
    summary = _summary_from_chunks(chunks)
    document_match = _DOCUMENT_ID_RE.search(joined)
    created_match = _CREATED_RE.search(joined)
    zoom_match = _ZOOM_RE.search(joined)
    document_id = document_match.group(1) if document_match else ""
    created_at = created_match.group(1) if created_match else ""
    semantic_payload = json.dumps(
        {
            "source_url": url,
            "title": title[:240],
            "summary": _knowledge_safe_text(summary or description),
            "document_id": document_id,
            "created_at": created_at,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return GranolaShare(
        source_url=url,
        title=title[:240],
        description=description[:1000],
        summary=summary[:12000],
        document_id=document_id,
        created_at=created_at,
        conferencing_url=html_lib.unescape(zoom_match.group(0)).rstrip('\\"') if zoom_match else "",
        content_hash=hashlib.sha256(semantic_payload.encode("utf-8")).hexdigest(),
    )


def fetch_granola_share(url: str, *, timeout: float = 15.0, max_bytes: int = 1_000_000) -> GranolaShare:
    candidate = _validate_url(url)
    request = Request(candidate, headers={"User-Agent": "Hermes-Granola-Reader/1.0"})
    with urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        if status != 200:
            raise RuntimeError(f"Granola share returned HTTP {status}")
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise RuntimeError("Granola share page exceeds safety limit")
    parsed = parse_granola_share_html(candidate, raw.decode("utf-8", errors="replace"))
    if not parsed.title or not (parsed.summary or parsed.description):
        raise RuntimeError("Granola share content was not found in the public page")
    return parsed


__all__ = [
    "GranolaShare",
    "extract_granola_share_url",
    "fetch_granola_share",
    "is_granola_share_url",
    "parse_granola_share_html",
]
