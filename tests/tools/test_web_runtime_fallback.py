import json

import pytest

from tools import web_tools


class FakeProvider:
    def __init__(self, name, *, search_response=None, extract_response=None):
        self.name = name
        self._search_response = search_response
        self._extract_response = extract_response
        self.search_calls = 0
        self.extract_calls = 0

    def is_available(self):
        return True

    def supports_search(self):
        return self._search_response is not None

    def supports_extract(self):
        return self._extract_response is not None

    def search(self, query, limit):
        self.search_calls += 1
        if isinstance(self._search_response, Exception):
            raise self._search_response
        return self._search_response

    def extract(self, urls, **kwargs):
        self.extract_calls += 1
        if isinstance(self._extract_response, Exception):
            raise self._extract_response
        return self._extract_response(urls) if callable(self._extract_response) else self._extract_response


def _patch_registry(monkeypatch, providers):
    import agent.web_search_registry as registry
    monkeypatch.setattr(web_tools, "_ensure_web_plugins_loaded", lambda: None)
    monkeypatch.setattr(registry, "get_provider", lambda name: providers.get(name))
    monkeypatch.setattr(registry, "get_active_search_provider", lambda: None)
    monkeypatch.setattr(registry, "get_active_extract_provider", lambda: None)


def test_search_runtime_fallback(monkeypatch):
    first = FakeProvider("tavily", search_response={"success": False, "error": "429"})
    second = FakeProvider("firecrawl", search_response={
        "success": True,
        "data": {"web": [{"title": "ok", "url": "https://example.com", "description": "ok"}]},
    })
    _patch_registry(monkeypatch, {"tavily": first, "firecrawl": second})
    monkeypatch.setattr(web_tools, "_get_search_backend", lambda: "tavily")
    monkeypatch.setattr(web_tools, "_get_configured_fallbacks", lambda capability: ["firecrawl"])

    payload = json.loads(web_tools.web_search_tool("query", 3))

    assert payload["success"] is True
    assert payload["fallback"]["provider"] == "firecrawl"
    assert first.search_calls == 1
    assert second.search_calls == 1


@pytest.mark.asyncio
async def test_extract_runtime_fallback_only_for_failed_pages(monkeypatch):
    urls = ["https://a.example", "https://b.example"]
    first = FakeProvider("firecrawl", extract_response=lambda requested: [
        {"url": requested[0], "content": "A", "title": "A"},
        {"url": requested[1], "content": "", "title": "", "error": "402"},
    ])
    second = FakeProvider("tavily", extract_response=lambda requested: [
        {"url": requested[0], "content": "B", "title": "B"},
    ])
    _patch_registry(monkeypatch, {"firecrawl": first, "tavily": second})
    monkeypatch.setattr(web_tools, "_get_extract_backend", lambda: "firecrawl")
    monkeypatch.setattr(web_tools, "_get_configured_fallbacks", lambda capability: ["tavily"])

    async def safe_url(url):
        return True

    monkeypatch.setattr(web_tools, "async_is_safe_url", safe_url)

    payload = json.loads(await web_tools.web_extract_tool(urls, format="markdown"))
    by_url = {item["url"]: item for item in payload["results"]}

    assert by_url[urls[0]]["content"] == "A"
    assert by_url[urls[1]]["content"] == "B"
    assert first.extract_calls == 1
    assert second.extract_calls == 1
