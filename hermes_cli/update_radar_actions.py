"""Interactive Telegram state for Update Radar cards.

The radar remains read-only. Buttons only select findings and, after an explicit
confirmation, enqueue a normal Hermes user turn. The existing VPS safety skill
and approval flow stay responsible for any actual change.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/home/hermes/.hermes"))
ACTION_FILE = HERMES_HOME / "update-radar/actions.json"
RADAR_STATE_FILE = HERMES_HOME / "update-radar/state.json"
ACTION_TTL_HOURS = 72


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except Exception:
        return default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    os.replace(temp, path)


def _finding_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        from dataclasses import asdict

        return asdict(value)
    except Exception:
        raise TypeError("Update Radar finding must be a mapping or dataclass")


def finding_signature(value: Any) -> str:
    item = _finding_dict(value)
    payload = {
        "key": item.get("key"),
        "finding_type": item.get("finding_type"),
        "current": item.get("current"),
        "latest": item.get("latest"),
        "status": item.get("status"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _store_default() -> dict[str, Any]:
    return {"schema": 1, "actions": {}, "snoozed": {}}


def _prune(store: dict[str, Any]) -> None:
    cutoff = _utc_now() - timedelta(hours=ACTION_TTL_HOURS)
    actions = store.setdefault("actions", {})
    for token, action in list(actions.items()):
        created = _parse_iso(action.get("created_at"))
        if created is None or created < cutoff:
            actions.pop(token, None)
    snoozed = store.setdefault("snoozed", {})
    now = _utc_now()
    for signature, until in list(snoozed.items()):
        parsed = _parse_iso(str(until))
        if parsed is None or parsed <= now:
            snoozed.pop(signature, None)


def _is_security(item: dict[str, Any]) -> bool:
    return item.get("finding_type") == "security"


def _is_recommended(item: dict[str, Any]) -> bool:
    return item.get("finding_type") == "release" and str(item.get("verdict") or "").startswith("✅")


def _is_pilot_or_check(item: dict[str, Any]) -> bool:
    return not (_is_security(item) or _is_recommended(item))


def create_action(findings: Iterable[Any]) -> dict[str, Any]:
    items = [_finding_dict(value) for value in findings]
    token = secrets.token_urlsafe(6).rstrip("=")
    security = [item["key"] for item in items if _is_security(item)]
    recommended = [item["key"] for item in items if _is_recommended(item)]
    checks = [item["key"] for item in items if _is_pilot_or_check(item)]
    action = {
        "token": token,
        "created_at": _iso(_utc_now()),
        "stage": "home",
        "status": "open",
        "findings": items,
        "security": security,
        "recommended": recommended,
        "checks": checks,
        "selected": list(dict.fromkeys(security + recommended)),
        "queued_at": None,
    }
    with _locked(ACTION_FILE):
        store = _read_json(ACTION_FILE, _store_default())
        _prune(store)
        store.setdefault("actions", {})[token] = action
        _write_json(ACTION_FILE, store)
    return action


def get_action(token: str) -> dict[str, Any] | None:
    with _locked(ACTION_FILE):
        store = _read_json(ACTION_FILE, _store_default())
        _prune(store)
        action = store.setdefault("actions", {}).get(token)
        _write_json(ACTION_FILE, store)
        return dict(action) if isinstance(action, dict) else None


def update_action(token: str, **changes: Any) -> dict[str, Any] | None:
    with _locked(ACTION_FILE):
        store = _read_json(ACTION_FILE, _store_default())
        _prune(store)
        action = store.setdefault("actions", {}).get(token)
        if not isinstance(action, dict):
            _write_json(ACTION_FILE, store)
            return None
        action.update(changes)
        _write_json(ACTION_FILE, store)
        return dict(action)


def set_selection(token: str, keys: Iterable[str], *, stage: str = "confirm") -> dict[str, Any] | None:
    action = get_action(token)
    if not action:
        return None
    allowed = set(action.get("security", []) + action.get("recommended", []))
    selected = [key for key in keys if key in allowed]
    return update_action(token, selected=list(dict.fromkeys(selected)), stage=stage)


def toggle_selection(token: str, key: str) -> dict[str, Any] | None:
    action = get_action(token)
    if not action:
        return None
    allowed = set(action.get("security", []) + action.get("recommended", []))
    if key not in allowed:
        return action
    selected = list(action.get("selected") or [])
    if key in selected:
        selected.remove(key)
    else:
        selected.append(key)
    return update_action(token, selected=selected, stage="manual")


def mark_queued(token: str, *, mode: str) -> dict[str, Any] | None:
    return update_action(
        token,
        status="queued",
        stage="queued",
        queued_mode=mode,
        queued_at=_iso(_utc_now()),
    )


def postpone_until_tomorrow(token: str) -> dict[str, Any] | None:
    action = get_action(token)
    if not action:
        return None
    now = _utc_now()
    next_run = (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    signatures = [finding_signature(item) for item in action.get("findings", [])]
    with _locked(ACTION_FILE):
        store = _read_json(ACTION_FILE, _store_default())
        _prune(store)
        for signature in signatures:
            store.setdefault("snoozed", {})[signature] = _iso(next_run)
        stored = store.setdefault("actions", {}).get(token)
        if isinstance(stored, dict):
            stored.update({"status": "postponed", "stage": "postponed", "postponed_until": _iso(next_run)})
        _write_json(ACTION_FILE, store)
    # Make the same findings eligible again after the snooze window. This is a
    # bounded edit of Update Radar's own state, not a component update.
    with _locked(RADAR_STATE_FILE):
        radar_state = _read_json(RADAR_STATE_FILE, {"schema": 3, "observed": {}, "notified": {}})
        notified = radar_state.setdefault("notified", {})
        for item in action.get("findings", []):
            notified.pop(str(item.get("key") or ""), None)
        _write_json(RADAR_STATE_FILE, radar_state)
    return get_action(token)


def filter_snoozed(findings: Iterable[Any]) -> list[Any]:
    values = list(findings)
    with _locked(ACTION_FILE):
        store = _read_json(ACTION_FILE, _store_default())
        _prune(store)
        snoozed = dict(store.get("snoozed") or {})
        _write_json(ACTION_FILE, store)
    return [value for value in values if finding_signature(value) not in snoozed]


def _item_map(action: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("key")): item for item in action.get("findings", [])}


def _version_line(item: dict[str, Any]) -> str:
    current = item.get("current")
    latest = item.get("latest")
    if item.get("finding_type") == "security":
        packages = latest if isinstance(latest, list) else [latest]
        return ", ".join(str(value) for value in packages if value) or "пакеты безопасности"
    return f"{current or '—'} → {latest or '—'}"


def _short_name(item: dict[str, Any]) -> str:
    name = str(item.get("name") or item.get("key") or "Компонент")
    return name.replace("Ubuntu security updates", "Ubuntu security")


def render_home(action: dict[str, Any]) -> str:
    items = _item_map(action)
    lines = ["🛰 Обновления Hermes", ""]
    if action.get("security"):
        lines.append("🚨 Безопасность")
        for key in action["security"]:
            item = items[key]
            lines.append(f"• {_short_name(item)}: {_version_line(item)}")
        lines.append("Рекомендация: установить сегодня")
        lines.append("")
    if action.get("recommended"):
        lines.append("✅ Рекомендуемые обновления")
        for key in action["recommended"]:
            item = items[key]
            lines.append(f"• {_short_name(item)}: {_version_line(item)}")
        lines.append("")
    if action.get("checks"):
        lines.append("🧪 Требуют проверки")
        for key in action["checks"]:
            item = items[key]
            verdict = str(item.get("verdict") or "проверить").lstrip("🧪🚨✅⏸ ")
            lines.append(f"• {_short_name(item)}: {verdict}")
        lines.append("")
    lines.append("Ничего не обновлено. Выберите действие ниже")
    return "\n".join(lines).strip()[:3600]


def render_manual(action: dict[str, Any]) -> str:
    selected = set(action.get("selected") or [])
    items = _item_map(action)
    lines = ["⚙️ Выбор обновлений", ""]
    for key in action.get("security", []) + action.get("recommended", []):
        mark = "☑️" if key in selected else "⬜"
        item = items[key]
        lines.append(f"{mark} {_short_name(item)}: {_version_line(item)}")
    lines.extend(["", "Нажатие по компоненту меняет выбор. Запуск будет только после подтверждения"])
    return "\n".join(lines).strip()


def render_confirm(action: dict[str, Any]) -> str:
    selected = set(action.get("selected") or [])
    items = _item_map(action)
    lines = ["Подтвердите обновление", ""]
    for key in action.get("security", []) + action.get("recommended", []):
        if key in selected:
            item = items[key]
            lines.append(f"• {_short_name(item)}: {_version_line(item)}")
    lines.extend(
        [
            "",
            "Hermes сначала проверит сервисы, место, зависимости и откат. Затем выполнит только выбранные пункты и проведёт smoke-тесты",
            "",
            "Перезагрузка VPS автоматически не выполняется",
        ]
    )
    return "\n".join(lines).strip()


def render_details(action: dict[str, Any]) -> str:
    lines = ["ℹ️ Подробности Update Radar", ""]
    for item in action.get("findings", []):
        lines.append(f"{item.get('verdict') or '•'} {_short_name(item)}")
        lines.append(f"Сейчас: {item.get('current') or '—'}")
        lines.append(f"Найдено: {item.get('latest') or item.get('status') or '—'}")
        reason = str(item.get("reason") or "").strip()
        if reason:
            lines.append(f"Почему: {reason}")
        source = str(item.get("source") or "").strip()
        if source:
            lines.append(f"Источник: {source}")
        lines.append("")
    lines.append("Ничего не обновлено")
    return "\n".join(lines).strip()[:3900]


def render_postponed(action: dict[str, Any]) -> str:
    until = _parse_iso(action.get("postponed_until"))
    when = until.strftime("%d.%m около 07:15 UTC") if until else "завтра"
    return f"⏰ Напоминание об этих обновлениях отложено до {when}"


def render_queued(action: dict[str, Any], *, mode: str) -> str:
    if mode == "checks":
        return "⏳ Проверка передана Hermes. Результат придёт отдельным сообщением"
    return "⏳ Обновление передано Hermes. Прогресс и итог придут отдельными сообщениями"


def render_for_stage(action: dict[str, Any]) -> str:
    stage = action.get("stage") or "home"
    if stage == "manual":
        return render_manual(action)
    if stage == "confirm":
        return render_confirm(action)
    if stage == "details":
        return render_details(action)
    if stage == "postponed":
        return render_postponed(action)
    if stage == "queued":
        return render_queued(action, mode=str(action.get("queued_mode") or "update"))
    return render_home(action)


def keyboard_spec(action: dict[str, Any]) -> list[list[dict[str, str]]]:
    token = action["token"]
    stage = action.get("stage") or "home"
    if action.get("status") != "open" or stage in {"queued", "postponed"}:
        return []
    if stage == "home":
        rows: list[list[dict[str, str]]] = []
        if action.get("security"):
            rows.append([{"text": "🚨 Обновить безопасность", "callback_data": f"ur:{token}:sec"}])
        if action.get("recommended"):
            rows.append([{"text": "✅ Обновить рекомендуемое", "callback_data": f"ur:{token}:rec"}])
        if action.get("security") or action.get("recommended"):
            rows.append([{"text": "⚙️ Выбрать вручную", "callback_data": f"ur:{token}:manual"}])
        if action.get("checks"):
            rows.append([{"text": "🧪 Запустить проверки", "callback_data": f"ur:{token}:checks"}])
        rows.append(
            [
                {"text": "⏰ Напомнить завтра", "callback_data": f"ur:{token}:later"},
                {"text": "ℹ️ Подробнее", "callback_data": f"ur:{token}:details"},
            ]
        )
        return rows
    if stage == "manual":
        selected = set(action.get("selected") or [])
        items = _item_map(action)
        rows = []
        for key in action.get("security", []) + action.get("recommended", []):
            mark = "☑️" if key in selected else "⬜"
            label = f"{mark} {_short_name(items[key])}"
            rows.append([{"text": label[:64], "callback_data": f"ur:{token}:toggle:{key}"}])
        rows.append([{"text": f"▶️ Продолжить: {len(selected)}", "callback_data": f"ur:{token}:confirm"}])
        rows.append(
            [
                {"text": "↩️ Назад", "callback_data": f"ur:{token}:home"},
                {"text": "✖️ Отмена", "callback_data": f"ur:{token}:cancel"},
            ]
        )
        return rows
    if stage == "confirm":
        return [
            [{"text": "▶️ Подтвердить и запустить", "callback_data": f"ur:{token}:execute"}],
            [
                {"text": "↩️ Изменить выбор", "callback_data": f"ur:{token}:manual"},
                {"text": "✖️ Отмена", "callback_data": f"ur:{token}:cancel"},
            ],
        ]
    if stage == "details":
        return [[{"text": "↩️ Назад", "callback_data": f"ur:{token}:home"}]]
    return []


def telegram_markup(action: dict[str, Any]):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = [
        [InlineKeyboardButton(button["text"], callback_data=button["callback_data"]) for button in row]
        for row in keyboard_spec(action)
    ]
    return InlineKeyboardMarkup(rows) if rows else None


def marker_for(action: dict[str, Any]) -> str:
    return f"[UPDATE_RADAR_ACTIONS:{action['token']}]"


def build_agent_prompt(action: dict[str, Any], *, mode: str) -> str:
    items = _item_map(action)
    if mode == "checks":
        keys = list(action.get("checks") or [])
        title = "Проведи только read-only проверки по находкам Update Radar"
    else:
        keys = list(action.get("selected") or [])
        title = "Обнови только выбранные компоненты из Update Radar"
    lines = [title + ":"]
    for key in keys:
        item = items.get(key)
        if not item:
            continue
        lines.append(
            f"- {item.get('name')}: {item.get('current') or '—'} → {item.get('latest') or item.get('status') or '—'}; "
            f"источник: {item.get('source') or 'локальная проверка'}"
        )
    if mode == "checks":
        lines.extend(
            [
                "Ничего не устанавливай и не меняй. Проверь актуальность, совместимость, риски и нужность пилота. Верни короткий вердикт по каждому пункту",
                "Работай через настроенный SSH/VPS Admin. Не используй GitHub-коннекторы или новый OAuth",
            ]
        )
    else:
        lines.extend(
            [
                "Работай через настроенный SSH/VPS Admin и skill vps-admin-safe. Не обновляй остальные компоненты",
                "Перед изменениями проверь свободное место, активные сервисы, зависимости, резервную копию и реальный откат. Если безопасного отката нет, остановись до установки и сообщи",
                "После каждого обновления проверь версию и профильный smoke-тест. При регрессии откати компонент. VPS автоматически не перезагружай",
                "Финал: READY / PARTIAL / BLOCKED, что изменено, чем проверено и что осталось",
            ]
        )
    return "\n".join(lines)
