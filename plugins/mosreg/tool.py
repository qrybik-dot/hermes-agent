from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

from gateway.mosreg_totp_bridge import TotpBridgeStore
from gateway.session_context import get_session_env
from tools.registry import tool_result

SSH_KEY = "/home/hermes/.ssh/hermes_mac_mosreg"
MAC_TARGET = "skyeng@100.112.5.120"
GUI_UID = "502"
GUI_LABEL = "com.hermes.mosreg.auth-probe"
GUI_REQUEST = "/Users/skyeng/.config/mosreg/auth.gui.request.json"
GUI_RESULT = "/Users/skyeng/.config/mosreg/auth.gui.result.json"
AUTH_LIVE_STATUS = "/Users/skyeng/.config/mosreg/auth.live.status.json"
APPOINTMENTS_CURRENT = "/Users/skyeng/.config/mosreg/appointments.current.json"
RUN_LOCK = Path("/run/user/1002/mosreg-refresh-native.lock")
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
_SENSITIVE_MOSREG_TERMS = (
    "mosreg-gateway-totp", "/dev/shm/mosreg", "/run/user/1002/mosreg-gateway-totp",
    "session_secret", "session_key",
)

_ERROR_CATALOG: dict[str, dict[str, Any]] = {
    "MOSREG_BUSY": {
        "stage": "coordination", "retryable": True,
        "diagnosis": "Уже выполняется другой запрос Мосрег.",
        "suggested_actions": ["Дождаться завершения текущего запроса и повторить один раз."],
    },
    "MAC_UNAVAILABLE": {
        "stage": "mac_transport", "retryable": True,
        "diagnosis": "Mac недоступен по управляющему SSH-каналу.",
        "suggested_actions": ["Проверить, что Mac включён и Tailscale Connected; затем повторить запрос."],
    },
    "GUI_REQUEST_WRITE_FAILED": {
        "stage": "mac_dispatch", "retryable": True,
        "diagnosis": "Не удалось атомарно передать запрос GUI worker на Mac.",
        "suggested_actions": ["Повторить запрос один раз; при повторе проверить GUI broker на Mac."],
    },
    "GUI_KICKSTART_FAILED": {
        "stage": "mac_dispatch", "retryable": True,
        "diagnosis": "LaunchAgent GUI worker не запустился по on-demand запросу.",
        "suggested_actions": ["Проверить loaded-состояние com.hermes.mosreg.auth-probe и повторить."],
    },
    "GUI_WORKER_TIMEOUT": {
        "stage": "mac_worker", "retryable": True,
        "diagnosis": "GUI worker не вернул результат в допустимое время.",
        "suggested_actions": ["Проверить auth.live.status и состояние браузера; не запрашивать новый код без нового challenge."],
    },
    "KEYCHAIN_ACCESS_DENIED": {
        "stage": "credentials", "retryable": False,
        "diagnosis": "GUI worker видит запись Keychain, но macOS не разрешила прочитать её значение.",
        "suggested_actions": ["Разрешить доступ к записи Keychain локально на Mac и повторить запрос."],
    },
    "KEYCHAIN_ITEM_MISSING": {
        "stage": "credentials", "retryable": False,
        "diagnosis": "Нужная запись ЕСИА отсутствует в macOS Keychain.",
        "suggested_actions": ["Восстановить mosreg-auth / esia.login и esia.password локально на Mac."],
    },
    "PLAYWRIGHT_UNAVAILABLE": {
        "stage": "browser_preflight", "retryable": False,
        "diagnosis": "Локальный browser worker не может загрузить Playwright.",
        "suggested_actions": ["Восстановить локальный Playwright runtime на Mac и повторить synthetic test."],
    },
    "TOTP_BRIDGE_UNAVAILABLE": {
        "stage": "totp_transport", "retryable": True,
        "diagnosis": "Mac не видит локальный TOTP bridge после попытки самовосстановления tunnel.",
        "suggested_actions": ["Проверить Mac tunnel supervisor и VPS bridge 127.0.0.1:9119."],
    },
    "TOTP_TIMEOUT": {
        "stage": "totp", "retryable": True,
        "diagnosis": "Реальный 2FA challenge был создан, но код не поступил в его окно действия.",
        "suggested_actions": ["Запустить новый refresh и ответить Reply только на новое сообщение challenge."],
    },
    "TOTP_LIMIT": {
        "stage": "totp", "retryable": True,
        "diagnosis": "ЕСИА не приняла код либо исчерпан лимит попыток текущего challenge.",
        "suggested_actions": ["Запустить новую сессию входа и использовать новый код."],
    },
    "CAPTCHA_REQUIRED": {
        "stage": "esia", "retryable": False,
        "diagnosis": "ЕСИА потребовала CAPTCHA; автоматический read-only вход остановлен.",
        "suggested_actions": ["Пройти CAPTCHA вручную на Mac, затем повторить refresh."],
    },
    "ACCOUNT_RECOVERY_REQUIRED": {
        "stage": "esia", "retryable": False,
        "diagnosis": "ЕСИА требует восстановление/подтверждение аккаунта.",
        "suggested_actions": ["Завершить восстановление вручную и повторить refresh."],
    },
    "BROWSER_READ_FAILED": {
        "stage": "mosreg_read", "retryable": True,
        "diagnosis": "Авторизация прошла, но read-only извлечение данных Мосрег не завершилось.",
        "suggested_actions": ["Повторить один раз; при повторе диагностировать текущую страницу/API schema."],
    },
    "BROWSER_TIMEOUT": {
        "stage": "browser", "retryable": True,
        "diagnosis": "Browser worker превысил внутренний timeout на текущем этапе.",
        "suggested_actions": ["Повторить один раз; при повторе использовать last_state как точку диагностики."],
    },
    "NO_PROFILES": {
        "stage": "mosreg_read", "retryable": False,
        "diagnosis": "Мосрег не вернул доступных профилей для текущей учётной записи.",
        "suggested_actions": ["Проверить привязанные профили в Мосрег вручную."],
    },
    "MOSREG_RESULT_READ_FAILED": {
        "stage": "result", "retryable": True,
        "diagnosis": "GUI worker завершился, но sanitized result не удалось прочитать с Mac.",
        "suggested_actions": ["Проверить appointments.current.json и повторить запрос."],
    },
    "MOSREG_RESULT_INVALID": {
        "stage": "result", "retryable": False,
        "diagnosis": "Sanitized result имеет неожиданную структуру.",
        "suggested_actions": ["Проверить reader schema до следующего live запуска."],
    },
    "MOSREG_WORKER_FAILED": {
        "stage": "mac_worker", "retryable": True,
        "diagnosis": "GUI worker завершился с ошибкой без более точного безопасного кода.",
        "suggested_actions": ["Использовать stage/error_code из auth.live.status; не придумывать причину."],
    },
}

MOSREG_REFRESH_SCHEMA = {
    "name": "mosreg_refresh",
    "description": (
        "Refresh current read-only Mosreg/Zdrav appointments using the authorized Mac GUI worker. "
        "Call this tool immediately when fresh Mosreg data is requested. Do NOT tell the user that 2FA is required "
        "before this tool itself creates and displays a real Telegram Reply challenge. On failure, report only the "
        "returned error_code/stage/diagnosis and current evidence; never infer tunnel, token, password or expiry causes. "
        "Retry automatically at most once and only when retryable=true. scope=family includes linked children."
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
    """Block secret-bearing Mosreg fallbacks, not ordinary diagnostics."""
    if tool_name not in {"terminal", "execute_code"}:
        return None
    raw = json.dumps(args or {}, ensure_ascii=False).lower()
    keychain_value_read = (
        "security" in raw and "find-generic-password" in raw
        and (" -w" in raw or '"-w"' in raw)
    )
    if any(term in raw for term in _SENSITIVE_MOSREG_TERMS) or keychain_value_read:
        return {
            "action": "block",
            "message": (
                "Raw Mosreg 2FA/session storage and Keychain values are protected. "
                "Use non-secret status/log/health diagnostics and reversible recovery instead."
            ),
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


def _acquire_run_lock():
    RUN_LOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fh = RUN_LOCK.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def _normalise_code(raw: Any) -> str:
    value = str(raw or "").strip()
    aliases = {
        "keychain_item_missing": "KEYCHAIN_ITEM_MISSING",
        "FAILED": "MOSREG_WORKER_FAILED",
        "BUSY": "MOSREG_BUSY",
        "TIMEOUT": "BROWSER_TIMEOUT",
    }
    if value in aliases:
        return aliases[value]
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", value):
        return value
    return "MOSREG_WORKER_FAILED"


def _failure(code: str, *, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    code = _normalise_code(code)
    meta = _ERROR_CATALOG.get(code) or {
        "stage": "unknown",
        "retryable": False,
        "diagnosis": "Mosreg worker вернул неизвестный безопасный код ошибки.",
        "suggested_actions": ["Сообщить error_code и stage без предположений; затем выполнить узкую диагностику."],
    }
    return {
        "success": False,
        "error_code": code,
        "stage": meta["stage"],
        "diagnosis": meta["diagnosis"],
        "retryable": bool(meta["retryable"]),
        "suggested_actions": list(meta["suggested_actions"]),
        "evidence": evidence or {},
        "response_policy": (
            "Treat this as current evidence. Report it first; if further diagnosis/recovery is useful, "
            "use targeted non-secret status/log/health checks and reversible actions. Change the diagnosis "
            "only when new evidence supports it; never read raw 2FA/session/Keychain values."
        ),
    }


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
            bot = Bot(token=pconfig.token, request=HTTPXRequest(proxy=proxy), get_updates_request=HTTPXRequest(proxy=proxy))
        else:
            bot = Bot(token=pconfig.token)
        async with bot:
            await bot.edit_message_text(chat_id=int(chat_id), message_id=int(message_id), text=text)
    except Exception:
        return


async def _ssh(argv: list[str], *, timeout: float, stdin_text: str | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *SSH_BASE, *argv,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(None if stdin_text is None else stdin_text.encode("utf-8")),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return 124, "", "timeout"
    return proc.returncode or 0, out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")


async def _write_gui_request(payload: dict[str, Any]) -> tuple[int, str]:
    script = (
        "import os,sys; p=sys.argv[1]; raw=sys.stdin.buffer.read(); "
        "tmp=p+\".tmp\"; f=open(tmp,\"wb\"); f.write(raw); f.flush(); os.fsync(f.fileno()); f.close(); "
        "os.chmod(tmp,0o600); os.replace(tmp,p)"
    )
    rc, _, stderr = await _ssh(
        ["/usr/bin/python3", "-c", script, GUI_REQUEST],
        timeout=12,
        stdin_text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
    )
    return rc, stderr


async def _read_json(path: str, *, timeout: float = 6) -> dict[str, Any] | None:
    rc, stdout, _ = await _ssh(["/bin/cat", path], timeout=timeout)
    if rc != 0:
        return None
    try:
        data = json.loads(stdout)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


async def _mac_status_summary() -> dict[str, Any]:
    data = await _read_json(AUTH_LIVE_STATUS)
    if not data:
        return {}
    return {k: data.get(k) for k in ("state", "last_state", "error_code", "host", "source_status") if data.get(k) not in (None, "")}


async def _run_gui_worker(user_id: str, chat_id: str, reply_to: str, pconfig: Any) -> tuple[dict[str, Any], bool]:
    request_id = "native_" + secrets.token_hex(12)
    payload = {
        "schema": 1,
        "request_id": request_id,
        "mode": "live",
        "telegram_user_id": user_id,
        "chat_id": chat_id,
        "reply_to_message_id": reply_to,
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + 240,
    }
    write_rc, write_err = await _write_gui_request(payload)
    if write_rc != 0:
        code = "MAC_UNAVAILABLE" if write_rc in {124, 255} else "GUI_REQUEST_WRITE_FAILED"
        return _failure(code, evidence={"ssh_exit_code": write_rc, "stderr_present": bool(write_err.strip())}), False
    rc, _, stderr = await _ssh(
        ["/bin/launchctl", "kickstart", "-k", f"gui/{GUI_UID}/{GUI_LABEL}"], timeout=15
    )
    if rc != 0:
        code = "MAC_UNAVAILABLE" if rc in {124, 255} else "GUI_KICKSTART_FAILED"
        return _failure(code, evidence={"ssh_exit_code": rc, "stderr_present": bool(stderr.strip())}), False

    prompted = False
    deadline = time.monotonic() + 205
    last_result: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        if not prompted and _binding_active(user_id, chat_id, reply_to):
            prompted = True
            await _edit_status(
                pconfig, chat_id, reply_to,
                "ЕСИА запросила 2FA. Ответьте Reply на это сообщение 6-значным кодом из SMS/Госуслуг.",
            )
        result = await _read_json(GUI_RESULT, timeout=4)
        if result and str(result.get("request_id")) == request_id:
            last_result = result
            break
        await asyncio.sleep(0.5)

    if last_result is None:
        probe_rc, _, _ = await _ssh(["/usr/bin/true"], timeout=10)
        code = "MAC_UNAVAILABLE" if probe_rc in {124, 255} else "GUI_WORKER_TIMEOUT"
        return _failure(code, evidence={"challenge_created": prompted, "ssh_exit_code": probe_rc}), prompted
    nested = last_result.get("result") if isinstance(last_result.get("result"), dict) else {}
    if last_result.get("ok") is True and nested.get("ok") is True:
        return {"success": True, "worker": nested}, prompted

    status = await _mac_status_summary()
    raw_code = (
        nested.get("error_code") or nested.get("error") or status.get("error_code")
        or last_result.get("error") or nested.get("auth_state")
    )
    code = _normalise_code(raw_code)
    evidence = {
        "challenge_created": prompted,
        "worker_exit_code": last_result.get("exit_code"),
        "worker_duration_ms": last_result.get("duration_ms"),
        **status,
    }
    return _failure(code, evidence=evidence), prompted


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
        return tool_result(_failure("MOSREG_WORKER_FAILED", evidence={"invalid_scope": True}))
    run_lock = _acquire_run_lock()
    if run_lock is None:
        return tool_result(_failure("MOSREG_BUSY"))
    try:
        try:
            user_id, chat_id = _session_identity()
        except Exception as exc:
            return tool_result(_failure(str(exc)))
        try:
            pconfig, message_id = await _send_status(chat_id, "Проверяю Мосрег и состояние авторизации…")
        except Exception as exc:
            return tool_result(_failure(str(exc)))

        outcome, prompted = await _run_gui_worker(user_id, chat_id, message_id, pconfig)
        if outcome.get("success") is not True:
            code = outcome.get("error_code", "MOSREG_WORKER_FAILED")
            stage = outcome.get("stage", "unknown")
            await _edit_status(
                pconfig, chat_id, message_id,
                f"Мосрег не обновлён: {code} (этап: {stage}).",
            )
            return tool_result(outcome)

        rc, stdout, stderr = await _ssh(["/bin/cat", APPOINTMENTS_CURRENT], timeout=20)
        if rc != 0:
            failure = _failure(
                "MOSREG_RESULT_READ_FAILED",
                evidence={"ssh_exit_code": rc, "stderr_present": bool(stderr.strip())},
            )
            await _edit_status(pconfig, chat_id, message_id, "Мосрег: данные обновлены, но результат не удалось прочитать.")
            return tool_result(failure)
        try:
            current = json.loads(stdout)
        except Exception:
            current = None
        if not isinstance(current, dict):
            failure = _failure("MOSREG_RESULT_INVALID")
            await _edit_status(pconfig, chat_id, message_id, "Мосрег: получен некорректный sanitized result.")
            return tool_result(failure)
        sanitized = _sanitize_current(current, scope)
        await _edit_status(pconfig, chat_id, message_id, "Мосрег обновлён.")
        return tool_result({
            "success": True,
            "live": True,
            "challenge_created": prompted,
            "data": sanitized,
            "response_policy": "Summarize the returned current data; do not add unverified infrastructure claims.",
        })
    finally:
        try:
            fcntl.flock(run_lock.fileno(), fcntl.LOCK_UN)
        finally:
            run_lock.close()
