"""Deterministic Hermes Knowledge quick-save for explicit short notes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import subprocess
from typing import Any

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
