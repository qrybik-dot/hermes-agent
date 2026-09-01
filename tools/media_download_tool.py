"""Deterministic public-media download and Telegram delivery fast path.

The model chooses *whether* the user wants a video. This tool owns everything
after that decision: bounded yt-dlp execution, truthful progress, delivery to
the current Telegram chat, and cleanup after a confirmed send or failure.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit
import uuid

from gateway.session_context import get_session_env
from tools.registry import registry, tool_error
from tools.url_safety import is_safe_url, normalize_url_for_request

logger = logging.getLogger(__name__)

_TEMP_ROOT = Path("/tmp/hermes-media")
_ORPHAN_MAX_AGE_SECONDS = 6 * 60 * 60
_DOWNLOAD_TIMEOUT_SECONDS = 180
_PROGRESS_DELAY_SECONDS = 10.0
_PROGRESS_UPDATE_SECONDS = 1.5
_MAX_TELEGRAM_BYTES = 49 * 1024 * 1024
_PROGRESS_PREFIX = "__HERMES_PROGRESS__|"
_FILE_PREFIX = "__HERMES_FILE__|"
_META_PREFIX = "__HERMES_META__|"
_REQUEST_DIR_RE = re.compile(r"^request-[0-9a-f]{32}$")


@dataclass(frozen=True)
class DownloadResult:
    success: bool
    file_path: Optional[Path] = None
    extractor: str = ""
    bytes_downloaded: int = 0
    error_code: str = ""
    user_message: str = ""


@dataclass(frozen=True)
class TelegramContext:
    adapter: Any
    chat_id: str
    thread_id: Optional[str]
    reply_to_message_id: Optional[str]

    @property
    def metadata(self) -> Optional[dict[str, Any]]:
        value: dict[str, Any] = {}
        if self.thread_id:
            value["thread_id"] = self.thread_id
        if self.reply_to_message_id:
            value["telegram_reply_to_message_id"] = self.reply_to_message_id
        return value or None


class _StatusMessage:
    """One editable status bubble, created only after the delay threshold."""

    def __init__(self, context: TelegramContext):
        self.context = context
        self.message_id: Optional[str] = None
        self._last_update = 0.0
        self._lock = asyncio.Lock()

    async def update(self, content: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_update < _PROGRESS_UPDATE_SECONDS:
            return
        async with self._lock:
            now = time.monotonic()
            if not force and now - self._last_update < _PROGRESS_UPDATE_SECONDS:
                return
            try:
                if self.message_id is None:
                    result = await self.context.adapter.send(
                        self.context.chat_id,
                        content,
                        metadata=self.context.metadata,
                    )
                else:
                    result = await self.context.adapter.edit_message(
                        self.context.chat_id,
                        self.message_id,
                        content,
                        finalize=True,
                        metadata=self.context.metadata,
                    )
                if getattr(result, "success", False) and getattr(
                    result, "message_id", None
                ):
                    self.message_id = str(result.message_id)
            except Exception:
                logger.debug("media_download status update failed", exc_info=True)
            finally:
                self._last_update = now

    async def delete(self) -> None:
        async with self._lock:
            if self.message_id is None:
                return
            try:
                await self.context.adapter.delete_message(
                    self.context.chat_id, self.message_id
                )
            except Exception:
                logger.debug("media_download status cleanup failed", exc_info=True)
            finally:
                self.message_id = None


def _dependency_available() -> bool:
    return (
        sys.platform.startswith("linux")
        and importlib.util.find_spec("yt_dlp") is not None
        and shutil.which("ffmpeg") is not None
    )


def check_media_download_requirements() -> bool:
    """Expose the tool only when the pinned downloader is already installed."""
    return _dependency_available()


def _ensure_temp_root() -> Path:
    try:
        stat = _TEMP_ROOT.lstat()
    except FileNotFoundError:
        _TEMP_ROOT.mkdir(mode=0o700, parents=False, exist_ok=False)
        stat = _TEMP_ROOT.lstat()
    if _TEMP_ROOT.is_symlink() or not _TEMP_ROOT.is_dir():
        raise RuntimeError("unsafe media temporary root")
    if stat.st_uid != os.getuid():
        raise RuntimeError("media temporary root has the wrong owner")
    if stat.st_mode & 0o077:
        _TEMP_ROOT.chmod(0o700)
    return _TEMP_ROOT


def _cleanup_stale_orphans(now: Optional[float] = None) -> int:
    """Remove only old request directories owned by this tool."""
    try:
        root = _ensure_temp_root()
    except Exception:
        return 0
    cutoff = (time.time() if now is None else now) - _ORPHAN_MAX_AGE_SECONDS
    removed = 0
    for child in root.iterdir():
        try:
            if (
                not _REQUEST_DIR_RE.fullmatch(child.name)
                or child.is_symlink()
                or not child.is_dir()
                or child.stat().st_mtime >= cutoff
            ):
                continue
            shutil.rmtree(child)
            if not child.exists():
                removed += 1
        except OSError:
            logger.debug("media_download orphan cleanup skipped %s", child.name)
    return removed


def _build_downloader_command(url: str, output_template: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-plugin-dirs",
        "--no-remote-components",
        "--use-extractors",
        "default,-generic",
        "--no-playlist",
        "--playlist-items",
        "1",
        "--abort-on-error",
        "--socket-timeout",
        "10",
        "--retries",
        "0",
        "--fragment-retries",
        "0",
        "--extractor-retries",
        "0",
        "--file-access-retries",
        "0",
        "--sleep-requests",
        "0",
        "--sleep-interval",
        "0",
        "--max-filesize",
        "49M",
        "--newline",
        "--progress",
        "--quiet",
        "--no-warnings",
        "--no-simulate",
        "--progress-template",
        _PROGRESS_PREFIX
        + "%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
        + "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s",
        "--print",
        _META_PREFIX + "%(extractor_key)s",
        "--print",
        "after_move:" + _FILE_PREFIX + "%(filepath)s",
        "--format",
        "bv*[height<=720]+ba/b[height<=720]/b",
        "--merge-output-format",
        "mp4",
        "--remux-video",
        "mp4",
        "--output",
        str(output_template),
        "--",
        url,
    ]


def _number(value: str) -> Optional[float]:
    if value in {"", "NA", "None", "null"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_progress_line(line: str) -> Optional[dict[str, Optional[float]]]:
    if not line.startswith(_PROGRESS_PREFIX):
        return None
    parts = line[len(_PROGRESS_PREFIX) :].strip().split("|")
    if len(parts) != 5:
        return None
    downloaded, total, estimate, speed, eta = (_number(part) for part in parts)
    return {
        "downloaded": downloaded,
        "total": total or estimate,
        "speed": speed,
        "eta": eta,
    }


def _format_bytes(value: Optional[float]) -> str:
    if not value:
        return "0 МБ"
    return f"{value / (1024 * 1024):.1f} МБ"


def _format_download_status(progress: Optional[dict[str, Optional[float]]]) -> str:
    if not progress or not progress.get("downloaded"):
        return "⬇️ Скачиваю видео…"
    downloaded = progress["downloaded"]
    total = progress.get("total")
    if total and total > 0:
        percent = min(100, max(0, round(100 * downloaded / total)))
        filled = min(10, percent // 10)
        bar = "█" * filled + "░" * (10 - filled)
        suffix = f"{percent}% · {_format_bytes(downloaded)} / {_format_bytes(total)}"
        eta = progress.get("eta")
        if eta is not None and eta >= 0:
            suffix += f" · ~{round(eta)} сек"
        return f"⬇️ Скачиваю видео\n{bar} {suffix}"
    return f"⬇️ Скачиваю видео · {_format_bytes(downloaded)}"


def _classify_failure(output: str, *, timed_out: bool = False) -> tuple[str, str]:
    if timed_out:
        return "timeout", "Источник отвечает слишком долго. Попробуйте позже."
    text = output.casefold()
    if "drm" in text or "copyright protected" in text:
        return "drm", "Это видео защищено и не может быть скачано."
    if "larger than max-filesize" in text or "file is larger" in text:
        return "too_large", "Видео слишком большое для отправки ботом в Telegram."
    if "unsupported url" in text or "no suitable extractor" in text:
        return "unsupported", "Эта ссылка пока не поддерживается."
    if "429" in text or "too many requests" in text or "rate limit" in text:
        return "rate_limited", "Источник временно ограничил загрузку. Попробуйте позже."
    if any(
        marker in text
        for marker in (
            "login required",
            "sign in",
            "private video",
            "not available to everyone",
            "cookies",
            "authentication required",
            "http error 401",
            "http error 403",
        )
    ):
        return "auth_required", "Не получилось получить видео: источник требует авторизацию."
    return "source_unavailable", "Не получилось получить видео по этой ссылке."


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
    except asyncio.TimeoutError:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()


async def _run_downloader(
    url: str,
    request_dir: Path,
    status: _StatusMessage,
    started: float,
) -> DownloadResult:
    command = _build_downloader_command(url, request_dir / "media.%(ext)s")
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=(os.name == "posix"),
    )
    output_tail: list[str] = []
    file_path: Optional[Path] = None
    extractor = ""
    latest_progress: Optional[dict[str, Optional[float]]] = None
    timed_out = False

    try:
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= _DOWNLOAD_TIMEOUT_SECONDS:
                timed_out = True
                break
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                raw = b""
            if raw:
                line = raw.decode("utf-8", errors="replace").strip()
                output_tail.append(line)
                output_tail = output_tail[-40:]
                progress = _parse_progress_line(line)
                if progress is not None:
                    latest_progress = progress
                elif line.startswith(_FILE_PREFIX):
                    file_path = Path(line[len(_FILE_PREFIX) :].strip())
                elif line.startswith(_META_PREFIX):
                    extractor = line[len(_META_PREFIX) :].strip()[:80]
            if time.monotonic() - started >= _PROGRESS_DELAY_SECONDS:
                await status.update(_format_download_status(latest_progress))
            if proc.returncode is not None:
                break
            if not raw and proc.stdout.at_eof():
                await proc.wait()
                break
        if timed_out:
            await _terminate_process(proc)
        elif proc.returncode is None:
            await proc.wait()
    finally:
        if proc.returncode is None:
            await _terminate_process(proc)

    combined = "\n".join(output_tail)
    if timed_out or proc.returncode != 0:
        code, message = _classify_failure(combined, timed_out=timed_out)
        return DownloadResult(False, extractor=extractor, error_code=code, user_message=message)

    if (
        file_path is None
        or file_path.is_symlink()
        or request_dir.resolve() not in file_path.resolve().parents
        or not file_path.is_file()
    ):
        return DownloadResult(
            False,
            extractor=extractor,
            error_code="missing_output",
            user_message="Не получилось получить видео по этой ссылке.",
        )
    size = file_path.stat().st_size
    if size <= 0:
        return DownloadResult(False, extractor=extractor, error_code="empty_output", user_message="Источник не вернул видео.")
    if size > _MAX_TELEGRAM_BYTES:
        return DownloadResult(False, extractor=extractor, bytes_downloaded=size, error_code="too_large", user_message="Видео слишком большое для отправки ботом в Telegram.")
    return DownloadResult(True, file_path=file_path, extractor=extractor, bytes_downloaded=size)


def _get_telegram_context() -> Optional[TelegramContext]:
    if get_session_env("HERMES_SESSION_PLATFORM", "").strip().lower() != "telegram":
        return None
    chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "").strip()
    if not chat_id:
        return None
    try:
        from gateway.run import _gateway_runner_ref

        runner = _gateway_runner_ref()
    except Exception:
        runner = None
    if runner is None:
        return None
    adapter = next(
        (
            value
            for key, value in runner.adapters.items()
            if getattr(key, "value", str(key)).lower() == "telegram"
        ),
        None,
    )
    if adapter is None:
        return None
    return TelegramContext(
        adapter=adapter,
        chat_id=chat_id,
        thread_id=get_session_env("HERMES_SESSION_THREAD_ID", "").strip() or None,
        reply_to_message_id=get_session_env("HERMES_SESSION_MESSAGE_ID", "").strip() or None,
    )


async def _upload_status_after_delay(status: _StatusMessage, started: float) -> None:
    await asyncio.sleep(max(0.0, _PROGRESS_DELAY_SECONDS - (time.monotonic() - started)))
    await status.update("📤 Отправляю в Telegram…", force=True)


def _saved_destination(request_id: str) -> Path:
    target_dir = Path.home() / "downloads"
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target_dir / f"media-{request_id}.mp4"


async def _handle_media_download(args: dict[str, Any], **_: Any) -> str:
    url = normalize_url_for_request(str(args.get("url") or "").strip())
    keep_local_copy = args.get("keep_local_copy", False) is True
    if not url or urlsplit(url).scheme not in {"http", "https"} or not is_safe_url(url):
        return tool_error("Нужна безопасная публичная ссылка http/https.", error_code="invalid_url")
    if not _dependency_available():
        return tool_error("Скачивание видео сейчас недоступно.", error_code="dependency_unavailable")

    context = _get_telegram_context()
    if context is None:
        return tool_error("Этот быстрый путь работает только в текущем Telegram-чате.", error_code="telegram_context_required")

    request_id = uuid.uuid4().hex
    _cleanup_stale_orphans()
    root = _ensure_temp_root()
    request_dir = root / f"request-{request_id}"
    request_dir.mkdir(mode=0o700, exist_ok=False)
    status = _StatusMessage(context)
    started = time.monotonic()
    domain = (urlsplit(url).hostname or "unknown").lower()[:120]
    result: Optional[DownloadResult] = None
    sent_message_id: Optional[str] = None
    saved_path: Optional[Path] = None
    payload: dict[str, Any] = {}

    try:
        result = await _run_downloader(url, request_dir, status, started)
        if not result.success or result.file_path is None:
            payload = {
                "success": False,
                "sent": False,
                "error_code": result.error_code,
                "message": result.user_message,
            }
        else:
            upload_status = asyncio.create_task(
                _upload_status_after_delay(status, started)
            )
            try:
                send_result = await context.adapter.send_video(
                    context.chat_id,
                    str(result.file_path),
                    reply_to=context.reply_to_message_id,
                    metadata=context.metadata,
                )
            finally:
                upload_status.cancel()
                try:
                    await upload_status
                except asyncio.CancelledError:
                    pass

            if not getattr(send_result, "success", False) or not getattr(
                send_result, "message_id", None
            ):
                payload = {
                    "success": False,
                    "sent": False,
                    "error_code": "telegram_send_failed",
                    "message": "Не получилось отправить видео в Telegram.",
                }
            else:
                sent_message_id = str(send_result.message_id)
                save_error = False
                if keep_local_copy:
                    try:
                        saved_path = _saved_destination(request_id)
                        shutil.move(str(result.file_path), saved_path)
                    except OSError:
                        save_error = True
                        saved_path = None
                        logger.warning(
                            "media_download save-copy failed request_id=%s",
                            request_id,
                        )

                elapsed = time.monotonic() - started
                payload = {
                    "success": not save_error,
                    "sent": True,
                    "message_id": sent_message_id,
                    "bytes": result.bytes_downloaded,
                    "duration_seconds": round(elapsed, 2),
                    "saved": bool(saved_path),
                    "saved_path": str(saved_path) if saved_path else None,
                    "next_response": (
                        "Видео отправлено, но сохранить копию не удалось."
                        if save_error
                        else "NO_REPLY"
                    ),
                }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "media_download failed request_id=%s domain=%s error_class=%s",
            request_id,
            domain,
            type(exc).__name__,
        )
        payload = {
            "success": False,
            "sent": bool(sent_message_id),
            "error_code": "internal_failure",
            "message": (
                "Видео отправлено, но завершить обработку не удалось."
                if sent_message_id
                else "Не получилось получить видео по этой ссылке."
            ),
            "next_response": (
                "Видео отправлено, но завершить обработку не удалось."
                if sent_message_id
                else None
            ),
        }
    finally:
        await status.delete()
        try:
            shutil.rmtree(request_dir)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("media_download cleanup failed request_id=%s", request_id)
        cleanup_ok = not request_dir.exists()
        payload["cleanup"] = cleanup_ok
        if not cleanup_ok:
            logger.error("media_download cleanup read-back failed request_id=%s", request_id)
            payload["success"] = False
            if payload.get("sent"):
                payload["next_response"] = (
                    "Видео отправлено, но временный файл не удалось удалить."
                )
            else:
                payload["message"] = (
                    "Не получилось безопасно завершить обработку видео."
                )

    elapsed = time.monotonic() - started
    if payload.get("success"):
        logger.info(
            "media_download success request_id=%s domain=%s extractor=%s bytes=%d duration=%.2f sent_message_id=%s kept=%s cleanup=%s",
            request_id,
            domain,
            result.extractor if result else "unknown",
            result.bytes_downloaded if result else 0,
            elapsed,
            sent_message_id,
            bool(saved_path),
            payload.get("cleanup"),
        )
    else:
        logger.info(
            "media_download failure request_id=%s domain=%s extractor=%s error_code=%s sent=%s cleanup=%s",
            request_id,
            domain,
            result.extractor if result else "unknown",
            payload.get("error_code", "post_send_failure"),
            payload.get("sent", False),
            payload.get("cleanup"),
        )
    return json.dumps(payload, ensure_ascii=False)


MEDIA_DOWNLOAD_SCHEMA = {
    "name": "media_download",
    "description": (
        "Download one public video/media URL, send the video to the current Telegram chat, "
        "and delete temporary files after confirmed delivery or failure. Use this deterministic "
        "tool instead of terminal/downloaders/browser fallbacks. Set keep_local_copy=true only "
        "when the user explicitly asks to keep or save a server copy."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "One public http/https media URL."},
            "keep_local_copy": {
                "type": "boolean",
                "description": "Keep a server copy only when the user explicitly requested it. Default false.",
                "default": False,
            },
        },
        "required": ["url"],
    },
}


registry.register(
    name="media_download",
    toolset="media_download",
    schema=MEDIA_DOWNLOAD_SCHEMA,
    handler=_handle_media_download,
    check_fn=check_media_download_requirements,
    is_async=True,
    emoji="⬇️",
)
