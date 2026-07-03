"""Narrow Avito Worker bridge.

The tool talks only to the local Avito Worker reverse tunnel configured by
``AVITO_WORKER_BASE_URL`` and ``AVITO_WORKER_API_TOKEN``. It intentionally does
not expose arbitrary HTTP or browser fallback behavior.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import ssl
from typing import Any
from urllib.parse import urlparse

from tools.registry import registry

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HEALTH_TIMEOUT_SECONDS = 5
TOOL_TIMEOUT_SECONDS = 45
CONNECT_TIMEOUT_SECONDS = 3
ALLOWED_ENDPOINTS = {"/health", "/search", "/listing"}
TRANSIENT_NETWORK_ERRORS = (
    TimeoutError,
    socket.timeout,
    ConnectionError,
    ConnectionRefusedError,
    ConnectionResetError,
    http.client.RemoteDisconnected,
)
NETWORK_EXCEPTIONS = (ssl.SSLError, OSError) + TRANSIENT_NETWORK_ERRORS


def _json_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _status(status: str, message: str, **extra: Any) -> str:
    payload = {"ok": False, "status": status, "message": message}
    payload.update(extra)
    return _json_result(payload)


def _get_config() -> tuple[str | None, str | None]:
    base_url = os.getenv("AVITO_WORKER_BASE_URL", "").strip()
    token = os.getenv("AVITO_WORKER_API_TOKEN", "").strip()
    if not base_url or not token:
        return None, None

    parsed = urlparse(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None, None
    return base_url.rstrip("/"), token


def _normalize_worker_error(data: Any, fallback: str = "Worker returned error") -> tuple[str, str]:
    code = ""
    message = fallback
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            message = str(error.get("message") or fallback)
        else:
            code = str(data.get("code") or data.get("status") or "")
            message = str(data.get("message") or fallback)

    mapping = {
        "UNAUTHORIZED": "AVITO_UNAUTHORIZED",
        "CAPTCHA_REQUIRED": "AVITO_CAPTCHA_REQUIRED",
        "AVITO_LAYOUT_CHANGED": "AVITO_LAYOUT_CHANGED",
        "LAYOUT_CHANGED": "AVITO_LAYOUT_CHANGED",
        "SEARCH_TIMEOUT": "AVITO_SEARCH_TIMEOUT",
        "TIMEOUT": "AVITO_SEARCH_TIMEOUT",
        "INVALID_AVITO_URL": "AVITO_INVALID_URL",
        "INVALID_URL": "AVITO_INVALID_URL",
        "NO_RESULTS": "AVITO_NO_RESULTS",
    }
    return mapping.get(code, "AVITO_INVALID_RESPONSE"), message


def _validate_endpoint(path: str) -> bool:
    return path in ALLOWED_ENDPOINTS


def _read_limited_response(resp: http.client.HTTPResponse) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(min(65536, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int = TOOL_TIMEOUT_SECONDS,
    auth: bool = True,
) -> tuple[dict[str, Any] | None, str | None, int | None]:
    if not _validate_endpoint(path):
        return None, "AVITO_INVALID_URL", None

    base_url, token = _get_config()
    if not base_url or not token:
        return None, "AVITO_NOT_CONFIGURED", None

    parsed = urlparse(base_url)
    body = b""
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if auth:
        headers["Authorization"] = f"Bearer {token}"

    last_error = None
    for attempt in range(2):
        conn: http.client.HTTPConnection | None = None
        try:
            conn = http.client.HTTPConnection(
                parsed.hostname,
                parsed.port,
                timeout=min(timeout, CONNECT_TIMEOUT_SECONDS),
            )
            conn.timeout = timeout
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()

            if 300 <= resp.status < 400:
                return None, "AVITO_INVALID_RESPONSE", resp.status
            if resp.status == 401:
                return None, "AVITO_UNAUTHORIZED", resp.status

            content_type = (resp.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            raw = _read_limited_response(resp)
            if raw is None:
                return None, "AVITO_INVALID_RESPONSE", resp.status
            if content_type != "application/json":
                return None, "AVITO_INVALID_RESPONSE", resp.status
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None, "AVITO_INVALID_RESPONSE", resp.status
            if not isinstance(data, dict):
                return None, "AVITO_INVALID_RESPONSE", resp.status
            if resp.status >= 400:
                status, _ = _normalize_worker_error(data)
                return data, status, resp.status
            return data, None, resp.status
        except NETWORK_EXCEPTIONS as exc:
            last_error = exc
            if attempt == 0:
                continue
            logger.debug("Avito worker request failed: %s", type(exc).__name__)
            if isinstance(exc, (TimeoutError, socket.timeout)):
                return None, "AVITO_SEARCH_TIMEOUT", None
            return None, "AVITO_WORKER_OFFLINE", None
        finally:
            if conn is not None:
                conn.close()

    logger.debug("Avito worker request failed after retry: %s", type(last_error).__name__)
    return None, "AVITO_WORKER_OFFLINE", None


def _validate_search_args(args: dict[str, Any]) -> dict[str, Any] | str:
    query = str(args.get("query", "")).strip()
    city = str(args.get("city", "")).strip()
    district_raw = args.get("district")
    district = None if district_raw is None else str(district_raw).strip() or None
    condition = str(args.get("condition", "any")).strip()
    strict_city = bool(args.get("strict_city", True))
    try:
        limit = int(args.get("limit", 10))
    except (TypeError, ValueError):
        return _status("AVITO_INVALID_RESPONSE", "limit must be an integer")

    if not query or not city:
        return _status("AVITO_INVALID_RESPONSE", "query and city are required")
    if condition not in {"used", "new", "any"}:
        return _status("AVITO_INVALID_RESPONSE", "condition must be used, new, or any")
    if limit < 1 or limit > 20:
        return _status("AVITO_INVALID_RESPONSE", "limit must be between 1 and 20")

    return {
        "query": query,
        "city": city,
        "district": district,
        "condition": condition,
        "limit": limit,
        "strict_city": strict_city,
    }


def _validate_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {"avito.ru", "www.avito.ru"} and bool(parsed.path)


def _handle_health(args: dict[str, Any], **_: Any) -> str:
    data, error_status, http_status = _request_json(
        "GET",
        "/health",
        timeout=HEALTH_TIMEOUT_SECONDS,
        auth=False,
    )
    if error_status:
        message = "Avito worker not configured" if error_status == "AVITO_NOT_CONFIGURED" else "Avito worker unavailable"
        return _status(error_status, message, http_status=http_status)
    return _json_result({"ok": True, "status": "ok", "worker": data or {}})


def _handle_search(args: dict[str, Any], **_: Any) -> str:
    payload = _validate_search_args(args)
    if isinstance(payload, str):
        return payload

    data, error_status, http_status = _request_json("POST", "/search", payload, timeout=TOOL_TIMEOUT_SECONDS)
    if error_status:
        if data:
            status, message = _normalize_worker_error(data)
            return _status(status if status != "AVITO_INVALID_RESPONSE" else error_status, message, http_status=http_status)
        message = "Avito worker not configured" if error_status == "AVITO_NOT_CONFIGURED" else "Avito search failed"
        return _status(error_status, message, http_status=http_status)

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return _status("AVITO_INVALID_RESPONSE", "Worker response missing results array")

    status = "ok"
    if not results:
        status = "AVITO_NO_RESULTS"
    elif len(results) < payload["limit"]:
        status = "AVITO_PARTIAL_RESULTS"

    return _json_result({
        "ok": True,
        "status": status,
        "query": payload,
        "count": len(results),
        "results": results,
    })


def _handle_get_listing(args: dict[str, Any], **_: Any) -> str:
    url = str(args.get("url", "")).strip()
    if not _validate_listing_url(url):
        return _status("AVITO_INVALID_URL", "Listing URL must be https://avito.ru or https://www.avito.ru")

    data, error_status, http_status = _request_json("POST", "/listing", {"url": url}, timeout=TOOL_TIMEOUT_SECONDS)
    if error_status:
        if data:
            status, message = _normalize_worker_error(data)
            return _status(status if status != "AVITO_INVALID_RESPONSE" else error_status, message, http_status=http_status)
        message = "Avito worker not configured" if error_status == "AVITO_NOT_CONFIGURED" else "Avito listing fetch failed"
        return _status(error_status, message, http_status=http_status)

    if not isinstance(data, dict):
        return _status("AVITO_INVALID_RESPONSE", "Worker response must be an object")
    return _json_result({"ok": True, "status": "ok", "listing": data})


AVITO_WORKER_HEALTH_SCHEMA = {
    "name": "avito_worker_health",
    "description": "Check local Avito Worker availability through the configured tunnel.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

AVITO_SEARCH_SCHEMA = {
    "name": "avito_search",
    "description": "Search Avito through the local worker. Strictly uses configured local worker only.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "city": {"type": "string", "description": "City, e.g. Королёв."},
            "district": {"type": ["string", "null"], "description": "Optional district, e.g. Юбилейный."},
            "condition": {"type": "string", "enum": ["used", "new", "any"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            "strict_city": {"type": "boolean", "description": "Reject results outside requested city."},
        },
        "required": ["query", "city", "condition", "limit", "strict_city"],
    },
}

AVITO_GET_LISTING_SCHEMA = {
    "name": "avito_get_listing",
    "description": "Fetch one Avito listing through the local worker. URL must be avito.ru HTTPS.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "https://avito.ru/... or https://www.avito.ru/..."},
        },
        "required": ["url"],
    },
}


registry.register(
    name="avito_worker_health",
    toolset="avito",
    schema=AVITO_WORKER_HEALTH_SCHEMA,
    handler=_handle_health,
    emoji="🟢",
)

registry.register(
    name="avito_search",
    toolset="avito",
    schema=AVITO_SEARCH_SCHEMA,
    handler=_handle_search,
    emoji="🔎",
)

registry.register(
    name="avito_get_listing",
    toolset="avito",
    schema=AVITO_GET_LISTING_SCHEMA,
    handler=_handle_get_listing,
    emoji="📄",
)
