"""Telegram live status based on the task's real actions, not a fixed template."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
import re
import time
from typing import Iterable, Mapping, Sequence


_TECHNICAL_EVENT_RE = re.compile(
    r"receiving stream response|waiting for non-streaming API response|pytest|\buv\b|venv|delegate_task|reviewer|subagent|"
    r"tool trace|tool\.started|tool\.completed|iteration budget|internal hypothes|self-improvement|"
    r"search_files|read_file|terminal|patch|write_file",
    re.I,
)

_PHASE_PCT = {
    "start": 5,
    "discover": 25,
    "work": 60,
    "verify": 80,
    "deliver": 90,
    "done": 100,
}

_LEGACY_PHASE = {
    "accepted": "start",
    "audit": "discover",
    "prepared": "work",
    "applied": "work",
    "tests": "verify",
    "delivery": "deliver",
    "done": "done",
    "blocked": "deliver",
    "incomplete": "deliver",
}

_LEGACY_ACTION = {
    "accepted": "Разбираю запрос",
    "audit": "Собираю нужные данные",
    "prepared": "Готовлю решение",
    "applied": "Выполняю работу",
    "tests": "Проверяю результат",
    "delivery": "Готовлю итоговый ответ",
    "done": "Завершено",
}

_PROGRESS_THEMES = {
    "developer": ("💭", "🔍", "🧑‍💻", "🧪", "🚀", "🚀"),
    "detective": ("❓", "🔍", "🕵️", "🧪", "💡", "💡"),
    "runner": ("🧍", "🚶", "🏃", "🏃‍♂️", "🏁", "🏆"),
}

_DEVELOPER_TASK_RE = re.compile(
    r"код|разработ|баг|ошиб|фикс|сервер|vps|gateway|telegram|интеграц|настро|конфиг|"
    r"депло|deploy|git|тест|skill|router|маршрутиз|автоматизац",
    re.I,
)
_DETECTIVE_TASK_RE = re.compile(
    r"исслед|анализ|аудит|диагност|проверь|проверка|сравн|найд|поиск|причин|"
    r"разбер|изучи|расслед|источник|логи",
    re.I,
)
_AGREEMENT_ONLY_RE = re.compile(
    r"^(?:да|ок(?:ей)?|согласен|верно|точно|подтверждаю|делай|действуй|\+1)[.!\s]*$",
    re.I,
)
_LIST_ITEM_RE = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])\s+(.+?)\s*$")


def progress_theme_for_title(title: str) -> str:
    value = str(title or "")
    if _DEVELOPER_TASK_RE.search(value):
        return "developer"
    if _DETECTIVE_TASK_RE.search(value):
        return "detective"
    return "runner"


def _safe_fragment(value: object, *, limit: int = 58) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" `\"'.,:;-")
    text = re.sub(r"https?://\S+", "ссылку", text, flags=re.I)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _basename(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "файл"
    try:
        name = PurePath(raw).name
    except Exception:
        name = raw.rsplit("/", 1)[-1]
    return _safe_fragment(name, limit=44) or "файл"


def status_steps_from_request(text: str, *, limit: int = 6) -> list[str]:
    """Extract only explicit user subtasks; never invent a generic checklist."""
    steps: list[str] = []
    for raw in str(text or "").splitlines():
        match = _LIST_ITEM_RE.match(raw)
        if match is None:
            continue
        item = _safe_fragment(match.group(1), limit=72)
        if not item or _AGREEMENT_ONLY_RE.fullmatch(item):
            continue
        if item not in steps:
            steps.append(item)
        if len(steps) >= limit:
            break
    return steps


def status_plan_from_tool_args(tool_name: str, args: Mapping[str, object] | None) -> list[str]:
    """Read an explicit todo payload when the agent actually created one."""
    if "todo" not in str(tool_name or "").lower() or not isinstance(args, Mapping):
        return []
    raw = args.get("todos") or args.get("items") or args.get("tasks") or args.get("steps")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, Mapping):
            value = item.get("content") or item.get("title") or item.get("task") or item.get("text")
        else:
            value = item
        step = _safe_fragment(value, limit=72)
        if step and not _AGREEMENT_ONLY_RE.fullmatch(step) and step not in out:
            out.append(step)
        if len(out) >= 6:
            break
    return out


def _first_arg(args: Mapping[str, object] | None, *keys: str) -> object:
    if not isinstance(args, Mapping):
        return ""
    for key in keys:
        value = args.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def status_action_for_tool(
    tool_name: str | None,
    preview: str | None = None,
    args: Mapping[str, object] | None = None,
) -> tuple[str, str]:
    """Return a user-facing action and phase from the tool that really started."""
    name = str(tool_name or "").lower().replace("-", "_")
    query = _safe_fragment(_first_arg(args, "query", "q", "search", "pattern"), limit=54)
    path = _first_arg(args, "path", "file_path", "filename", "target")
    command = str(_first_arg(args, "command", "cmd", "code") or preview or "").lower()

    if "todo" in name:
        return "Уточняю план по реальным подзадачам", "discover"
    if "delegate" in name or "review" in name:
        return "Проверяю план на риски и пропуски", "verify"
    if "memory" in name or "session_search" in name:
        return (f"Ищу в памяти: {query}" if query else "Ищу нужный контекст в памяти"), "discover"
    if "skill" in name:
        return (f"Проверяю навык: {query}" if query else "Проверяю доступные навыки"), "discover"
    if "read_file" in name or name == "file.read":
        return f"Читаю {_basename(path)}", "discover"
    if "search_file" in name or "grep" in name:
        return (f"Ищу в коде: {query}" if query else "Ищу нужный участок кода"), "discover"
    if "write_file" in name or "patch" in name or "edit_file" in name:
        return f"Изменяю {_basename(path)}", "work"
    if "web" in name or "browser" in name:
        return (f"Ищу: {query}" if query else "Проверяю источники"), "discover"
    if "code_execution" in name:
        return "Проверяю решение на тестовом запуске", "verify"
    if "terminal" in name or "shell" in name:
        if re.search(r"pytest|unittest|npm\s+test|vitest|go\s+test|cargo\s+test", command):
            return "Запускаю тесты", "verify"
        if re.search(r"git\s+(?:diff|status)|diff\s+--check", command):
            return "Проверяю список и чистоту изменений", "verify"
        if re.search(r"systemctl|journalctl", command):
            return "Проверяю сервис и его логи", "verify"
        if re.search(r"py_compile|compileall|syntax|ruff|mypy|eslint", command):
            return "Проверяю синтаксис и качество кода", "verify"
        if re.search(r"cp\s|backup|mkdir", command):
            return "Сохраняю точку отката", "work"
        return "Выполняю нужную команду", "work"
    if "send" in name or "document" in name:
        return "Доставляю результат", "deliver"
    label = _safe_fragment(str(tool_name or "").replace("_", " "), limit=42)
    return (f"Выполняю: {label}" if label else "Выполняю следующий шаг"), "work"


@dataclass
class TelegramTaskStatusState:
    """One editable Telegram status whose checklist is built from real actions."""

    title: str
    stages: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    current_stage: str = "Разбираю запрос"
    current_phase: str = "start"
    delivered_stages: set[str] = field(default_factory=set)
    last_text: str = ""
    last_sent_at: float = 0.0
    heartbeat_seconds: float = 55.0
    critical_tests_started: bool = False
    final_verdict: str | None = None
    theme: str = "auto"
    max_percent: int = 5

    def resolved_theme(self) -> str:
        return progress_theme_for_title(self.title) if self.theme == "auto" else self.theme

    def set_plan(self, steps: Iterable[str]) -> None:
        for raw in steps:
            step = _safe_fragment(raw, limit=72)
            if step and not _AGREEMENT_ONLY_RE.fullmatch(step) and step not in self.stages:
                self.stages.append(step)
        if self.current_stage == "Разбираю запрос" and self.stages:
            self.current_stage = self.stages[0]
            self.current_phase = "discover"

    def start_step(self, label: str, phase: str = "work") -> None:
        clean = _safe_fragment(label, limit=72) or "Выполняю следующий шаг"
        if clean not in {"Разбираю запрос", "Готовлю итоговый ответ", "Завершено"} and clean not in self.stages:
            self.stages.append(clean)
        self.current_stage = clean
        self.current_phase = phase if phase in _PHASE_PCT else "work"
        if self.current_phase == "verify":
            self.critical_tests_started = True

    def complete_current(self) -> None:
        if self.current_stage in self.stages:
            self.delivered_stages.add(self.current_stage)

    def mark_stage(self, stage: str) -> None:
        phase = _LEGACY_PHASE.get(stage, stage if stage in _PHASE_PCT else "work")
        label = _LEGACY_ACTION.get(stage, stage)
        self.start_step(label, phase)

    def percent_for_stage(self, stage: str | None = None) -> int:
        phase = _LEGACY_PHASE.get(stage or "", stage or self.current_phase)
        phase = phase if phase in _PHASE_PCT else self.current_phase
        percent = _PHASE_PCT.get(phase, 5)
        if self.stages:
            completed_ratio = len(self.delivered_stages) / max(1, len(self.stages))
            percent = max(percent, min(80, int(10 + completed_ratio * 70)))
        if phase != "done":
            percent = min(percent, 90)
        self.max_percent = max(self.max_percent, percent)
        return self.max_percent

    def stage_icon(self) -> str:
        if self.final_verdict in {"BLOCKED", "INCOMPLETE", "PARTIAL"}:
            return "⚠️"
        phase_order = ["start", "discover", "work", "verify", "deliver", "done"]
        phase = "done" if self.final_verdict in {"READY", "SUCCESS"} else self.current_phase
        try:
            index = phase_order.index(phase)
        except ValueError:
            index = 0
        icons = _PROGRESS_THEMES.get(self.resolved_theme(), _PROGRESS_THEMES["runner"])
        return icons[min(index, len(icons) - 1)]

    def should_emit(self, text: str, *, now: float | None = None, force: bool = False) -> bool:
        now = time.monotonic() if now is None else now
        if force:
            self.last_text = text
            self.last_sent_at = now
            return True
        if text != self.last_text:
            self.last_text = text
            self.last_sent_at = now
            return True
        if now - self.last_sent_at >= self.heartbeat_seconds:
            self.last_sent_at = now
            return True
        return False

    def render(
        self,
        *,
        stage: str | None = None,
        action: str | None = None,
        phase: str | None = None,
        verdict: str | None = None,
        blocker: str | None = None,
    ) -> str:
        if stage:
            self.mark_stage(stage)
        if action:
            self.start_step(action, phase or "work")
        if verdict:
            self.final_verdict = verdict.upper()

        pct = self.percent_for_stage()
        stage_name = self.current_stage
        if self.final_verdict in {"READY", "SUCCESS"}:
            self.current_phase = "done"
            self.delivered_stages.update(self.stages)
            self.max_percent = 100
            pct = 100
            stage_name = "Завершено"
        elif self.final_verdict == "BLOCKED":
            pct = min(pct, 90)
            stage_name = "Остановлено из-за блокера"
        elif self.final_verdict in {"INCOMPLETE", "PARTIAL"}:
            pct = min(pct, 90)
            stage_name = "Завершено частично"

        filled = max(0, min(10, round(pct / 10)))
        elapsed = max(0, int(time.monotonic() - self.started))
        minutes, seconds = divmod(elapsed, 60)
        elapsed_text = f"{minutes} мин {seconds:02d} сек" if minutes else f"{seconds} сек"
        lines = [
            f"{self.stage_icon()} {'▰' * filled}{'▱' * (10 - filled)} {pct}%",
            f"{stage_name} · {elapsed_text}",
        ]
        compact_title = _safe_fragment(self.title, limit=76)
        if compact_title and compact_title.lower() != stage_name.lower():
            lines.append(f"Задача: {compact_title}")

        visible = self.stages[-6:]
        if visible:
            lines.append("")
            for item in visible:
                marker = "✓" if item in self.delivered_stages else ("→" if item == self.current_stage else "•")
                lines.append(f"{marker} {item}")
        if blocker:
            lines.extend(["", f"Блокер: {_safe_fragment(blocker, limit=180)}"])
        return "\n".join(lines)


def should_surface_telegram_interim(text: str, *, task_status_enabled: bool) -> bool:
    """Return False for internal commentary while deterministic task status owns UX."""
    if not str(text or "").strip():
        return False
    if task_status_enabled:
        return False
    return _TECHNICAL_EVENT_RE.search(str(text)) is None


def verdict_from_text(text: str, *, failed: bool = False, partial: bool = False) -> str:
    if failed:
        return "BLOCKED"
    if partial:
        return "INCOMPLETE"
    match = re.search(r"(?im)^\s*(?:статус\s*:\s*)?(READY|PARTIAL|BLOCKED|INCOMPLETE)\b", text or "")
    if match:
        value = match.group(1).upper()
        return "INCOMPLETE" if value == "PARTIAL" else value
    return "READY"


class FinalDeliveryDeduper:
    """In-process idempotency ledger for user-visible final text/documents."""

    def __init__(self) -> None:
        self._delivered: set[tuple[str, str, str, str]] = set()

    @staticmethod
    def key(*, task_id: str, verdict: str, delivery_type: str, generation: str) -> tuple[str, str, str, str]:
        return (str(task_id or ""), str(verdict or "").upper(), str(delivery_type or ""), str(generation or ""))

    def mark_once(self, *, task_id: str, verdict: str, delivery_type: str, generation: str) -> bool:
        key = self.key(task_id=task_id, verdict=verdict, delivery_type=delivery_type, generation=generation)
        if key in self._delivered:
            return False
        self._delivered.add(key)
        return True


def completed_stage_percent(stages: Iterable[str], completed: Iterable[str], *, tests_started: bool = False) -> int:
    ordered = list(stages)
    done = set(completed)
    if not ordered:
        return 0
    count = sum(1 for stage in ordered if stage in done)
    if count >= len(ordered):
        return 100
    percent = int((count / len(ordered)) * 90)
    if percent >= 70 and not tests_started:
        return 60
    return min(percent, 90)
