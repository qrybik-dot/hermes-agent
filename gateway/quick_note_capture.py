"""Deterministic Hermes Knowledge quick-save for explicit short notes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import subprocess
from typing import Any
from urllib.parse import urlsplit

HELPER_PATH = "/opt/hermes-knowledge-mcp/quick_save_cli.py"

_COMMAND_RE = re.compile(
    r"^\s*(?P<verb>запиши|сохрани|зафиксируй)\s+"
    r"(?P<object>локаци[юя]|адрес|заметк[уа]|место)\s+"
    r"(?P<label>.+?)\s*[.!?]*$",
    re.I | re.S,
)
_BLOCK_RE = re.compile(
    r"\b("
    r"календар|напоминан|reminder|calendar|"
    r"письм|почт|email|mail|gmail|"
    r"файл|код|скрипт|репозитори|git|github|"
    r"vps|gateway|systemd|service|ssh|"
    r"https?://|www\.|скача|download|"
    r"удали|удалить|delete|remove|drop"
    r")\w*",
    re.I,
)
_UNCLEAR_RE = re.compile(r"^(?:это|тут|здесь|вот это|данное|такое)$", re.I)
_ADDRESS_HINT_RE = re.compile(
    r"\b(?:г\.|город|обл\.|область|район|дер\.|деревня|пос\.|улица|ул\.|стр\.|дом|д\.)\b|,\s*\d",
    re.I,
)


@dataclass(frozen=True)
class QuickNote:
    payload: dict[str, Any]
    idempotency_key: str



_CONTEXT_SAVE_RE = re.compile(
    r"^\s*(?:"
    r"сохр\w*|сохрарни|запиши|зафиксируй|добавь"
    r")\b(?P<tail>.*?)\s*[.!?]*$",
    re.I | re.S,
)
_CONTEXT_SAVE_HINT_RE = re.compile(
    r"\b(?:баз[уы]|knowledge|памят[ьи]|инфо|информаци[яюи]|стать[яюи]|ссылк[ауи]|материал|документ|источник|это)\b",
    re.I,
)
_CONTEXT_SAVE_BLOCK_RE = re.compile(
    r"\b(?:календар|напоминан|reminder|calendar|удали|удалить|delete|remove|drop|trash)\w*",
    re.I,
)
_URL_RE = re.compile(r"https?://[^\s<>)\]}\\\"']+", re.I)


def _urls(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for match in _URL_RE.finditer(str(text or "")):
        value = match.group(0).rstrip(".,;:!?)]}>")
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _domain(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    return (parsed.netloc or "").removeprefix("www.").casefold()


def _topic_from_save_command(text: str) -> str:
    clean = _clean(text)
    patterns = (
        r"\bпро\s+(.+?)(?:\s+-\s+|$)",
        r"\b(?:стать[яю]|материал|инфо|информаци[яю]|ссылк[ау])\s+(.+?)(?:\s+-\s+|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, clean, re.I)
        if match:
            topic = _clean(match.group(1))
            topic = re.sub(r"\b(?:инфа|информация)\s+по\s+ссылке\b", "", topic, flags=re.I).strip(" -;,. ")
            if topic and not _UNCLEAR_RE.fullmatch(topic):
                return topic[:180]
    return ""


def _title_from_context_save(command_text: str, body: str, urls: list[str]) -> str:
    topic = _topic_from_save_command(command_text)
    if topic:
        return f"Материал: {topic}"[:240]
    joined = "\n".join([body, " ".join(urls)]).casefold()
    if "yandex.cloud" in joined and "datalens" in joined and ("neuroanalyst" in joined or "нейроаналит" in joined):
        return "Yandex DataLens: нейроаналитик в дашбордах"
    if urls:
        domain = _domain(urls[0]) or "ссылка"
        return f"Материал: {domain}"[:240]
    first = _clean(body).split(".", 1)[0].strip()
    return ("Материал: " + first[:180])[:240] if first else "Материал из переписки"


def detect_context_save_note(
    command_text: str,
    *context_texts: str | None,
    continued: bool = False,
) -> QuickNote | None:
    """Capture explicit 'save this/info/link/article to knowledge' over reply/context.

    This fast path prevents simple knowledge saves from falling through to an LLM,
    where weaker fallback models may invent unavailable tools or try terminal workarounds.
    """
    if continued:
        return None
    command = str(command_text or "").strip()
    if not command:
        return None
    match = _CONTEXT_SAVE_RE.match(command)
    if not match:
        return None
    if _CONTEXT_SAVE_BLOCK_RE.search(command):
        return None
    tail = match.group("tail") or ""
    command_urls = _urls(command)
    if not (_CONTEXT_SAVE_HINT_RE.search(tail) or command_urls):
        return None

    context_parts = [_clean(value or "") for value in context_texts if _clean(value or "")]
    if command_urls:
        context_parts.append(_clean(command))
    body = "\n\n".join(part for part in context_parts if part).strip()
    if not body or _UNCLEAR_RE.fullmatch(body):
        return None
    if len(body) < 8 and not command_urls:
        return None

    sources = _urls(body)
    title = _title_from_context_save(command, body, sources)
    summary_parts = [body]
    user_note = _clean(command)
    if user_note and user_note not in body:
        summary_parts.append("Команда пользователя: " + user_note)
    summary = "\n\n".join(summary_parts)[:4000]
    payload = {
        "knowledge_project": "general",
        "type": "research" if sources else "note",
        "title": title,
        "summary": summary,
        "accepted_facts": [body[:1000]],
        "sources": sources,
        "sensitivity": "internal",
        "gpt_access": "allowed",
        "source_system": "hermes",
        "source_workspace": "telegram",
    }
    key_source = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return QuickNote(
        payload=payload,
        idempotency_key="context-save:" + hashlib.sha256(key_source.encode("utf-8")).hexdigest(),
    )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip(" \t\r\n.;")


def _label(text: str) -> str:
    value = _clean(text)
    value = re.sub(r"^пляжа\b", "пляж", value, flags=re.I)
    value = re.sub(r"\bпирогово\b", "Пирогово", value, flags=re.I)
    return value


def detect_quick_note(text: str, *, continued: bool = False) -> QuickNote | None:
    if continued:
        return None
    raw = str(text or "").strip()
    if not raw or _BLOCK_RE.search(raw):
        return None

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return None

    command_index = None
    command_match = None
    for index, line in enumerate(lines):
        match = _COMMAND_RE.match(line)
        if match:
            command_index = index
            command_match = match
            break
    if command_match is None or command_index is None:
        return None

    before = _clean("\n".join(lines[:command_index]))
    after = _clean("\n".join(lines[command_index + 1:]))
    label = _label(command_match.group("label"))
    obj = command_match.group("object").casefold()
    if not label or _UNCLEAR_RE.fullmatch(label):
        return None
    if after:
        return None

    fact = before
    if not fact and obj.startswith("адрес"):
        fact = label
        label = "адрес"
    elif not fact and obj.startswith("замет"):
        fact = label

    if not fact or _UNCLEAR_RE.fullmatch(fact):
        return None

    accepted_facts: list[dict[str, str]] = [{"kind": "user_label", "value": label}]
    is_address = bool(_ADDRESS_HINT_RE.search(fact))
    if is_address:
        accepted_facts.insert(0, {"kind": "address", "value": fact})
        summary = f"{label[:1].upper() + label[1:]}. Адрес: {fact}"
    else:
        accepted_facts.insert(0, {"kind": "note", "value": fact})
        summary = fact

    title_prefix = "Локация" if obj.startswith(("локаци", "мест")) else "Заметка"
    if obj.startswith("адрес"):
        title_prefix = "Адрес"
    title = f"{title_prefix}: {label[:1].upper() + label[1:]}"
    is_travel = obj.startswith(("\u043b\u043e\u043a\u0430\u0446\u0438", "\u043c\u0435\u0441\u0442", "\u0430\u0434\u0440\u0435\u0441")) or is_address
    payload = {
        "knowledge_project": "travel" if is_travel else "general",
        "type": "note",
        "title": title[:240],
        "summary": summary[:4000],
        "accepted_facts": accepted_facts,
        "sensitivity": "internal",
        "gpt_access": "allowed",
        "source_system": "hermes",
        "source_workspace": "telegram",
    }
    key_source = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return QuickNote(
        payload=payload,
        idempotency_key="quick-note:" + hashlib.sha256(key_source.encode("utf-8")).hexdigest(),
    )


def run_quick_save(note: QuickNote, *, wait_seconds: int = 60) -> dict[str, Any]:
    request = {
        "payload": note.payload,
        "idempotency_key": note.idempotency_key,
        "wait_seconds": wait_seconds,
    }
    proc = subprocess.run(
        ["sudo", "-n", "-u", "hermes-knowledge", "/usr/bin/python3", HELPER_PATH],
        input=json.dumps(request, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=max(15, wait_seconds + 20),
        check=False,
    )
    output = (proc.stdout or "").strip().splitlines()
    if not output:
        raise RuntimeError("quick save returned no JSON")
    try:
        result = json.loads(output[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("quick save returned invalid JSON") from exc
    if proc.returncode != 0 and result.get("status") != "error":
        result["status"] = "error"
        result["saved"] = False
    return result


def format_quick_save_response(result: dict[str, Any]) -> tuple[str, str]:
    saved = bool(result.get("saved"))
    already = bool(result.get("already_exists"))
    readback = int(result.get("readback_count") or 0)
    title = _clean(result.get("title") or "заметка")
    summary = _clean(result.get("summary") or "")
    kind, _, subject = title.partition(":")
    subject = subject.strip() or title
    location_like = kind.casefold() in {"локация", "адрес"}
    if (saved or already) and readback > 0:
        if already:
            if location_like:
                return "Эта локация уже сохранена", "success"
            return f"Эта заметка уже сохранена: {subject}", "success"
        if location_like:
            address = summary.split("Адрес:", 1)[1].strip() if "Адрес:" in summary else summary
            suffix = f"\n{address}" if address else ""
            return f"Записал: {subject}{suffix}", "success"
        return f"Сохранено: {subject}", "success"
    message = _clean(result.get("message") or "публикация не подтверждена")
    return f"Не удалось подтвердить сохранение: {message}", "failed"


_PLACE_LABELS = {
    "дом": "Дом",
    "дома": "Дом",
    "домой": "Дом",
    "работа": "Работа",
    "работу": "Работа",
    "работы": "Работа",
    "офис": "Работа",
    "дача": "Дача",
    "дачу": "Дача",
    "дачи": "Дача",
}


def canonical_place_label(raw: str | None) -> str | None:
    value = _clean(raw or "").casefold()
    if not value:
        return None
    return _PLACE_LABELS.get(value) or (value[:1].upper() + value[1:])


def build_location_place_note(*, label: str, latitude: float, longitude: float, source_message_id: str | None = None) -> QuickNote:
    canonical = canonical_place_label(label) or "Место"
    rounded_lat = round(float(latitude), 6)
    rounded_lon = round(float(longitude), 6)
    summary = (
        f"{canonical}. Координаты сохранены из Telegram location. "
        f"lat={rounded_lat:.6f}; lon={rounded_lon:.6f}. "
        "confidence=explicit_user_location."
    )
    accepted_facts = [
        {"kind": "personal_place_label", "value": canonical},
        {"kind": "latitude", "value": f"{rounded_lat:.6f}"},
        {"kind": "longitude", "value": f"{rounded_lon:.6f}"},
        {"kind": "source", "value": "telegram_location"},
        {"kind": "confidence", "value": "explicit_user_location"},
    ]
    if source_message_id:
        accepted_facts.append({"kind": "source_message_id", "value": str(source_message_id)})
    payload = {
        "knowledge_project": "travel",
        "type": "note",
        "title": f"Личное место: {canonical}",
        "summary": summary[:4000],
        "accepted_facts": accepted_facts,
        "sensitivity": "personal_sensitive",
        "gpt_access": "restricted",
        "source_system": "hermes",
        "source_workspace": "telegram",
    }
    return QuickNote(
        payload=payload,
        idempotency_key="personal-place:" + hashlib.sha256(canonical.casefold().encode("utf-8")).hexdigest(),
    )
