import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools import avito_worker_tool as tool


class WorkerHandler(BaseHTTPRequestHandler):
    routes = {}
    seen_headers = []

    def log_message(self, fmt, *args):
        return

    def _send(self, status=200, body=None, content_type="application/json", headers=None):
        body = body if body is not None else {"ok": True}
        if isinstance(body, (dict, list)):
            raw = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            raw = body.encode("utf-8")
        else:
            raw = body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        WorkerHandler.seen_headers.append(dict(self.headers))
        route = WorkerHandler.routes.get(("GET", self.path))
        if route is None:
            self._send(404, {"error": {"code": "NOT_FOUND"}})
            return
        route(self)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or "0")
        self.body = self.rfile.read(length)
        WorkerHandler.seen_headers.append(dict(self.headers))
        route = WorkerHandler.routes.get(("POST", self.path))
        if route is None:
            self._send(404, {"error": {"code": "NOT_FOUND"}})
            return
        route(self)


@pytest.fixture()
def worker(monkeypatch):
    WorkerHandler.routes = {}
    WorkerHandler.seen_headers = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), WorkerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("AVITO_WORKER_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("AVITO_WORKER_API_TOKEN", "secret-token-value")
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)


def parse(result):
    return json.loads(result)


def test_health_success(worker):
    WorkerHandler.routes[("GET", "/health")] = lambda h: h._send(200, {"status": "healthy"})

    result = parse(tool._handle_health({}))

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["worker"]["status"] == "healthy"
    assert "Authorization" not in WorkerHandler.seen_headers[-1]


def test_search_success(worker):
    def search(handler):
        body = json.loads(handler.body)
        assert body["query"] == "бутыль"
        handler._send(200, {"results": [
            {"title": "Пэт бутыль 18,9л", "price": 500, "city": "Королёв", "url": "https://www.avito.ru/item"}
        ]})

    WorkerHandler.routes[("POST", "/search")] = search

    result = parse(tool._handle_search({
        "query": "бутыль",
        "city": "Королёв",
        "district": "Юбилейный",
        "condition": "used",
        "limit": 1,
        "strict_city": True,
    }))

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert WorkerHandler.seen_headers[-1]["Authorization"] == "Bearer secret-token-value"


def test_search_one_result_less_than_limit_is_partial(worker):
    WorkerHandler.routes[("POST", "/search")] = lambda h: h._send(200, {"results": [{"title": "one"}]})

    result = parse(tool._handle_search({
        "query": "бутыль",
        "city": "Королёв",
        "district": None,
        "condition": "used",
        "limit": 20,
        "strict_city": True,
    }))

    assert result["ok"] is True
    assert result["status"] == "AVITO_PARTIAL_RESULTS"
    assert result["count"] == 1


def test_search_empty_results(worker):
    WorkerHandler.routes[("POST", "/search")] = lambda h: h._send(200, {"results": []})

    result = parse(tool._handle_search({
        "query": "бутыль",
        "city": "Королёв",
        "district": None,
        "condition": "used",
        "limit": 20,
        "strict_city": True,
    }))

    assert result["ok"] is True
    assert result["status"] == "AVITO_NO_RESULTS"
    assert result["results"] == []


def test_listing_success(worker):
    WorkerHandler.routes[("POST", "/listing")] = lambda h: h._send(200, {
        "url": "https://www.avito.ru/korolev/item",
        "title": "Пэт бутыль",
    })

    result = parse(tool._handle_get_listing({"url": "https://www.avito.ru/korolev/item"}))

    assert result["ok"] is True
    assert result["listing"]["title"] == "Пэт бутыль"


def test_missing_token(monkeypatch):
    monkeypatch.setenv("AVITO_WORKER_BASE_URL", "http://127.0.0.1:18765")
    monkeypatch.delenv("AVITO_WORKER_API_TOKEN", raising=False)

    result = parse(tool._handle_search({
        "query": "бутыль",
        "city": "Королёв",
        "district": None,
        "condition": "used",
        "limit": 1,
        "strict_city": True,
    }))

    assert result["status"] == "AVITO_NOT_CONFIGURED"


def test_missing_base_url(monkeypatch):
    monkeypatch.delenv("AVITO_WORKER_BASE_URL", raising=False)
    monkeypatch.setenv("AVITO_WORKER_API_TOKEN", "secret-token-value")

    result = parse(tool._handle_health({}))

    assert result["status"] == "AVITO_NOT_CONFIGURED"


def test_unauthorized(worker):
    WorkerHandler.routes[("POST", "/search")] = lambda h: h._send(401, {"error": {"code": "UNAUTHORIZED"}})

    result = parse(tool._handle_search({
        "query": "бутыль",
        "city": "Королёв",
        "district": None,
        "condition": "used",
        "limit": 1,
        "strict_city": True,
    }))

    assert result["status"] == "AVITO_UNAUTHORIZED"


def test_timeout(worker, monkeypatch):
    monkeypatch.setattr(tool, "TOOL_TIMEOUT_SECONDS", 1)
    WorkerHandler.routes[("POST", "/search")] = lambda h: (time.sleep(2), h._send(200, {"results": []}))

    result = parse(tool._handle_search({
        "query": "бутыль",
        "city": "Королёв",
        "district": None,
        "condition": "used",
        "limit": 1,
        "strict_city": True,
    }))

    assert result["status"] == "AVITO_SEARCH_TIMEOUT"


@pytest.mark.parametrize(("code", "status"), [
    ("CAPTCHA_REQUIRED", "AVITO_CAPTCHA_REQUIRED"),
    ("AVITO_LAYOUT_CHANGED", "AVITO_LAYOUT_CHANGED"),
])
def test_worker_error_mapping(worker, code, status):
    WorkerHandler.routes[("POST", "/search")] = lambda h: h._send(409, {"error": {"code": code, "message": code}})

    result = parse(tool._handle_search({
        "query": "бутыль",
        "city": "Королёв",
        "district": None,
        "condition": "used",
        "limit": 1,
        "strict_city": True,
    }))

    assert result["status"] == status


def test_invalid_json(worker):
    WorkerHandler.routes[("POST", "/search")] = lambda h: h._send(200, "{bad json")

    result = parse(tool._handle_search({
        "query": "бутыль",
        "city": "Королёв",
        "district": None,
        "condition": "used",
        "limit": 1,
        "strict_city": True,
    }))

    assert result["status"] == "AVITO_INVALID_RESPONSE"


def test_wrong_content_type(worker):
    WorkerHandler.routes[("POST", "/search")] = lambda h: h._send(200, "ok", content_type="text/plain")

    result = parse(tool._handle_search({
        "query": "бутыль",
        "city": "Королёв",
        "district": None,
        "condition": "used",
        "limit": 1,
        "strict_city": True,
    }))

    assert result["status"] == "AVITO_INVALID_RESPONSE"


def test_oversized_response(worker, monkeypatch):
    monkeypatch.setattr(tool, "MAX_RESPONSE_BYTES", 64)
    WorkerHandler.routes[("POST", "/search")] = lambda h: h._send(200, b'{"results":"' + (b"x" * 128) + b'"}')

    result = parse(tool._handle_search({
        "query": "бутыль",
        "city": "Королёв",
        "district": None,
        "condition": "used",
        "limit": 1,
        "strict_city": True,
    }))

    assert result["status"] == "AVITO_INVALID_RESPONSE"


def test_external_redirect(worker):
    WorkerHandler.routes[("POST", "/search")] = lambda h: h._send(
        302,
        b"",
        headers={"Location": "https://example.com/"},
    )

    result = parse(tool._handle_search({
        "query": "бутыль",
        "city": "Королёв",
        "district": None,
        "condition": "used",
        "limit": 1,
        "strict_city": True,
    }))

    assert result["status"] == "AVITO_INVALID_RESPONSE"


@pytest.mark.parametrize("url", [
    "https://example.com/korolev/item",
    "https://avito.ru.example.com/korolev/item",
    "http://www.avito.ru/korolev/item",
])
def test_listing_rejects_other_domains(url):
    result = parse(tool._handle_get_listing({"url": url}))

    assert result["status"] == "AVITO_INVALID_URL"


def test_token_not_in_errors_or_logs(worker, caplog):
    WorkerHandler.routes[("POST", "/search")] = lambda h: h._send(401, {
        "error": {"code": "UNAUTHORIZED", "message": "bad auth"}
    })

    with caplog.at_level("DEBUG"):
        rendered = tool._handle_search({
            "query": "бутыль",
            "city": "Королёв",
            "district": None,
            "condition": "used",
            "limit": 1,
            "strict_city": True,
        })

    assert "secret-token-value" not in rendered
    assert "secret-token-value" not in caplog.text
