"""Deterministic Telegram task live-status and final-delivery guards."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Iterable


_TECHNICAL_EVENT_RE = re.compile(
    r"receiving stream response|waiting for non-streaming API response|pytest|\buv\b|venv|delegate_task|reviewer|subagent|"
    r"tool trace|tool\.started|tool\.completed|iteration budget|internal hypothes|self-improvement|"
    r"search_files|read_file|terminal|patch|write_file",
    re.I,
)

_STAGE_LABELS = {
    "accepted": "принятие",
    "audit": "аудит",
    "prepared": "изменения подготовлены",
    "applied": "изменения применены",
    "tests": "проверка",
    "delivery": "приёмка и доставка",
    "blocked": "блокер",
    "incomplete": "не завершено",
    "done": "завершено",
}

_STAGE_PCT = {
    "accepted": 0,
    "audit": 20,
    "prepared": 40,
    "applied": 60,
    "tests": 80,
    "delivery": 90,
    "done": 100,
}


@dataclass
class TelegramTaskStatusState:
    """Plan-stage based status formatter/throttle for one Telegram task."""

    title: str
    stages: list[str] = field(
        default_factory=lambda: ["accepted", "audit", "prepared", "applied", "tests", "delivery"]
    )
    started: float = field(default_factory=time.monotonic)
    current_stage: str = "accepted"
    delivered_stages: set[str] = field(default_factory=set)
    last_text: str = ""
    last_sent_at: float = 0.0
    heartbeat_seconds: float = 55.0
    critical_tests_started: bool = False
    final_verdict: str | None = None

    def mark_stage(self, stage: str) -> None:
        if stage in self.stages:
            idx = self.stages.index(stage)
            self.delivered_stages.update(self.stages[:idx])
            self.current_stage = stage
            if stage == "tests":
                self.critical_tests_started = True

    def percent_for_stage(self, stage: str | None = None) -> int:
        stage = stage or self.current_stage
        percent = int(_STAGE_PCT.get(stage, 0))
        if percent >= 80 and not self.critical_tests_started and stage not in {"blocked", "incomplete", "done"}:
            return 60
        if percent == 100 and stage not in {"delivery", "done"}:
            return 80 if self.critical_tests_started else 60
        return percent

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

    def render(self, *, stage: str | None = None, verdict: str | None = None, blocker: str | None = None) -> str:
        if stage:
            self.mark_stage(stage)
        if verdict:
            self.final_verdict = verdict.upper()
        stage_name = _STAGE_LABELS.get(self.current_stage, self.current_stage)
        pct = self.percent_for_stage()
        if self.final_verdict in {"READY", "SUCCESS"}:
            pct = 100
            stage_name = _STAGE_LABELS["done"]
        elif self.final_verdict in {"BLOCKED", "INCOMPLETE", "PARTIAL"}:
            stage_name = _STAGE_LABELS["blocked" if self.final_verdict == "BLOCKED" else "incomplete"]
        filled = max(0, min(10, round(pct / 10)))
        elapsed = max(0, int(time.monotonic() - self.started))
        minutes, seconds = divmod(elapsed, 60)
        elapsed_text = f"{minutes} мин {seconds:02d} сек" if minutes else f"{seconds} сек"
        lines = [
            f"⏳ В работе: {self.title}",
            f"[{'█' * filled}{'░' * (10 - filled)}] {pct}%",
            f"Идёт: {elapsed_text}",
            f"Этап: {stage_name}",
            "",
        ]
        for item in self.stages:
            marker = "✓" if item in self.delivered_stages else ("→" if item == self.current_stage else "•")
            lines.append(f"{marker} {_STAGE_LABELS.get(item, item)}")
        if blocker:
            lines.extend(["", f"Блокер: {blocker}"])
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
    percent = int((count / len(ordered)) * 100)
    if percent >= 80 and not tests_started:
        return min(percent, 60)
    if percent >= 100 and len(done) < len(ordered):
        return 80 if tests_started else 60
    return min(percent, 90) if len(done) < len(ordered) else percent
