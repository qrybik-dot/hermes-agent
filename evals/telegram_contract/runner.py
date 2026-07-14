"""Deterministic grader for sanitized observations from real model runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Iterable, Mapping

from .corpus import CASES, EvalCase


_PUBLIC_VERDICTS = {"READY", "PARTIAL", "BLOCKED"}
_LEAK_RE = re.compile(
    r"(?i)(<tool_call>|tool\.started|iteration\s+\d+\s*/\s*\d+|receiving stream response|"
    r"waiting for non-streaming|```(?:bash|shell)\s+(?:sudo|ssh|curl)\b)"
)


@dataclass(frozen=True)
class EvalObservation:
    case_id: str
    final_text: str
    verdict: str
    response_mode: str
    llm_calls: int = 0
    tool_calls: int = 0
    skill_calls: int = 0
    status_messages: int = 0
    duplicate_deliveries: int = 0
    latency_ms: float = 0.0
    model_role: str = ""
    model: str = ""
    provider: str = ""
    prompt_hash: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "EvalObservation":
        allowed = cls.__dataclass_fields__
        return cls(**{key: value[key] for key in allowed if key in value})


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    passed: bool
    failures: tuple[str, ...]


def grade_observation(case: EvalCase, observation: EvalObservation) -> EvalResult:
    failures: list[str] = []
    text = str(observation.final_text or "")
    verdict = str(observation.verdict or "").upper()
    if verdict not in _PUBLIC_VERDICTS:
        failures.append("invalid_public_verdict")
    if verdict != case.expected_verdict:
        failures.append(f"verdict:{verdict}!={case.expected_verdict}")
    if "INCOMPLETE" in text.upper():
        failures.append("public_incomplete")
    if _LEAK_RE.search(text):
        failures.append("technical_leak")
    if case.public_verdict_required and not re.search(r"(?im)^\s*(READY|PARTIAL|BLOCKED)\b", text):
        failures.append("missing_public_verdict")
    if not case.public_verdict_required and re.search(r"(?im)^\s*(READY|PARTIAL|BLOCKED)\b", text):
        failures.append("unnecessary_public_verdict")
    if observation.response_mode != case.response_mode:
        failures.append(f"response_mode:{observation.response_mode}!={case.response_mode}")
    for field, maximum in (
        ("llm_calls", case.max_llm_calls),
        ("tool_calls", case.max_tool_calls),
        ("skill_calls", case.max_skill_calls),
        ("status_messages", case.max_status_messages),
    ):
        if int(getattr(observation, field)) > maximum:
            failures.append(f"{field}_budget")
    if observation.duplicate_deliveries != case.expected_duplicate_deliveries:
        failures.append("duplicate_delivery")
    return EvalResult(case.case_id, not failures, tuple(failures))


def run_suite(observations: Iterable[EvalObservation]) -> tuple[EvalResult, ...]:
    by_id = {item.case_id: item for item in observations}
    results: list[EvalResult] = []
    for case in CASES:
        observation = by_id.get(case.case_id)
        if observation is None:
            results.append(EvalResult(case.case_id, False, ("missing_observation",)))
        else:
            results.append(grade_observation(case, observation))
    return tuple(results)


def load_observations(path: str | Path) -> tuple[EvalObservation, ...]:
    items: list[EvalObservation] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(EvalObservation.from_mapping(json.loads(line)))
    return tuple(items)


def reference_observations() -> tuple[EvalObservation, ...]:
    """Synthetic PASS set used to test the grader itself, never as model proof."""
    items = []
    for case in CASES:
        prefix = f"{case.expected_verdict}\n" if case.public_verdict_required else ""
        items.append(
            EvalObservation(
                case.case_id,
                prefix + "Проверенный результат сценария.",
                case.expected_verdict,
                case.response_mode,
                llm_calls=case.max_llm_calls,
                tool_calls=case.max_tool_calls,
                skill_calls=case.max_skill_calls,
                status_messages=case.max_status_messages,
            )
        )
    return tuple(items)


def summary(results: Iterable[EvalResult]) -> dict[str, object]:
    values = tuple(results)
    failed = [asdict(item) for item in values if not item.passed]
    return {"total": len(values), "passed": len(values) - len(failed), "failed": failed}
