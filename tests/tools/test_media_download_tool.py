import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from tools import media_download_tool as mod


class FakeAdapter:
    def __init__(self, *, send_success=True):
        self.send_success = send_success
        self.videos = []
        self.statuses = []
        self.deleted = []

    async def send(self, chat_id, content, metadata=None):
        self.statuses.append((chat_id, content, metadata))
        return SimpleNamespace(success=True, message_id="status-1")

    async def edit_message(self, chat_id, message_id, content, **kwargs):
        self.statuses.append((chat_id, content, kwargs.get("metadata")))
        return SimpleNamespace(success=True, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        return True

    async def send_video(self, chat_id, video_path, **kwargs):
        self.videos.append((chat_id, Path(video_path), kwargs))
        if self.send_success:
            return SimpleNamespace(success=True, message_id="video-42")
        return SimpleNamespace(success=False, message_id=None, error="nope")


def test_downloader_command_is_bounded_and_does_not_load_user_config(tmp_path):
    command = mod._build_downloader_command(
        "https://example.com/video", tmp_path / "media.%(ext)s"
    )
    joined = " ".join(command)
    assert "--ignore-config" in command
    assert "--no-plugin-dirs" in command
    assert "--no-remote-components" in command
    assert "--use-extractors default,-generic" in joined
    assert "--retries 0" in joined
    assert "--fragment-retries 0" in joined
    assert "--extractor-retries 0" in joined
    assert "--socket-timeout 10" in joined
    assert "--max-filesize 49M" in joined
    assert "--no-simulate" in command
    assert command[-2:] == ["--", "https://example.com/video"]
    assert not any("cookie" in part.casefold() for part in command)


def test_progress_parser_and_unknown_total_copy_are_truthful():
    progress = mod._parse_progress_line(
        mod._PROGRESS_PREFIX + "1048576|4194304|NA|524288|6"
    )
    assert progress == {
        "downloaded": 1048576.0,
        "total": 4194304.0,
        "speed": 524288.0,
        "eta": 6.0,
    }
    status = mod._format_download_status(progress)
    assert "25%" in status
    assert "1.0 МБ / 4.0 МБ" in status

    unknown = mod._parse_progress_line(
        mod._PROGRESS_PREFIX + "1048576|NA|NA|NA|NA"
    )
    unknown_status = mod._format_download_status(unknown)
    assert "%" not in unknown_status
    assert "1.0 МБ" in unknown_status


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("HTTP Error 429: Too Many Requests", "rate_limited"),
        ("This content is private; login required", "auth_required"),
        ("Unsupported URL", "unsupported"),
        ("file is larger than max-filesize", "too_large"),
        ("DRM protected", "drm"),
    ],
)
def test_failure_classification(raw, code):
    assert mod._classify_failure(raw)[0] == code


def test_stale_cleanup_never_touches_unrelated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_TEMP_ROOT", tmp_path)
    old = tmp_path / ("request-" + "a" * 32)
    fresh = tmp_path / ("request-" + "b" * 32)
    unrelated = tmp_path / "keep-me"
    old.mkdir()
    fresh.mkdir()
    unrelated.mkdir()
    stale = time.time() - mod._ORPHAN_MAX_AGE_SECONDS - 60
    os.utime(old, (stale, stale))
    assert mod._cleanup_stale_orphans() == 1
    assert not old.exists()
    assert fresh.exists()
    assert unrelated.exists()


@pytest.mark.asyncio
async def test_success_sends_video_and_removes_temp(tmp_path, monkeypatch):
    adapter = FakeAdapter()
    context = mod.TelegramContext(adapter, "chat-1", None, "msg-1")
    monkeypatch.setattr(mod, "_TEMP_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_dependency_available", lambda: True)
    monkeypatch.setattr(mod, "is_safe_url", lambda url: True)
    monkeypatch.setattr(mod, "_get_telegram_context", lambda: context)

    observed_request_dir = None

    async def fake_download(url, request_dir, status, started):
        nonlocal observed_request_dir
        observed_request_dir = request_dir
        path = request_dir / "media.mp4"
        path.write_bytes(b"video")
        return mod.DownloadResult(True, path, "Example", path.stat().st_size)

    monkeypatch.setattr(mod, "_run_downloader", fake_download)
    raw = await mod._handle_media_download({"url": "https://example.com/video"})
    result = json.loads(raw)

    assert result["success"] is True
    assert result["sent"] is True
    assert result["message_id"] == "video-42"
    assert result["next_response"] == "NO_REPLY"
    assert adapter.videos[0][0] == "chat-1"
    assert not observed_request_dir.exists()


@pytest.mark.asyncio
async def test_send_failure_still_removes_temp(tmp_path, monkeypatch):
    adapter = FakeAdapter(send_success=False)
    context = mod.TelegramContext(adapter, "chat-1", None, None)
    monkeypatch.setattr(mod, "_TEMP_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_dependency_available", lambda: True)
    monkeypatch.setattr(mod, "is_safe_url", lambda url: True)
    monkeypatch.setattr(mod, "_get_telegram_context", lambda: context)

    observed_request_dir = None

    async def fake_download(url, request_dir, status, started):
        nonlocal observed_request_dir
        observed_request_dir = request_dir
        path = request_dir / "media.mp4"
        path.write_bytes(b"video")
        return mod.DownloadResult(True, path, "Example", path.stat().st_size)

    monkeypatch.setattr(mod, "_run_downloader", fake_download)
    raw = await mod._handle_media_download({"url": "https://example.com/video"})
    result = json.loads(raw)

    assert result["success"] is False
    assert result["error_code"] == "telegram_send_failed"
    assert not observed_request_dir.exists()


@pytest.mark.asyncio
async def test_save_failure_reports_partial_success_and_removes_temp(tmp_path, monkeypatch):
    adapter = FakeAdapter()
    context = mod.TelegramContext(adapter, "chat-1", None, None)
    monkeypatch.setattr(mod, "_TEMP_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_dependency_available", lambda: True)
    monkeypatch.setattr(mod, "is_safe_url", lambda url: True)
    monkeypatch.setattr(mod, "_get_telegram_context", lambda: context)

    observed_request_dir = None

    async def fake_download(url, request_dir, status, started):
        nonlocal observed_request_dir
        observed_request_dir = request_dir
        path = request_dir / "media.mp4"
        path.write_bytes(b"video")
        return mod.DownloadResult(True, path, "Example", path.stat().st_size)

    def fail_move(*args, **kwargs):
        raise OSError("destination unavailable")

    monkeypatch.setattr(mod, "_run_downloader", fake_download)
    monkeypatch.setattr(mod.shutil, "move", fail_move)
    raw = await mod._handle_media_download(
        {"url": "https://example.com/video", "keep_local_copy": True}
    )
    result = json.loads(raw)

    assert result["success"] is False
    assert result["sent"] is True
    assert result["saved"] is False
    assert result["cleanup"] is True
    assert result["next_response"] == "Видео отправлено, но сохранить копию не удалось."
    assert not observed_request_dir.exists()


@pytest.mark.asyncio
async def test_invalid_url_never_downloads(monkeypatch):
    monkeypatch.setattr(mod, "_dependency_available", lambda: True)
    monkeypatch.setattr(mod, "is_safe_url", lambda url: False)
    called = False

    async def fake_download(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(mod, "_run_downloader", fake_download)
    result = await mod._handle_media_download({"url": "http://127.0.0.1/x"})
    assert "invalid_url" in result
    assert called is False
