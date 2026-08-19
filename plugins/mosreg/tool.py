from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from gateway.mosreg_totp_bridge import TotpBridgeStore
from gateway.session_context import get_session_env
from tools.registry import tool_error, tool_result

SSH_KEY = "/home/hermes/.ssh/hermes_mac_mosreg"
MAC_TARGET = "skyeng@100.112.5.120"
REMOTE_ENTRY = "/Users/skyeng/.config/mosreg/runtime/mosreg_remote_entry.py"
APPOINTMENTS_CURRENT = "/Users/skyeng/.config/mosreg/appointments.current.json"
SSH_BASE = (
    "/usr/bin/ssh",
    "-i", SSH_KEY,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "ConnectTimeout=8",
    "-o", "ConnectionAttempts=1",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=2",
    MAC_TARGET,
)
ID_RE = re.compile(r"^-?\d{1,20}$")
_MOSREG_TERMS = (
    "mosreg", "zdrav.mosreg", "esia.gosuslugi", "mosreg-gateway-totp",
    "mosreg_totp", "/dev/shm/mosreg", "auth.gui.request",
)

MOSREG_REFRESH_SCHEMA = {
    "name": "mosreg_refresh",
    "description": (
        "Refresh live read-only appointments from Mosreg/Zdrav through the authorized Mac browser. "
        "Use scope=family for the owner plus linked children; scope=self for adult profile(s). "
        "The tool handles ЕСИА and sends its own Telegram Reply challenge only when 2FA is required."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["self", "family"],
                "default": "family",
                "description": "Profiles to return: adult profile(s) only, or all linked family profiles.",
            }
        },
        "additionalProperties": False,
    },
}


def block_mosreg_fallback(tool_name: str, args: dict | None = None, **kwargs):
    """Block only command/code fallbacks that explicitly target Mosreg internals."""
    if tool_name not in {"terminal", "execute_code"}:
        return None
    raw = json.dumps(args or {}, ensure_ascii=False).lower()
    if any(term in raw for term in _MOSREG_TERMS):
        return {
            "action": "block",
            "message": "Mosreg access must use the native mosreg_refresh tool; terminal/execute_code fallback is disabled.",
        }
    return None


def _session_identity() -> tuple[str, str]:
    platform = str(get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip().lower()
    chat_id = str(get_session_env("HERMES_SESSION_CHAT_ID", "") or "").strip()
    user_id = str(get_session_env("HERMES_SESSION_USER_ID", "") or "").strip()
    if platform != "telegram":
        raise RuntimeError("TELEGRAM_SESSION_REQUIRED")
    if not ID_RE.fullmatch(chat_id) or not ID_RE.fullmatch(user_id):
        raise RuntimeError("TELEGRAM_IDENTITY_UNAVAILABLE")
    return user_id, chat_id


def _binding_active(user_id: str, chat_id: str, reply_to: str) -> bool:
    store = TotpBridgeStore()
    store.setup()
    now = int(time.time())
    for path in store.sessions.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if now > int(data.get("expires_at", 0)):
            continue
        if (
            str(data.get("telegram_user_id", "")) == user_id
            and str(data.get("chat_id", "")) == chat_id
            and str(data.get("reply_to_message_id", "")) == reply_to
        ):
            return True
    return False


async def _telegram_config():
    from gateway.config import Platform, load_gateway_config
    config = load_gateway_config()
    pconfig = config.platforms.get(Platform.TELEGRAM)
    if not pconfig or not pconfig.enabled:
        raise RuntimeError("TELEGRAM_NOT_CONFIGURED")
    return Platform.TELEGRAM, pconfig


async def _send_status(chat_id: str, text: str) -> tuple[Any, str]:
    from tools.send_message_tool import _send_to_platform
    platform, pconfig = await _telegram_config()
    result = await _send_to_platform(platform, pconfig, chat_id, text)
    if not isinstance(result, dict) or result.get("success") is not True or not result.get("message_id"):
        raise RuntimeError("TELEGRAM_CHALLENGE_SEND_FAILED")
    return pconfig, str(result["message_id"])


async def _edit_status(pconfig: Any, chat_id: str, message_id: str, text: str) -> None:
    try:
        from telegram import Bot
        from telegram.request import HTTPXRequest
        from gateway.platforms.base import resolve_proxy_url
        proxy = resolve_proxy_url("TELEGRAM_PROXY", target_hosts=["api.telegram.org"])
        if proxy:
            bot = Bot(
                token=pconfig.token,
                request=HTTPXRequest(proxy=proxy),
                get_updates_request=HTTPXRequest(proxy=proxy),
            )
        else:
            bot = Bot(token=pconfig.token)
        async with bot:
            await bot.edit_message_text(chat_id=int(chat_id), message_id=int(message_id), text=text)
    except Exception:
        # Status editing is UX-only; never fail the protected browser read because
        # Telegram rejected an edit. The original bot message remains a valid reply target.
        return


async def _ssh(argv: list[str], *, timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *SSH_BASE, *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return 124, "", "timeout"
    return proc.returncode or 0, out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")


async def _start_live_worker(user_id: str, chat_id: str, reply_to: str, pconfig: Any) -> dict:
    proc = await asyncio.create_subprocess_exec(
        *SSH_BASE,
        "python3", REMOTE_ENTRY, "--live",
        "--telegram-user-id", user_id,
        "--chat-id", chat_id,
        "--reply-to-message-id", reply_to,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    prompted = False
    deadline = time.monotonic() + 205
    while proc.returncode is None and time.monotonic() < deadline:
        if not prompted and _binding_active(user_id, chat_id, reply_to):
            prompted = True
            await _edit_status(
                pconfig, chat_id, reply_to,
                "ЕСИА запросила код. Ответьте Reply на это сообщение 6-значным кодом из SMS/Госуслуг.",
            )
        await asyncio.sleep(0.25)
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        raise RuntimeError("MOSREG_WORKER_TIMEOUT")
    out_b, err_b = await proc.communicate()
    stdout = out_b.decode("utf-8", "replace")
    stderr = err_b.decode("utf-8", "replace")
    payload = None
    for line in reversed([x.strip() for x in stdout.splitlines() if x.strip()]):
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            payload = parsed
            break
    if proc.returncode != 0 or not isinstance(payload, dict) or payload.get("ok") is not True:
        code = "MOSREG_WORKER_FAILED"
        if isinstance(payload, dict):
            candidate = str(payload.get("error") or payload.get("auth_state") or "")
            if candidate and len(candidate) <= 80:
                code = candidate
        raise RuntimeError(code)
    return {"prompted": prompted, "worker": payload, "stderr_nonempty": bool(stderr.strip())}


def _sanitize_current(data: dict, scope: str) -> dict:
    members = []
    for raw in data.get("members") or []:
        if not isinstance(raw, dict):
            continue
        if scope == "self" and str(raw.get("type")) != "adult":
            continue
        appointments = []
        for appt in raw.get("appointments") or []:
            if not isinstance(appt, dict):
                continue
            appointments.append({k: appt.get(k) for k in (
                "date", "time", "status", "speciality", "doctor", "clinic",
                "address", "display_name", "external_id",
            ) if appt.get(k) not in (None, "")})
        members.append({
            "member": raw.get("member"),
            "display_name": raw.get("display_name"),
            "type": raw.get("type"),
            "source_status": raw.get("source_status"),
            "appointments": appointments,
        })
    return {
        "schema_version": data.get("schema_version"),
        "updated_at": data.get("updated_at"),
        "source_status": data.get("source_status"),
        "authorization_status": data.get("authorization_status"),
        "scope": scope,
        "members": members,
    }


async def handle_mosreg_refresh(args: dict, **kwargs) -> str:
    scope = str((args or {}).get("scope") or "family").strip().lower()
    if scope not in {"self", "family"}:
        return tool_error("scope must be self or family")
    try:
        user_id, chat_id = _session_identity()
        pconfig, message_id = await _send_status(chat_id, "Обновляю записи Мосрег…")
        try:
            live = await _start_live_worker(user_id, chat_id, message_id, pconfig)
            rc, stdout, _stderr = await _ssh(["cat", APPOINTMENTS_CURRENT], timeout=20)
            if rc != 0:
                raise RuntimeError("MOSREG_RESULT_READ_FAILED")
            current = json.loads(stdout)
            if not isinstance(current, dict):
                raise RuntimeError("MOSREG_RESULT_INVALID")
            sanitized = _sanitize_current(current, scope)
            await _edit_status(pconfig, chat_id, message_id, "Мосрег обновлён.")
            return tool_result({
                "success": True,
                "live": True,
                "totp_prompted": bool(live.get("prompted")),
                "data": sanitized,
            })
        except Exception:
            await _edit_status(pconfig, chat_id, message_id, "Не удалось обновить Мосрег. Повторите запрос позже.")
            raise
    except Exception as exc:
        code = str(exc) if str(exc) and len(str(exc)) <= 100 else exc.__class__.__name__
        return tool_error(f"Mosreg refresh failed: {code}")
