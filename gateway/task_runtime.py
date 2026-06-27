"""Thin gateway integration for persistent task continuation."""

from __future__ import annotations

from dataclasses import dataclass
import re

from gateway.task_continuation import (
    TaskRecord,
    TaskStateStore,
    format_choice,
    infer_execution_contract,
    is_pause_request,
    should_track_task,
)
from gateway.task_router import TaskRoute, route_turn

_IMAGE_DEPENDENCY_RE = re.compile(
    r"\b(?:по|из|с|со)\s+(?:картинк|изображен|фото|скриншот)\w*|"
    r"\b(?:на|в)\s+скриншот\w*|"
    r"\b(?:распознай|прочитай|извлеки)\w*.*(?:картинк|изображен|фото|скриншот)\w*",
    re.I | re.S,
)
_IMAGE_CONTEXT_RE = re.compile(
    r"\[The user sent an image|vision_analyze|image_url:|media_urls?=|attachment",
    re.I,
)
_CALENDAR_CREATE_RE = re.compile(
    r"\b(?:создай|добавь|запиши|поставь|назначь|запланируй)\w*\b.*"
    r"\b(?:календар|событи|встреч)\w*\b|"
    r"\b(?:календар|событи|встреч)\w*\b.*"
    r"\b(?:создай|добавь|запиши|поставь|назначь|запланируй)\w*\b",
    re.I | re.S,
)
_DATE_RE = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|пятниц[ау]|"
    r"суббот[ау]|воскресень[еья]|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|"
    r"\d{4}-\d{2}-\d{2})\b",
    re.I,
)
_TIME_RE = re.compile(
    r"\b(?:[01]?\d|2[0-3])[:.]\d{2}\b|\b(?:в|на)\s+(?:[01]?\d|2[0-3])\b",
    re.I,
)
_PURPOSE_RE = re.compile(
    r"\b(?:с\s+[А-ЯA-ZЁ][\wё-]+|созвон|встреч[ауы]|при[её]м|дедлайн|"
    r"интервью|собеседовани|звонок|обед|ужин|тренировк)\w*\b",
    re.I,
)


def _deterministic_input_preflight(text: str, route: TaskRoute) -> tuple[list[str], str] | None:
    value = text or ""
    missing: list[str] = []
    if route.skill_names and _IMAGE_DEPENDENCY_RE.search(value) and not _IMAGE_CONTEXT_RE.search(value):
        missing.append("доступное изображение или vision-контекст")
    if "google-workspace" in route.skill_names and _CALENDAR_CREATE_RE.search(value):
        if not _DATE_RE.search(value):
            missing.append("конкретная дата события")
        if not _TIME_RE.search(value):
            missing.append("конкретное время события")
        if not _PURPOSE_RE.search(value):
            missing.append("назначение события")
    if missing:
        return missing, "deterministic input preflight blocked"
    return None


def _with_preloaded_skills(route: TaskRoute, task_id: str | None) -> tuple[TaskRoute, tuple[str, ...]]:
    if not route.skill_names:
        return route, ()
    from agent.skill_commands import build_preloaded_skills_prompt
    prompt, loaded, missing = build_preloaded_skills_prompt(list(route.skill_names), task_id=task_id)
    if missing:
        return route, tuple(missing)
    if not prompt.strip():
        return route, tuple(route.skill_names)
    combined = (route.operational_context + "\n\n" + prompt).strip()
    return TaskRoute(
        role=route.role,
        reason=route.reason + "; preloaded_skills=" + ",".join(loaded),
        toolsets=route.toolsets,
        max_iterations=route.max_iterations,
        skill_names=route.skill_names,
        skip_context_files=route.skip_context_files,
        operational_context=combined,
    ), ()


@dataclass(frozen=True)
class PreparedTaskTurn:
    message: str
    route: TaskRoute | None
    task: TaskRecord | None
    early_response: dict | None
    continued: bool


def early_response(text: str, *, status: str = "success", task_id: str | None = None,
                   role: str = "no_llm", reason: str = "deterministic task state") -> dict:
    return {
        "final_response": text,
        "messages": [],
        "api_calls": 0,
        "completed": status == "success",
        "interrupted": False,
        "partial": status != "success",
        "failed": status == "failed",
        "error": None if status == "success" else status,
        "tools": [],
        "history_offset": 0,
        "session_id": "",
        "model": "deterministic",
        "provider": "gateway",
        "selected_model": "deterministic",
        "selected_provider": "gateway",
        "fallback_used": False,
        "fallback_reason": None,
        "task_level": role,
        "routing_reason": reason,
        "selected_toolsets": [],
        "llm_total_ms": 0,
        "diagnostics": {},
        "context_length": 0,
        "task_id": task_id,
    }


def prepare_task_turn(*, message: str, platform_key: str, chat_id: str,
                      session_key: str, session_id: str, request_id: str,
                      user_config: dict, platform_toolsets: list[str] | None) -> PreparedTaskTurn:
    store = TaskStateStore()
    original = str(message or "")
    decision = store.resolve(original, platform_key, str(chat_id))
    if decision.kind == "choice":
        return PreparedTaskTurn(
            original, None, None,
            early_response(format_choice(decision.candidates), reason="continuation choice required"),
            False,
        )
    if decision.kind == "empty":
        return PreparedTaskTurn(
            original, None, None,
            early_response(
                "Незавершённых задач нет",
                reason="continuation requested without active tasks",
            ),
            False,
        )

    task = decision.task if decision.kind == "selected" else None
    continued = task is not None
    if task is not None:
        store.update(task.task_id, status="running", session_key=session_key,
                     source_session_id=session_id, last_error=None)
        message = (
            "[System continuation: resume the saved unfinished task. Preserve its role, "
            "tools, safety mode and completion contract. Do not interpret the short "
            "continuation phrase as a new task.]\n\nSaved task:\n"
            + task.original_request + "\n\nContinuation message:\n" + original
        )

    if is_pause_request(original):
        pending = store.active(platform_key, str(chat_id))
        if len(pending) == 1:
            store.update(pending[0].task_id, status="paused")
            return PreparedTaskTurn(
                original, None, pending[0],
                early_response(
                    f"Задача поставлена на паузу: {pending[0].title}",
                    status="partial", task_id=pending[0].task_id, reason="task paused",
                ),
                False,
            )

    base_text = task.original_request if task else str(message or "")
    route = route_turn(base_text, command=None, platform_key=platform_key,
                       user_config=user_config, platform_toolsets=platform_toolsets)
    if task is not None:
        allowed = set(platform_toolsets or [])
        restored = [name for name in task.toolsets
                    if name == "no_mcp" or platform_toolsets is None or name in allowed]
        route = TaskRoute(
            role=task.role,
            reason=f"continued task {task.task_id}; {route.reason}",
            toolsets=restored,
            max_iterations=route.max_iterations,
            skill_names=route.skill_names,
            skip_context_files=route.skip_context_files,
            operational_context=route.operational_context,
        )

    preflight = _deterministic_input_preflight(base_text, route)
    if preflight is not None:
        missing_inputs, preflight_reason = preflight
        if task is not None:
            store.update(task.task_id, status="blocked", last_error="missing inputs: " + ",".join(missing_inputs))
        return PreparedTaskTurn(
            str(message or ""), route, task,
            early_response(
                "BLOCKED\nНе хватает: "
                + ", ".join(missing_inputs)
                + "\nФактические действия не выполнялись",
                status="blocked", task_id=task.task_id if task else None,
                role=route.role, reason=preflight_reason,
            ),
            continued,
        )

    route, missing_skills = _with_preloaded_skills(route, task.task_id if task else None)
    if missing_skills:
        if task is not None:
            store.update(task.task_id, status="blocked", last_error="missing skills: " + ",".join(missing_skills))
        return PreparedTaskTurn(
            str(message or ""), route, task,
            early_response(
                "BLOCKED\nДля выполнения задачи недоступен skill: "
                + ", ".join(missing_skills)
                + "\nФактические действия не выполнялись",
                status="blocked", task_id=task.task_id if task else None,
                role=route.role, reason="skill preflight blocked",
            ),
            continued,
        )

    requires_execution, required = infer_execution_contract(base_text, route.role, route.toolsets)
    if task is not None:
        requires_execution, required = task.requires_execution, task.required_toolsets
    missing = sorted(set(required) - set(route.toolsets))
    if missing:
        if task is not None:
            store.update(task.task_id, status="blocked",
                         last_error="missing capabilities: " + ",".join(missing))
        return PreparedTaskTurn(
            str(message or ""), route, task,
            early_response(
                "BLOCKED\nДля выполнения задачи недоступны обязательные возможности: "
                + ", ".join(missing) + "\nФактические действия не выполнялись",
                status="blocked", task_id=task.task_id if task else None,
                role=route.role, reason="capability preflight blocked",
            ),
            continued,
        )

    if task is None and should_track_task(original, route.role, route.toolsets):
        title = " ".join(original.split())[:120] or "Задача Hermes"
        task = store.create(
            platform=platform_key,
            chat_id=str(chat_id),
            session_key=session_key,
            title=title,
            original_request=original,
            role=route.role,
            toolsets=route.toolsets,
            required_toolsets=required,
            requires_execution=requires_execution,
            source_request_id=request_id,
            source_session_id=session_id,
            status="running",
        )
    return PreparedTaskTurn(str(message or ""), route, task, None, continued)
