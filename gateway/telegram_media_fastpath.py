"""Deterministic Telegram handling for Instagram and YouTube links.

The fast path deliberately has no inline-button state. Bare links download the
video; explicit summary/transcript wording selects the matching pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MEDIA_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    r"instagram\.com/(?:reel|p)/[^\s?]+(?:\?[^\s]+)?|"
    r"youtu\.be/[^\s?]+(?:\?[^\s]+)?|"
    r"youtube\.com/(?:watch\?v=[^\s&]+(?:&[^\s]+)?|shorts/[^\s?]+(?:\?[^\s]+)?)"
    r")",
    re.I,
)
DOWNLOAD_RE = re.compile(
    r"^\s*(?:ск+ачай|загрузи|пришли|отправь)(?:\s+(?:мне|это|этот|ролик|видео))*[.!?]*\s*$",
    re.I,
)
VIDEO_INTENT_RE = re.compile(
    r"(?:\bск+ачай\b|\bзагрузи\b|\bпришли\b|\bотправь\b).{0,80}?"
    r"(?:\bвидео\b|\bролик\b|\bфайл\b)|(?:\bск+ачай\b|\bзагрузи\b)(?:\s|$)",
    re.I | re.S,
)
SUMMARY_INTENT_RE = re.compile(
    r"\b(?:сам+ари|summary|резюме)\b|\bкратко\b|перескаж|какая\s+суть|"
    r"\bсуть\b|чем.{0,30}полезн|ключев(?:ые|ая)\s+(?:мысли|тезисы)",
    re.I | re.S,
)
TEXT_INTENT_RE = re.compile(r"транскриб|расшифр|полный\s+текст|текстом", re.I)
STATUS_RE = re.compile(r"^\s*(?:статус|прогресс|что\s+там|как\s+там)[.!?]*\s*$", re.I)
LOCATION_SAVE_RE = re.compile(
    r"(?:\b(?:сохрани|запиши|добавь)\w*\b.{0,120}"
    r"\b(?:место|локаци|точк|объект)\w*\b|"
    r"\b(?:место|локаци|точк|объект)\w*\b.{0,120}"
    r"\b(?:сохрани|запиши|добавь)\w*\b)",
    re.I | re.S,
)
STATE_TTL_SECONDS = 600.0
DEFAULT_CACHED_SOURCE_MAX_MB = 50.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _media_dir() -> Path:
    return Path(os.getenv("HERMES_MEDIA_DOWNLOADER_DIR", "/home/hermes/media_downloader"))


def _runtime_python() -> str:
    candidate = _repo_root() / "venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return "/home/hermes/.hermes/hermes-agent/venv/bin/python"


def _yt_dlp() -> str:
    candidate = _repo_root() / "venv" / "bin" / "yt-dlp"
    if candidate.exists():
        return str(candidate)
    return "/home/hermes/.hermes/hermes-agent/venv/bin/yt-dlp"


def _elapsed(started: float) -> str:
    seconds_total = max(0, int(time.monotonic() - started))
    minutes, seconds = divmod(seconds_total, 60)
    return f"{minutes} мин {seconds:02d} сек" if minutes else f"{seconds} сек"


def _bytes(value: Any) -> str:
    try:
        size = max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return "неизвестно"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.1f} {unit}" if unit != "Б" else f"{int(size)} Б"
        size /= 1024
    return f"{size:.1f} ГБ"


def _platform_name(url: str) -> str:
    return "YouTube" if "youtu" in url.lower() else "Instagram"


def _normalize_media_url(url: str) -> str:
    return str(url or "").split("?", 1)[0].rstrip("/") + "/"


def _cached_media_for_url(url: str) -> Path | None:
    cache_path = _media_dir() / "cache.json"
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        item = payload.get(_normalize_media_url(url)) if isinstance(payload, dict) else None
        raw_path = item.get("path") if isinstance(item, dict) else None
        candidate = Path(str(raw_path or "")).expanduser().resolve()
        media_root = _media_dir().resolve()
        if not candidate.is_relative_to(media_root):
            return None
        if not candidate.is_file() or candidate.stat().st_size <= 0:
            return None
        try:
            max_mb = float(os.getenv("HERMES_TRAVEL_SOURCE_MEDIA_MAX_MB", DEFAULT_CACHED_SOURCE_MAX_MB))
        except (TypeError, ValueError):
            max_mb = DEFAULT_CACHED_SOURCE_MAX_MB
        max_bytes = int(min(500.0, max(1.0, max_mb)) * 1024 * 1024)
        if candidate.stat().st_size > max_bytes:
            return None
        return candidate
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def attach_pending_media_source_to_event(event: Any, adapter: Any, msg: Any) -> bool:
    """Reuse the latest undecided media URL for a clear follow-up command."""
    text = str(getattr(event, "text", "") or "").strip()
    if not text or MEDIA_URL_RE.search(text):
        return False
    if not (
        LOCATION_SAVE_RE.search(text)
        or SUMMARY_INTENT_RE.search(text)
        or TEXT_INTENT_RE.search(text)
        or VIDEO_INTENT_RE.search(text)
        or DOWNLOAD_RE.fullmatch(text)
    ):
        return False
    state = getattr(adapter, "_latest_media_by_chat", None) or {}
    record = state.get(_context_key(msg))
    if not record or time.monotonic() - float(record.get("created", 0)) > STATE_TTL_SECONDS:
        return False
    source_url = str(record.get("url") or "").strip()
    if not source_url:
        return False
    event.text = f"{source_url}\n{text}"
    metadata = getattr(event, "metadata", None)
    if isinstance(metadata, dict):
        metadata["telegram_pending_source_url"] = source_url
    return True


def attach_cached_media_to_event(event: Any) -> bool:
    """Attach a same-URL cached video to a save-place follow-up."""
    text = str(getattr(event, "text", "") or "")
    match = MEDIA_URL_RE.search(text)
    if not match or not LOCATION_SAVE_RE.search(text):
        return False
    source_url = match.group(0).rstrip(".,);]")
    cached = _cached_media_for_url(source_url)
    if cached is None:
        return False
    media_urls = getattr(event, "media_urls", None)
    media_types = getattr(event, "media_types", None)
    metadata = getattr(event, "metadata", None)
    if not isinstance(media_urls, list) or not isinstance(media_types, list):
        return False
    cached_str = str(cached)
    if cached_str not in media_urls:
        media_urls.append(cached_str)
        media_types.append("video/mp4")
    if isinstance(metadata, dict):
        metadata["telegram_cached_source_url"] = source_url
        metadata["telegram_cached_source_path"] = cached_str
    return True


def _context_key(msg: Any) -> str:
    thread_id = getattr(msg, "message_thread_id", None)
    chat = getattr(msg, "chat", None)
    return f"{getattr(chat, 'id', '')}:{thread_id or ''}"


def status_snapshot(record: dict[str, Any]) -> str:
    stage = str(record.get("stage") or "queued")
    started = float(record.get("stage_started") or record.get("created") or time.monotonic())
    label = _platform_name(str(record.get("url") or ""))
    elapsed = _elapsed(started)
    if stage == "probing":
        return f"🔎 {label} · проверяю размер\nИдёт: {elapsed}"
    if stage == "downloading":
        percent = record.get("progress_percent")
        downloaded = _bytes(record.get("downloaded_bytes"))
        total = _bytes(record.get("total_bytes") or record.get("estimated_bytes"))
        if isinstance(percent, (int, float)):
            filled = min(10, max(0, int(float(percent) // 10)))
            bar = "▰" * filled + "▱" * (10 - filled)
            return f"⬇️ {label} · {float(percent):.0f}%\n{bar}\n{downloaded} / {total}\nИдёт: {elapsed}"
        return f"⬇️ {label} · скачивание\nОжидаемый размер: {total}\nИдёт: {elapsed}"
    if stage == "sending":
        return f"📤 {label} · отправка в Telegram\nФайл: {_bytes(record.get('actual_bytes'))}\nИдёт: {elapsed}"
    if stage == "summary":
        return f"📝 YouTube · готовлю саммари\nИдёт: {elapsed}"
    if stage == "transcript":
        return f"📝 YouTube · получаю расшифровку\nИдёт: {elapsed}"
    if stage in {"done", "failed", "skipped"}:
        return str(record.get("last_status") or "Готово")
    return f"⏳ {label} · задача принята\nИдёт: {elapsed}"


async def probe_download_size(url: str) -> int | None:
    cmd = [_yt_dlp()]
    cookies = Path("/home/hermes/.hermes/youtube-cookies.txt")
    if cookies.is_file():
        cmd.extend(["--cookies", str(cookies)])
    cmd.extend([
        "--js-runtimes", "node:/home/hermes/.local/bin/node",
        "--remote-components", "ejs:github",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--no-playlist", "--skip-download", "--dump-single-json",
        "--quiet", "--no-warnings", url,
    ])
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(_media_dir()),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        return None
    if proc.returncode != 0 or not stdout:
        return None
    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace"))
    except Exception:
        return None
    formats = payload.get("requested_formats") or payload.get("requested_downloads") or [payload]
    sizes: list[int] = []
    for item in formats:
        if not isinstance(item, dict):
            continue
        raw = item.get("filesize") or item.get("filesize_approx") or 0
        try:
            sizes.append(int(raw))
        except (TypeError, ValueError):
            pass
    total = sum(value for value in sizes if value > 0)
    if total <= 0:
        try:
            duration = float(payload.get("duration") or 0)
            tbr = float(payload.get("tbr") or 0)
            total = int(duration * tbr * 1000 / 8) if duration > 0 and tbr > 0 else 0
        except (TypeError, ValueError):
            total = 0
    return total or None


async def _edit(adapter: Any, chat_id: str, message_id: str | None, text: str) -> None:
    if not message_id:
        return
    try:
        await adapter.edit_message(chat_id, str(message_id), text)
    except Exception:
        logger.debug("Telegram media status edit failed", exc_info=True)


async def _followup(adapter: Any, record: dict[str, Any]) -> None:
    metadata = None
    if record.get("thread_id") is not None:
        metadata = {"thread_id": str(record["thread_id"])}
    await adapter.send(
        str(record["chat_id"]),
        "Новый ролик — просто пришли следующую ссылку",
        metadata=metadata,
    )


async def run_video_download(adapter: Any, record: dict[str, Any]) -> None:
    if record.get("video_running") or record.get("video_done"):
        return
    record["video_running"] = True
    record["stage"] = "probing"
    record["stage_started"] = time.monotonic()
    chat_id = str(record["chat_id"])
    metadata = {"thread_id": str(record["thread_id"])} if record.get("thread_id") is not None else None
    limit_getter = getattr(adapter, "_env_float_clamped", None)
    limit_mb = limit_getter("HERMES_TELEGRAM_UPLOAD_LIMIT_MB", 45.0, min_value=1.0, max_value=1900.0) if callable(limit_getter) else 45.0
    upload_limit = int(float(limit_mb) * 1024 * 1024)
    status = await adapter.send(chat_id, status_snapshot(record), metadata=metadata)
    status_id = getattr(status, "message_id", None)
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            await asyncio.sleep(3)
            if stop.is_set():
                break
            await _edit(adapter, chat_id, status_id, status_snapshot(record))

    beat = asyncio.create_task(heartbeat())
    proc = None
    try:
        estimated = await probe_download_size(str(record["url"]))
        record["estimated_bytes"] = estimated
        record["total_bytes"] = estimated
        if estimated is None:
            message = "Не удалось безопасно определить размер видео. Не скачиваю его, чтобы не перегружать VPS."
            record.update(stage="skipped", video_done=True, last_status=message)
            await _edit(adapter, chat_id, status_id, message)
            return
        if estimated > upload_limit:
            message = (
                f"Видео примерно {_bytes(estimated)}, безопасный лимит отправки {_bytes(upload_limit)}. "
                "Не скачиваю файл, чтобы не перегружать VPS."
            )
            record.update(stage="skipped", video_done=True, last_status=message)
            await _edit(adapter, chat_id, status_id, message)
            return

        record["stage"] = "downloading"
        proc = await asyncio.create_subprocess_exec(
            _runtime_python(),
            str(_media_dir() / "download.py"),
            str(record["url"]),
            cwd=str(_media_dir()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output_lines: list[str] = []
        last_edit = 0.0
        assert proc.stdout is not None
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=180)
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            output_lines.append(decoded)
            if decoded.startswith("PROGRESS:"):
                parts = decoded.split(":", 1)[1].split("|")
                try:
                    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", parts[0])
                    if match:
                        record["progress_percent"] = float(match.group(1))
                    if len(parts) > 1 and parts[1] not in {"NA", "N/A", ""}:
                        record["downloaded_bytes"] = int(float(parts[1]))
                    if len(parts) > 2 and parts[2] not in {"NA", "N/A", ""}:
                        record["total_bytes"] = int(float(parts[2]))
                except (TypeError, ValueError):
                    pass
                now = time.monotonic()
                if now - last_edit >= 2:
                    last_edit = now
                    await _edit(adapter, chat_id, status_id, status_snapshot(record))
        returncode = await proc.wait()
        output = "\n".join(output_lines)
        match = re.search(r"^RESULT_PATH:(.+)$", output, re.M)
        video_path = match.group(1).strip() if match else ""
        if video_path and not os.path.isabs(video_path):
            video_path = str((_media_dir() / video_path).resolve())
        if returncode != 0 or not video_path or not os.path.isfile(video_path) or os.path.getsize(video_path) <= 0:
            if "Sign in to confirm" in output or "LOGIN_REQUIRED" in output:
                raise RuntimeError("площадка требует cookies или прокси")
            raise RuntimeError("загрузчик не вернул готовый файл")

        actual_size = os.path.getsize(video_path)
        record["actual_bytes"] = actual_size
        record["video_path"] = video_path
        if actual_size > upload_limit:
            message = f"Файл оказался {_bytes(actual_size)} и превышает безопасный лимит {_bytes(upload_limit)}. Не отправляю его."
            record.update(stage="skipped", video_done=True, last_status=message)
            await _edit(adapter, chat_id, status_id, message)
            return
        record["stage"] = "sending"
        record["stage_started"] = time.monotonic()
        await _edit(adapter, chat_id, status_id, status_snapshot(record))
        sent = await adapter.send_video(chat_id, video_path, caption=None, metadata=metadata)
        if not getattr(sent, "success", False):
            raise RuntimeError(getattr(sent, "error", None) or "Telegram не принял видео")
        record.update(video_done=True, stage="done", last_status=f"Готово 🎬\n{_bytes(actual_size)}")
        await _edit(adapter, chat_id, status_id, record["last_status"])
        await _followup(adapter, record)
    except asyncio.TimeoutError:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        message = "Не удалось обработать видео за 3 минуты."
        record.update(stage="failed", last_status=message)
        await _edit(adapter, chat_id, status_id, message)
    except Exception as exc:
        logger.warning("Telegram media fast path failed: %s", exc, exc_info=True)
        message = f"Видео не отправлено: {str(exc)[:160]}."
        record.update(stage="failed", last_status=message)
        await _edit(adapter, chat_id, status_id, message)
    finally:
        stop.set()
        beat.cancel()
        record["video_running"] = False


async def run_youtube_action(adapter: Any, record: dict[str, Any], action: str) -> None:
    if action not in {"summary", "text"}:
        return
    running_key = f"{action}_running"
    done_key = f"{action}_done"
    if record.get(running_key) or record.get(done_key):
        return
    record[running_key] = True
    chat_id = str(record["chat_id"])
    metadata = {"thread_id": str(record["thread_id"])} if record.get("thread_id") is not None else None
    record["stage"] = "summary" if action == "summary" else "transcript"
    record["stage_started"] = time.monotonic()
    status = await adapter.send(chat_id, status_snapshot(record), metadata=metadata)
    status_id = getattr(status, "message_id", None)
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            _runtime_python(),
            str(_repo_root() / "scripts" / "youtube_pipeline.py"),
            str(record["url"]),
            "--action", action,
            cwd=str(_media_dir()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=330)
        decoded = stdout.decode("utf-8", errors="replace").strip()
        error_text = stderr.decode("utf-8", errors="replace").strip()
        try:
            payload = json.loads(decoded.splitlines()[-1]) if decoded else {}
        except Exception:
            payload = {}
        if proc.returncode != 0 or payload.get("status") != "success":
            raise RuntimeError(str(payload.get("message") or error_text or "неизвестная ошибка")[:300])
        if action == "summary":
            summary = str(payload.get("summary") or "").strip()
            if not summary:
                raise RuntimeError("получен пустой результат саммари")
            sent = await adapter.send(chat_id, summary, metadata=metadata)
        else:
            transcript_path = str(payload.get("transcript_path") or "")
            if not transcript_path or not os.path.isfile(transcript_path):
                raise RuntimeError("файл расшифровки не создан")
            sent = await adapter.send_document(
                chat_id,
                transcript_path,
                caption="Расшифровка YouTube-видео",
                file_name="youtube_transcript.txt",
                metadata=metadata,
            )
        if not getattr(sent, "success", False):
            raise RuntimeError(getattr(sent, "error", None) or "Telegram не принял результат")
        record[done_key] = True
        record.update(stage="done", last_status="Саммари готово ✓" if action == "summary" else "Расшифровка готова ✓")
        await _edit(adapter, chat_id, status_id, record["last_status"])
    except asyncio.TimeoutError:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        record.update(stage="failed", last_status="Не удалось обработать видео за 5 минут")
        await _edit(adapter, chat_id, status_id, record["last_status"])
    except Exception as exc:
        logger.warning("YouTube %s pipeline failed: %s", action, exc, exc_info=True)
        record.update(stage="failed", last_status=f"Не получилось: {str(exc)[:220]}")
        await _edit(adapter, chat_id, status_id, record["last_status"])
    finally:
        record[running_key] = False


async def handle_media_fast_path(adapter: Any, msg: Any) -> bool:
    value = str(getattr(msg, "text", "") or "").strip()
    state = getattr(adapter, "_latest_media_by_chat", None)
    if state is None:
        state = {}
        adapter._latest_media_by_chat = state
    key = _context_key(msg)

    if STATUS_RE.fullmatch(value):
        record = state.get(key)
        if record and time.monotonic() - float(record.get("created", 0)) <= STATE_TTL_SECONDS:
            metadata = {"thread_id": str(getattr(msg, "message_thread_id"))} if getattr(msg, "message_thread_id", None) is not None else None
            await adapter.send(str(msg.chat.id), status_snapshot(record), metadata=metadata)
            return True

    match = MEDIA_URL_RE.search(value)
    if not match:
        record = state.get(key)
        if record and time.monotonic() - float(record.get("created", 0)) <= STATE_TTL_SECONDS:
            if DOWNLOAD_RE.fullmatch(value):
                await run_video_download(adapter, record)
                return True
            is_youtube = "youtu" in str(record.get("url") or "").lower()
            if is_youtube and SUMMARY_INTENT_RE.search(value):
                await run_youtube_action(adapter, record, "summary")
                return True
            if is_youtube and TEXT_INTENT_RE.search(value):
                await run_youtube_action(adapter, record, "text")
                return True
        return False

    url = match.group(0).rstrip(".,);]")
    is_full_link = MEDIA_URL_RE.fullmatch(value) is not None
    is_youtube = "youtu" in url.lower()
    wants_video = bool(VIDEO_INTENT_RE.search(value))
    wants_summary = bool(SUMMARY_INTENT_RE.search(value))
    wants_text = bool(TEXT_INTENT_RE.search(value))

    record = {
        "url": url,
        "chat_id": str(msg.chat.id),
        "thread_id": getattr(msg, "message_thread_id", None),
        "created": time.monotonic(),
        "stage": "awaiting_intent" if is_full_link else "queued",
    }
    state[key] = record

    if is_full_link and not (wants_video or wants_summary or wants_text):
        return False
    if not is_youtube and not wants_video:
        return False
    if not (wants_video or wants_summary or wants_text):
        return False
    if wants_video:
        await run_video_download(adapter, record)
    if is_youtube and wants_summary:
        await run_youtube_action(adapter, record, "summary")
    elif is_youtube and wants_text:
        await run_youtube_action(adapter, record, "text")
    return True
