from types import SimpleNamespace

import pytest

from gateway import telegram_media_fastpath as media


class FakeMessage:
    def __init__(self, text: str, chat_id: int = 42, thread_id=None):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id)
        self.message_thread_id = thread_id


class FakeAdapter:
    def __init__(self):
        self._latest_media_by_chat = {}
        self.sent = []
        self.edited = []
        self.videos = []
        self.documents = []

    async def send(self, chat_id, text, metadata=None):
        self.sent.append((str(chat_id), text, metadata))
        return SimpleNamespace(success=True, message_id=str(len(self.sent)))

    async def edit_message(self, chat_id, message_id, text):
        self.edited.append((str(chat_id), str(message_id), text))
        return SimpleNamespace(success=True)

    async def send_video(self, chat_id, path, caption=None, metadata=None):
        self.videos.append((str(chat_id), path, metadata))
        return SimpleNamespace(success=True)

    async def send_document(self, chat_id, path, caption=None, file_name=None, metadata=None):
        self.documents.append((str(chat_id), path, metadata))
        return SimpleNamespace(success=True)

    def _env_float_clamped(self, *_args, **_kwargs):
        return 45.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/abc123?si=test",
        "https://www.instagram.com/reel/abc123/?igsh=test",
    ],
)
async def test_bare_media_link_downloads_video_without_buttons(monkeypatch, url):
    adapter = FakeAdapter()
    calls = []

    async def fake_video(_adapter, record):
        calls.append(("video", record["url"]))

    async def fake_action(_adapter, record, action):
        calls.append((action, record["url"]))

    monkeypatch.setattr(media, "run_video_download", fake_video)
    monkeypatch.setattr(media, "run_youtube_action", fake_action)

    assert await media.handle_media_fast_path(adapter, FakeMessage(url)) is True
    assert calls == [("video", url)]


@pytest.mark.asyncio
async def test_explicit_youtube_summary_does_not_download_video(monkeypatch):
    adapter = FakeAdapter()
    calls = []

    async def fake_video(_adapter, record):
        calls.append("video")

    async def fake_action(_adapter, record, action):
        calls.append(action)

    monkeypatch.setattr(media, "run_video_download", fake_video)
    monkeypatch.setattr(media, "run_youtube_action", fake_action)

    text = "Перескажи суть https://youtu.be/abc123"
    assert await media.handle_media_fast_path(adapter, FakeMessage(text)) is True
    assert calls == ["summary"]


@pytest.mark.asyncio
async def test_combined_youtube_request_runs_video_and_summary(monkeypatch):
    adapter = FakeAdapter()
    calls = []

    async def fake_video(_adapter, record):
        calls.append("video")

    async def fake_action(_adapter, record, action):
        calls.append(action)

    monkeypatch.setattr(media, "run_video_download", fake_video)
    monkeypatch.setattr(media, "run_youtube_action", fake_action)

    text = "Скачай видео и перескажи https://youtube.com/watch?v=abc123"
    assert await media.handle_media_fast_path(adapter, FakeMessage(text)) is True
    assert calls == ["video", "summary"]


@pytest.mark.asyncio
async def test_instagram_summary_without_download_falls_back_to_agent(monkeypatch):
    adapter = FakeAdapter()
    text = "Перескажи https://instagram.com/reel/abc123"
    assert await media.handle_media_fast_path(adapter, FakeMessage(text)) is False


@pytest.mark.asyncio
async def test_size_guard_stops_before_download_process(monkeypatch):
    adapter = FakeAdapter()
    record = {
        "url": "https://youtu.be/large",
        "chat_id": "42",
        "thread_id": None,
        "created": 1.0,
    }

    async def oversized(_url):
        return 100 * 1024 * 1024

    monkeypatch.setattr(media, "probe_download_size", oversized)
    await media.run_video_download(adapter, record)

    assert record["stage"] == "skipped"
    assert record["video_done"] is True
    assert adapter.videos == []
    assert any("безопасный лимит" in item[2] for item in adapter.edited)
