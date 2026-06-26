"""Gateway runtime-metadata footer.

Renders a compact footer showing runtime state (model, context %, cwd) and
appends it to the FINAL message of an agent turn when enabled.  Off by default
to keep replies minimal.

Config (``~/.hermes/config.yaml``)::

    display:
      runtime_footer:
        enabled: true                       # off by default
        fields: [model, context_pct, cwd]   # order shown; drop any to hide

Per-platform overrides live under ``display.platforms.<platform>.runtime_footer``.
Users can toggle the global setting with ``/footer on|off`` from both the CLI
and any gateway platform.

The footer is appended to the final response text in ``gateway/run.py`` right
before returning the response to the adapter send path — so it only lands on
the final message a user sees, not on tool-progress updates or streaming
partials.  When streaming is on and the final text has already been delivered
piecemeal, the footer is sent as a separate trailing message via
``send_trailing_footer()``.
"""

from __future__ import annotations

import math
import os
from typing import Any, Iterable, Optional

_DEFAULT_FIELDS: tuple[str, ...] = ("model", "context_pct", "cwd")
_SEP = " · "


def _home_relative_cwd(cwd: str) -> str:
    """Return *cwd* with ``$HOME`` collapsed to ``~``.  Empty string if unset."""
    if not cwd:
        return ""
    try:
        home = os.path.expanduser("~")
        p = os.path.abspath(cwd)
        if home and (p == home or p.startswith(home + os.sep)):
            return "~" + p[len(home):]
        return p
    except Exception:
        return cwd


def _model_short(model: Optional[str]) -> str:
    """Drop ``vendor/`` prefix for readability (``openai/gpt-5.4`` → ``gpt-5.4``)."""
    if not model:
        return ""
    return model.rsplit("/", 1)[-1]


def _format_elapsed_seconds(elapsed_seconds: float | None) -> str:
    """Return a Russian elapsed-time field, or "" for invalid values."""
    if elapsed_seconds is None:
        return ""
    try:
        seconds_f = float(elapsed_seconds)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(seconds_f) or seconds_f < 0:
        return ""
    total = int(seconds_f + 0.5)
    minutes, seconds = divmod(total, 60)
    if minutes <= 0:
        return f"Время: {seconds} сек"
    return f"Время: {minutes} мин {seconds:02d} сек"


def resolve_footer_config(
    user_config: dict[str, Any] | None,
    platform_key: str | None = None,
) -> dict[str, Any]:
    """Resolve effective runtime-footer config for *platform_key*.

    Merge order (later wins):
        1. Built-in defaults (enabled=False)
        2. ``display.runtime_footer``
        3. ``display.platforms.<platform_key>.runtime_footer``
    """
    resolved = {"enabled": False, "fields": list(_DEFAULT_FIELDS)}
    cfg = (user_config or {}).get("display") or {}

    global_cfg = cfg.get("runtime_footer")
    if isinstance(global_cfg, dict):
        if "enabled" in global_cfg:
            resolved["enabled"] = bool(global_cfg.get("enabled"))
        if isinstance(global_cfg.get("fields"), list) and global_cfg["fields"]:
            resolved["fields"] = [str(f) for f in global_cfg["fields"]]

    if platform_key:
        platforms = cfg.get("platforms") or {}
        plat_cfg = platforms.get(platform_key)
        if isinstance(plat_cfg, dict):
            plat_footer = plat_cfg.get("runtime_footer")
            if isinstance(plat_footer, dict):
                if "enabled" in plat_footer:
                    resolved["enabled"] = bool(plat_footer.get("enabled"))
                if isinstance(plat_footer.get("fields"), list) and plat_footer["fields"]:
                    resolved["fields"] = [str(f) for f in plat_footer["fields"]]

    return resolved


def format_runtime_footer(
    *,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    fields: Iterable[str] = _DEFAULT_FIELDS,
    elapsed_seconds: float | None = None,
    llm_call_count: int | None = None,
    tool_call_count: int | None = None,
) -> str:
    """Render the footer line, or return "" if no fields have data.

    Fields are skipped silently when their underlying data is missing — a
    partially-populated footer is better than a line with ``?%`` or empty slots.
    """
    parts: list[str] = []
    elapsed_lines: list[str] = []
    for field in fields:
        if field == "model":
            m = _model_short(model)
            if m:
                parts.append(m)
        elif field == "context_pct":
            if context_length and context_length > 0 and context_tokens >= 0:
                pct = max(0, min(100, round((context_tokens / context_length) * 100)))
                parts.append(f"{pct}%")
        elif field == "cwd":
            rel = _home_relative_cwd(cwd or os.environ.get("TERMINAL_CWD", ""))
            if rel:
                parts.append(rel)
        elif field in {"duration", "elapsed", "elapsed_time", "time"}:
            elapsed = _format_elapsed_seconds(elapsed_seconds)
            if elapsed:
                counters: list[str] = []
                if llm_call_count is not None:
                    counters.append(f"модель: {max(0, int(llm_call_count))}")
                if tool_call_count is not None:
                    counters.append(f"инструменты: {max(0, int(tool_call_count))}")
                if counters:
                    elapsed = f"{elapsed}{_SEP}{_SEP.join(counters)}"
                elapsed_lines.append(elapsed)
        # Unknown field names are silently ignored.

    lines: list[str] = []
    if parts:
        lines.append(_SEP.join(parts))
    lines.extend(elapsed_lines)
    return "\n".join(lines)


def build_runtime_footer(
    *,
    user_config: dict[str, Any] | None,
    platform_key: str | None,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    elapsed_seconds: float | None = None,
    llm_call_count: int | None = None,
    tool_call_count: int | None = None,
) -> str:
    """Top-level entry point used by gateway/run.py.

    Returns the footer text (empty string when disabled or no data).  Callers
    append this to the final response themselves, preserving a single blank
    line of separation.
    """
    cfg = resolve_footer_config(user_config, platform_key)
    if not cfg.get("enabled"):
        return ""
    fields = cfg.get("fields") or _DEFAULT_FIELDS
    if platform_key == "telegram" and elapsed_seconds is not None:
        fields = ("model", "duration")
    return format_runtime_footer(
        model=model,
        context_tokens=context_tokens,
        context_length=context_length,
        cwd=cwd,
        fields=fields,
        elapsed_seconds=elapsed_seconds,
        llm_call_count=llm_call_count,
        tool_call_count=tool_call_count,
    )


def build_footer_line(
    *,
    user_config: dict[str, Any] | None,
    platform_key: str | None,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    elapsed_seconds: float | None = None,
    llm_call_count: int | None = None,
    tool_call_count: int | None = None,
) -> str:
    """Backward-compatible wrapper for the runtime footer builder."""
    return build_runtime_footer(
        user_config=user_config,
        platform_key=platform_key,
        model=model,
        context_tokens=context_tokens,
        context_length=context_length,
        cwd=cwd,
        elapsed_seconds=elapsed_seconds,
        llm_call_count=llm_call_count,
        tool_call_count=tool_call_count,
    )
