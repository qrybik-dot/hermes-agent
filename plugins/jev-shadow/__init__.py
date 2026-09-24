"""Jev shadow decision layer for Hermes.

Observes user turns through pre_llm_call, evaluates independent semantic routing
signals in a background worker, and correlates them with actual tool/API outcomes.
It never changes routing, prompts, tools, approvals, or model selection.
Only the current user turn is sent externally after local secret/PII masking; conversation
history, tool arguments/results, and knowledge contents are never sent or logged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_PROVIDERS = {
    "typesafe": {
        "endpoint": "https://api.typesafe.ai/v1/systemone",
        "key_env": "TYPESAFE_API_KEY",
        "model": "jev-latest",
    },
    "opencode": {
        "endpoint": "https://opencode.ai/zen/v1/systemone",
        "key_env": "OPENCODE_ZEN_API_KEY",
        "model": "jev-1.13-free",
    },
}
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-shadow")
_SLOTS = threading.BoundedSemaphore(2)
_LOG_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_CLIENTS: dict[str, httpx.Client] = {}
_SEEN: dict[str, float] = {}
_HOURLY: deque[float] = deque()
_DAILY: deque[float] = deque()
_TURNS: dict[str, dict[str, Any]] = {}

_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|token|password|passwd|secret|bearer)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_URL_RE = re.compile(r"(?i)https?://[^\s<>]+")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{8,}\d)(?!\w)")
_HANDLE_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{3,}")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _home() -> Path:
    return Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))


def _log_path() -> Path:
    return _home() / "logs" / "jev-shadow.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _redact(text: str) -> str:
    """Minimize external state: mask obvious secrets plus common personal identifiers."""
    text = _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _LONG_TOKEN_RE.sub("[REDACTED_LONG_TOKEN]", text)
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _HANDLE_RE.sub("[HANDLE]", text)
    return _URL_RE.sub("[URL]", text)


def _append_log(record: dict[str, Any]) -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with _LOG_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception as exc:
        logger.debug("jev-shadow log write failed: %s", type(exc).__name__)


def _client(backend: str) -> httpx.Client:
    with _STATE_LOCK:
        client = _CLIENTS.get(backend)
        if client is None:
            timeout = _env_float("HERMES_JEV_TIMEOUT_S", 5.0, 1.0, 15.0)
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if backend == "opencode":
                # OpenCode/Cloudflare rejects Python's default client signature (1010).
                # A curl-compatible User-Agent is known-good from the VPS canary.
                headers["User-Agent"] = "curl/8.5.0"
            client = httpx.Client(
                timeout=httpx.Timeout(timeout, connect=min(3.0, timeout)),
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
                headers=headers,
            )
            _CLIENTS[backend] = client
        return client


def _read_env_file_value(path: str, name: str) -> str:
    try:
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            if raw.startswith(name + "="):
                return raw.split("=", 1)[1].strip()
    except (OSError, UnicodeError):
        pass
    return ""


def _provider_config(backend: str) -> dict[str, str] | None:
    backend = (backend or "").strip().lower()
    base = _PROVIDERS.get(backend)
    if base is None:
        return None
    key_env = str(base["key_env"])
    key = os.getenv(key_env, "").strip()
    if not key and backend == "opencode":
        key = _read_env_file_value(
            os.getenv("HERMES_JEV_OPENCODE_ENV_FILE", "/etc/hermes/opencode.env"), key_env
        )
    model_env = "HERMES_JEV_MODEL" if backend == "typesafe" else "HERMES_JEV_OPENCODE_MODEL"
    endpoint_env = "HERMES_JEV_TYPESAFE_ENDPOINT" if backend == "typesafe" else "HERMES_JEV_OPENCODE_ENDPOINT"
    return {
        "backend": backend,
        "endpoint": os.getenv(endpoint_env, str(base["endpoint"])).strip(),
        "key": key,
        "model": os.getenv(model_env, str(base["model"])).strip(),
    }


def _provider_order() -> list[str]:
    primary = os.getenv("HERMES_JEV_BACKEND", "opencode").strip().lower()
    fallback = os.getenv("HERMES_JEV_FALLBACK_BACKEND", "typesafe").strip().lower()
    order: list[str] = []
    for name in (primary, fallback):
        if name in _PROVIDERS and name not in order:
            order.append(name)
    return order or ["opencode", "typesafe"]


def _allowed_platform(platform: str) -> bool:
    raw = os.getenv("HERMES_JEV_PLATFORMS", "telegram")
    allowed = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return "*" in allowed or (platform or "").strip().lower() in allowed


def _reserve_budget() -> bool:
    now = time.time()
    max_hour = _env_int("HERMES_JEV_MAX_CALLS_PER_HOUR", 60, 1, 5000)
    max_day = _env_int("HERMES_JEV_MAX_CALLS_PER_DAY", 300, 1, 20000)
    with _STATE_LOCK:
        while _HOURLY and _HOURLY[0] < now - 3600:
            _HOURLY.popleft()
        while _DAILY and _DAILY[0] < now - 86400:
            _DAILY.popleft()
        if len(_HOURLY) >= max_hour or len(_DAILY) >= max_day:
            return False
        _HOURLY.append(now)
        _DAILY.append(now)
        return True


def _dedupe(session_id: str, user_message: str) -> bool:
    key = _hash((session_id or "") + "\0" + user_message)
    now = time.time()
    with _STATE_LOCK:
        stale = [k for k, ts in _SEEN.items() if ts < now - 300]
        for k in stale:
            _SEEN.pop(k, None)
        if key in _SEEN:
            return False
        _SEEN[key] = now
    return True


def _question_set() -> dict[str, Any]:
    """Independent semantic signals; no mutually-exclusive tool-family bucket."""
    return {
        "needs_web": {"type": "noul", "instructions": "Does completing this request require fresh public internet search, web extraction, or current external facts?"},
        "needs_files": {"type": "noul", "instructions": "Does completing this request require reading, searching, or editing local/project files or documents?"},
        "needs_knowledge": {"type": "noul", "instructions": "Does completing this request require retrieving or writing stored memory/knowledge records?"},
        "needs_server_ops": {"type": "noul", "instructions": "Does completing this request require terminal, VPS, system service, logs, configuration, deployment, or operational tooling?"},
        "needs_code_execution": {"type": "noul", "instructions": "Does completing this request require executing code, tests, data processing, or programmatic computation?"},
        "needs_browser": {"type": "noul", "instructions": "Is interactive browser or graphical UI navigation materially required to complete this request?"},
        "needs_communication": {"type": "noul", "instructions": "Does completing this request require email, calendar, messaging, or another connected communication/service action?"},
        "needs_generation": {"type": "noul", "instructions": "Does the final result require substantive free-form generated text or code rather than only deterministic execution/classification?"},
        "needs_deep_reasoning": {"type": "noul", "instructions": "Does this request require deep multi-step reasoning, ambiguity resolution, architecture, or complex planning?"},
        "deterministic_complete": {"type": "noul", "instructions": "Could deterministic code/tool execution complete this request reliably without a generative model making semantic judgments?"},
        "likely_external_side_effect": {"type": "noul", "instructions": "If carried out as requested, is the task likely to send, publish, book, deploy, mutate external state, or otherwise create an external side effect?"},
    }


def _signal(answers: dict[str, Any], name: str) -> float:
    try:
        return float((answers.get(name) or {}).get("noul") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _predicted_bundles(answers: dict[str, Any], threshold: float = 0.60) -> list[str]:
    mapping = (
        ("needs_web", "WEB"), ("needs_files", "FILES"), ("needs_knowledge", "KNOWLEDGE"),
        ("needs_server_ops", "OPS"), ("needs_code_execution", "CODE"),
        ("needs_browser", "BROWSER"), ("needs_communication", "COMMUNICATION"),
    )
    return [bundle for signal, bundle in mapping if _signal(answers, signal) >= threshold]

def _derive_model_tier(answers: dict[str, Any]) -> str:
    deterministic = _signal(answers, "deterministic_complete")
    generation = _signal(answers, "needs_generation")
    deep = _signal(answers, "needs_deep_reasoning")
    if deterministic >= 0.85 and generation <= 0.25:
        return "no_llm_candidate"
    if deep >= 0.70:
        return "strong_candidate"
    if generation >= 0.60:
        return "standard_candidate"
    return "cheap_candidate"

def _compact_answer(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, dict):
        return {}
    kind = answer.get("type")
    if kind == "choice":
        return {"type": kind, "choice": answer.get("choice"), "confidence": answer.get("confidence")}
    if kind == "score":
        return {"type": kind, "score": answer.get("score"), "confidence": answer.get("confidence")}
    if kind == "noul":
        return {"type": kind, "noul": answer.get("noul")}
    return {"type": kind}


def _turn_key(turn_id: str, session_id: str, request_hash: str = "") -> str:
    return turn_id or f"session:{_hash(session_id or request_hash or 'none')}"


def _prune_turns_locked(now: float) -> None:
    stale = [k for k, v in _TURNS.items() if float(v.get("updated_mono", now)) < now - 3600]
    for k in stale:
        _TURNS.pop(k, None)
    if len(_TURNS) > 512:
        oldest = sorted(_TURNS, key=lambda k: float(_TURNS[k].get("updated_mono", 0)))
        for k in oldest[: len(_TURNS) - 512]:
            _TURNS.pop(k, None)


def _ensure_turn(*, turn_id: str, session_id: str, task_id: str, platform: str,
                 model: str, request_hash: str, request_chars: int) -> tuple[str, dict[str, Any]]:
    now = time.monotonic()
    key = _turn_key(turn_id, session_id, request_hash)
    with _STATE_LOCK:
        _prune_turns_locked(now)
        state = _TURNS.setdefault(key, {
            "decision_id": _hash(key), "turn": _hash(turn_id or key),
            "session": _hash(session_id or "none"), "task": _hash(task_id or "none"),
            "platform": platform or "", "current_model": model or "",
            "request": request_hash, "request_chars": request_chars, "created_mono": now,
            "tools": [], "tool_families": [], "tool_errors": 0, "discovery_calls": 0,
            "tool_duration_ms": 0, "api_calls": 0, "api_duration_ms": 0,
            "input_tokens": 0, "output_tokens": 0, "prediction_ready": False,
        })
        state["updated_mono"] = now
        return key, dict(state)


def _tool_family(tool_name: str) -> str:
    name = (tool_name or "").lower()
    if name in {"tool_search", "tool_describe", "tool_call"}:
        return "DISCOVERY"
    if name.startswith("browser_") or "computer_use" in name:
        return "BROWSER"
    if name in {"web_search", "web_extract", "x_search"} or name.startswith("web_"):
        return "WEB"
    if name in {"read_file", "search_files", "write_file", "patch"}:
        return "FILES"
    if name == "memory" or "knowledge" in name or "session_search" in name:
        return "KNOWLEDGE"
    if name == "terminal" or "systemd" in name or "deploy" in name or "vps" in name:
        return "OPS"
    if name == "execute_code" or "code" in name:
        return "CODE"
    if any(x in name for x in ("gmail", "calendar", "telegram", "slack", "discord", "message", "mail")):
        return "COMMUNICATION"
    return "OTHER"


def _usage_int(usage: Any, *names: str) -> int:
    if not isinstance(usage, dict):
        return 0
    for name in names:
        value = usage.get(name)
        if isinstance(value, int):
            return value
    return 0


def _evaluate_shadow(*, user_message: str, session_id: str, task_id: str, turn_id: str,
                     platform: str, model: str, provider: str, is_first_turn: bool) -> None:
    t0 = time.perf_counter()
    request_hash = _hash(user_message)
    state_key, snapshot = _ensure_turn(
        turn_id=turn_id, session_id=session_id, task_id=task_id, platform=platform,
        model=model, request_hash=request_hash, request_chars=len(user_message),
    )
    base = {
        "schema": 2, "ts": _now_iso(), "event": "decision", "mode": "shadow",
        "decision_id": snapshot["decision_id"], "turn": snapshot["turn"],
        "session": _hash(session_id or "none"), "task": _hash(task_id or "none"),
        "request": request_hash, "request_chars": len(user_message),
        "platform": platform or "", "current_model": model or "",
        "current_provider": provider or "",
    }
    try:
        max_chars = _env_int("HERMES_JEV_MAX_STATE_CHARS", 2400, 256, 6000)
        base_payload = {
            "state": {
                "request": _redact(user_message)[:max_chars],
                "platform": platform or "unknown",
                "current_model": model or "unknown",
                "is_first_turn": bool(is_first_turn),
            },
            "questions": _question_set(),
        }
        body: dict[str, Any] | None = None
        used_backend = ""
        attempts: list[dict[str, Any]] = []
        for backend in _provider_order():
            cfg = _provider_config(backend)
            if not cfg or not cfg.get("key"):
                attempts.append({"backend": backend, "status": "missing_key"})
                continue
            payload = {**base_payload, "model": cfg["model"]}
            attempt_t0 = time.perf_counter()
            try:
                response = _client(backend).post(
                    cfg["endpoint"], headers={"Authorization": f"Bearer {cfg['key']}"}, json=payload
                )
                attempt_ms = round((time.perf_counter() - attempt_t0) * 1000)
                if response.status_code != 200:
                    attempts.append({
                        "backend": backend, "status": "http_error",
                        "http_status": response.status_code, "latency_ms": attempt_ms,
                    })
                    continue
                parsed = response.json()
            except Exception as exc:
                attempts.append({
                    "backend": backend, "status": "exception",
                    "error_type": type(exc).__name__,
                    "latency_ms": round((time.perf_counter() - attempt_t0) * 1000),
                })
                continue
            if not isinstance(parsed, dict):
                attempts.append({"backend": backend, "status": "invalid_json", "latency_ms": attempt_ms})
                continue
            body = parsed
            used_backend = backend
            attempts.append({"backend": backend, "status": "ok", "latency_ms": attempt_ms})
            break
        latency_ms = round((time.perf_counter() - t0) * 1000)
        if body is None:
            _append_log({**base, "status": "provider_error", "latency_ms": latency_ms,
                         "provider_attempts": attempts})
            return
        answers = body.get("answers") if isinstance(body, dict) else {}
        usage = body.get("usage") if isinstance(body, dict) else {}
        compact = {name: _compact_answer(answer) for name, answer in (answers or {}).items()}
        bundles = _predicted_bundles(answers or {})
        with _STATE_LOCK:
            state = _TURNS.get(state_key)
            if state is not None:
                state.update({
                    "prediction_ready": True, "decisions": compact,
                    "predicted_bundles": bundles,
                    "derived_model_tier": _derive_model_tier(answers or {}),
                    "jev_backend": used_backend,
                    "jev_latency_ms": latency_ms, "updated_mono": time.monotonic(),
                })
        _append_log({
            **base, "status": "ok", "api_model": body.get("model"),
            "jev_backend": used_backend, "provider_attempts": attempts,
            "latency_ms": latency_ms,
            "input_tokens": (usage or {}).get("input_tokens"), "decisions": compact,
            "predicted_bundles": bundles,
            "derived_model_tier": _derive_model_tier(answers or {}),
        })
    except Exception as exc:
        _append_log({
            **base, "status": "exception", "error_type": type(exc).__name__,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        })


def _release_slot(_future: Any) -> None:
    try:
        _SLOTS.release()
    except ValueError:
        pass


def on_pre_llm_call(*, user_message: Any = None, session_id: str = "",
                    task_id: str = "", turn_id: str = "", platform: str = "", model: str = "",
                    provider: str = "", is_first_turn: bool = False) -> None:
    if not _env_bool("HERMES_JEV_SHADOW_ENABLED", True):
        return
    if not _allowed_platform(platform):
        return
    if not isinstance(user_message, str) or not user_message.strip():
        return
    message = user_message.strip()
    request_hash = _hash(message)
    _ensure_turn(
        turn_id=turn_id, session_id=session_id, task_id=task_id, platform=platform,
        model=model, request_hash=request_hash, request_chars=len(message),
    )
    if not _dedupe(session_id + "\0" + turn_id, message):
        return
    if not _reserve_budget():
        _append_log({
            "schema": 2, "ts": _now_iso(), "event": "decision", "mode": "shadow",
            "status": "skipped_budget", "session": _hash(session_id or "none"),
            "request": _hash(message), "platform": platform or "",
        })
        return
    if not _SLOTS.acquire(blocking=False):
        _append_log({
            "schema": 2, "ts": _now_iso(), "event": "decision", "mode": "shadow",
            "status": "skipped_busy", "session": _hash(session_id or "none"),
            "request": _hash(message), "platform": platform or "",
        })
        return
    try:
        future = _POOL.submit(
            _evaluate_shadow, user_message=message, session_id=session_id,
            task_id=task_id, turn_id=turn_id, platform=platform, model=model, provider=provider,
            is_first_turn=is_first_turn,
        )
        future.add_done_callback(_release_slot)
    except Exception:
        _SLOTS.release()


def on_post_tool_call(*, tool_name: str = "", turn_id: str = "", session_id: str = "",
                      status: str = "", duration_ms: int = 0) -> None:
    key = _turn_key(turn_id, session_id)
    with _STATE_LOCK:
        state = _TURNS.get(key)
        if state is None:
            return
        tools = state.setdefault("tools", [])
        if tool_name and tool_name not in tools:
            tools.append(tool_name)
        family = _tool_family(tool_name)
        families = state.setdefault("tool_families", [])
        if family not in families:
            families.append(family)
        if family == "DISCOVERY":
            state["discovery_calls"] = int(state.get("discovery_calls", 0)) + 1
        if status and status != "ok":
            state["tool_errors"] = int(state.get("tool_errors", 0)) + 1
        state["tool_duration_ms"] = int(state.get("tool_duration_ms", 0)) + max(0, int(duration_ms or 0))
        state["updated_mono"] = time.monotonic()


def on_post_api_request(*, turn_id: str = "", session_id: str = "", api_call_count: int = 0,
                        api_duration: float = 0.0, usage: Any = None, model: str = "",
                        provider: str = "") -> None:
    key = _turn_key(turn_id, session_id)
    with _STATE_LOCK:
        state = _TURNS.get(key)
        if state is None:
            return
        state["api_calls"] = max(int(state.get("api_calls", 0)), int(api_call_count or 0))
        state["api_duration_ms"] = int(state.get("api_duration_ms", 0)) + max(0, round(float(api_duration or 0) * 1000))
        state["input_tokens"] = int(state.get("input_tokens", 0)) + _usage_int(usage, "input_tokens", "prompt_tokens")
        state["output_tokens"] = int(state.get("output_tokens", 0)) + _usage_int(usage, "output_tokens", "completion_tokens")
        state["final_model"] = model or state.get("current_model", "")
        state["final_provider"] = provider or ""
        state["updated_mono"] = time.monotonic()


def on_turn_complete(*, turn_id: str = "", session_id: str = "", task_id: str = "",
                   completed: bool = False, failed: bool = False, interrupted: bool = False,
                   turn_exit_reason: str = "", model: str = "", platform: str = "") -> None:
    key = _turn_key(turn_id, session_id)
    with _STATE_LOCK:
        state = _TURNS.pop(key, None)
    if state is None:
        return
    elapsed_ms = max(0, round((time.monotonic() - float(state.get("created_mono", time.monotonic()))) * 1000))
    _append_log({
        "schema": 2, "ts": _now_iso(), "event": "outcome", "mode": "shadow",
        "decision_id": state.get("decision_id"), "turn": state.get("turn"),
        "session": state.get("session"), "task": state.get("task") or _hash(task_id or "none"),
        "platform": platform or state.get("platform", ""), "request": state.get("request"),
        "prediction_ready": bool(state.get("prediction_ready")),
        "predicted_bundles": state.get("predicted_bundles", []),
        "actual_tools": state.get("tools", []),
        "actual_tool_families": state.get("tool_families", []),
        "discovery_calls": int(state.get("discovery_calls", 0)),
        "tool_errors": int(state.get("tool_errors", 0)),
        "tool_duration_ms": int(state.get("tool_duration_ms", 0)),
        "api_calls": int(state.get("api_calls", 0)),
        "api_duration_ms": int(state.get("api_duration_ms", 0)),
        "input_tokens": int(state.get("input_tokens", 0)),
        "output_tokens": int(state.get("output_tokens", 0)),
        "turn_elapsed_ms": elapsed_ms,
        "completed": bool(completed), "failed": bool(failed), "interrupted": bool(interrupted),
        "turn_exit_reason": turn_exit_reason or "",
        "final_model": model or state.get("final_model", ""),
        "final_provider": state.get("final_provider", ""),
    })


def _status() -> str:
    path = _log_path()
    counts: dict[str, int] = {}
    latencies: list[float] = []
    decision_ok = outcomes = discovery = errors = api_calls = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-1000:]
    except Exception:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        event = str(row.get("event", "unknown"))
        counts[event] = counts.get(event, 0) + 1
        if event == "decision" and row.get("status") == "ok":
            decision_ok += 1
            if isinstance(row.get("latency_ms"), (int, float)):
                latencies.append(float(row["latency_ms"]))
        if event == "outcome":
            outcomes += 1
            discovery += int(row.get("discovery_calls") or 0)
            errors += int(row.get("tool_errors") or 0)
            api_calls += int(row.get("api_calls") or 0)
    avg = round(sum(latencies) / len(latencies)) if latencies else None
    return (
        "Jev shadow v2: enabled=" + str(_env_bool("HERMES_JEV_SHADOW_ENABLED", True)).lower()
        + f", platforms={os.getenv('HERMES_JEV_PLATFORMS', 'telegram')}"
        + f", backends={'->'.join(_provider_order())}"
        + f"\nlast1000_events={counts or {}}"
        + f"\ndecision_ok={decision_ok}, outcomes={outcomes}, avg_latency_ms={avg if avg is not None else 'n/a'}"
        + f"\ndiscovery_calls={discovery}, tool_errors={errors}, api_calls={api_calls}"
        + "\nmode=observer-only; request-local tool selection disabled"
    )

def _handle_slash(raw_args: str) -> str:
    arg = (raw_args or "").strip().lower()
    if arg in {"", "status"}:
        return _status()
    return "Usage: /jev-shadow [status]"


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("on_turn_complete", on_turn_complete)
    # v2 intentionally does NOT register select_tools_for_request: this release measures only.
    ctx.register_command("jev-shadow", handler=_handle_slash,
                         description="Show Jev v2 shadow evaluator status and backend order.")
