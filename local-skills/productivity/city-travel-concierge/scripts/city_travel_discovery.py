#!/usr/bin/env python3
"""Events and nearby-place discovery for city-travel-concierge.

Providers:
- Overpass API for bounded OpenStreetMap infrastructure searches.
- KudaGo public API for current events.

The module is stdlib-only, preserves source attribution, and degrades by
provider so one unavailable service does not erase successful results.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import city_travel_concierge as core

KUDAGO_EVENTS_URL = "https://kudago.com/public-api/v1.4/events/"
OVERPASS_ENDPOINTS = (
    ("vk-maps-russia", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"),
    ("fossgis-main", "https://overpass-api.de/api/interpreter"),
)
OSM_COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"

KUDAGO_TTL_SECONDS = 6 * 60 * 60
OVERPASS_TTL_SECONDS = 12 * 60 * 60
MAX_OVERPASS_RADIUS_M = 5000
MAX_RESULTS = 50

UrlOpen = Callable[[urllib.request.Request, float], Any]

OVERPASS_CATEGORY_FILTERS: dict[str, list[tuple[str, str]]] = {
    "toilets": [("amenity", "toilets")],
    "playgrounds": [("leisure", "playground")],
    "parks": [("leisure", "park"), ("leisure", "garden")],
    "museums": [("tourism", "museum"), ("tourism", "gallery")],
    "libraries": [("amenity", "library")],
    "theatres": [("amenity", "theatre")],
    "parking": [("amenity", "parking")],
}


def parse_datetime(value: str | datetime | None, *, default: datetime | None = None) -> datetime:
    if value is None:
        return default or core.utc_now()
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _status(
    provider: str,
    operation: str,
    success: bool,
    *,
    http_status: int | None = None,
    response_ms: int | None = None,
    result_count: int = 0,
    cache: str = "miss",
    error_code: str | None = None,
    endpoint: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "provider": provider,
        "operation": operation,
        "success": success,
        "http_status": http_status,
        "response_ms": response_ms,
        "result_count": result_count,
        "cache": cache,
    }
    if error_code:
        out["error_code"] = error_code
    if endpoint:
        out["endpoint"] = endpoint
    return out


def _envelope(
    operation: str,
    results: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
    *,
    checked: datetime | None = None,
    ttl_seconds: int,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checked = checked or core.utc_now()
    out: dict[str, Any] = {
        "ok": bool(results) or any(bool(item.get("success")) for item in statuses),
        "operation": operation,
        "checked_at": core.iso(checked),
        "expires_at": core.iso(checked + timedelta(seconds=ttl_seconds)),
        "results": results,
        "provider_status": statuses,
    }
    if metadata:
        out["metadata"] = metadata
    return out


def _rate_limit(provider: str, interval: float) -> None:
    path = core.cache_dir() / f".rate-{provider}"
    now = time.time()
    try:
        previous = float(path.read_text(encoding="utf-8").strip())
    except Exception:
        previous = 0.0
    delay = interval - (now - previous)
    if delay > 0:
        time.sleep(delay)
    try:
        path.write_text(str(time.time()), encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass


def _cached_json_request(
    provider: str,
    operation: str,
    cache_payload: dict[str, Any],
    request_factory: Callable[[], urllib.request.Request],
    *,
    ttl_seconds: int,
    timeout: float,
    retries: int = core.DEFAULT_RETRIES,
    rate_interval: float = 1.0,
    urlopen: UrlOpen | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    cache_key = core.stable_cache_key(provider, operation, cache_payload)
    cached = core.read_cache(cache_key)
    if cached is not None:
        values = cached.get("results")
        count = len(values) if isinstance(values, list) else 0
        return cached, _status(
            provider,
            operation,
            True,
            http_status=200,
            response_ms=0,
            result_count=count,
            cache="hit",
        )

    last_error = "unknown_error"
    last_status: int | None = None
    for attempt in range(retries + 1):
        try:
            _rate_limit(provider, rate_interval)
            payload, status, elapsed_ms = core._request_json(
                request_factory(), timeout, urlopen=urlopen
            )
            core.write_cache(cache_key, payload, ttl_seconds=ttl_seconds)
            values = payload.get("results")
            count = len(values) if isinstance(values, list) else 0
            return payload, _status(
                provider,
                operation,
                True,
                http_status=status,
                response_ms=elapsed_ms,
                result_count=count,
                cache="miss",
            )
        except core.ProviderError as exc:
            last_error = exc.code
            last_status = exc.status
            retryable = exc.code in {"timeout", "network_error", "http_retryable"}
            if not retryable or attempt >= retries:
                break
            time.sleep(min(2.0, 0.35 * (2**attempt)))
    return None, _status(
        provider,
        operation,
        False,
        http_status=last_status,
        error_code=last_error,
    )


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    value = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(value))


def _base_result(
    source: str,
    kind: str,
    title: str,
    *,
    checked: datetime,
    ttl_seconds: int,
    confidence: float,
    verification_status: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "checked_at": core.iso(checked),
        "expires_at": core.iso(checked + timedelta(seconds=ttl_seconds)),
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "verification_status": verification_status,
        "kind": kind,
        "title": title,
        "coordinates": None,
        "address": None,
        "categories": [],
        "dates": [],
        "is_free": None,
        "age_restriction": None,
        "distance_m": None,
        "provider_payload": {},
        "deep_links": {},
        "source_url": None,
        "attribution": None,
    }


def _deep_links(lat: float | None, lon: float | None, title: str) -> dict[str, str | None]:
    return {
        "yandex_maps": core.yandex_place_link(lat, lon, title),
        "2gis": core.twogis_place_link(title),
    }


def build_overpass_query(
    lat: float,
    lon: float,
    radius_m: int,
    categories: list[str],
    *,
    timeout_seconds: int = 15,
) -> tuple[str, list[str], int]:
    radius = max(50, min(MAX_OVERPASS_RADIUS_M, int(radius_m)))
    selected = [item for item in categories if item in OVERPASS_CATEGORY_FILTERS]
    if not selected:
        raise ValueError("unsupported_category")
    clauses: list[str] = []
    for category in selected:
        for key, value in OVERPASS_CATEGORY_FILTERS[category]:
            clauses.append(
                f'nwr(around:{radius},{lat:.6f},{lon:.6f})["{key}"="{value}"];'
            )
    query = (
        f"[out:json][timeout:{max(5, min(25, timeout_seconds))}];"
        "(" + "".join(clauses) + ");out center tags;"
    )
    return query, selected, radius


def _overpass_category(tags: dict[str, Any]) -> str:
    for category, filters in OVERPASS_CATEGORY_FILTERS.items():
        for key, value in filters:
            if str(tags.get(key) or "") == value:
                return category
    return "other"


def overpass_nearby(
    lat: float,
    lon: float,
    categories: list[str],
    *,
    radius_m: int = 1000,
    limit: int = 20,
    timeout: float = core.DEFAULT_TIMEOUT,
    urlopen: UrlOpen | None = None,
    endpoints: tuple[tuple[str, str], ...] = OVERPASS_ENDPOINTS,
) -> dict[str, Any]:
    checked = core.utc_now()
    try:
        query, selected, radius = build_overpass_query(lat, lon, radius_m, categories)
    except ValueError as exc:
        return _envelope(
            "overpass-nearby",
            [],
            [_status("overpass", "nearby", False, error_code=str(exc))],
            checked=checked,
            ttl_seconds=OVERPASS_TTL_SECONDS,
        )

    result_limit = min(MAX_RESULTS, max(1, int(limit)))
    cache_payload = {"query": query, "limit": result_limit}
    cache_key = core.stable_cache_key("overpass", "nearby", cache_payload)
    cached = core.read_cache(cache_key)
    statuses: list[dict[str, Any]] = []
    payload: dict[str, Any] | None = None

    if cached is not None:
        payload = cached
        statuses.append(
            _status(
                "overpass",
                "nearby",
                True,
                http_status=200,
                response_ms=0,
                result_count=len(payload.get("elements") or []),
                cache="hit",
                endpoint=str(payload.get("_endpoint") or "cache"),
            )
        )
    else:
        body = urllib.parse.urlencode({"data": query}).encode("utf-8")
        for endpoint_name, endpoint_url in endpoints:
            last_error = "unknown_error"
            last_status: int | None = None
            for attempt in range(core.DEFAULT_RETRIES + 1):
                try:
                    _rate_limit("overpass", 1.5)
                    request = urllib.request.Request(
                        endpoint_url,
                        data=body,
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/x-www-form-urlencoded",
                            "User-Agent": core.USER_AGENT,
                        },
                        method="POST",
                    )
                    data, status, elapsed_ms = core._request_json(
                        request, timeout, urlopen=urlopen
                    )
                    data["_endpoint"] = endpoint_name
                    core.write_cache(cache_key, data, ttl_seconds=OVERPASS_TTL_SECONDS)
                    payload = data
                    statuses.append(
                        _status(
                            "overpass",
                            "nearby",
                            True,
                            http_status=status,
                            response_ms=elapsed_ms,
                            result_count=len(data.get("elements") or []),
                            endpoint=endpoint_name,
                        )
                    )
                    break
                except core.ProviderError as exc:
                    last_error = exc.code
                    last_status = exc.status
                    retryable = exc.code in {"timeout", "network_error", "http_retryable"}
                    if not retryable or attempt >= core.DEFAULT_RETRIES:
                        break
                    time.sleep(min(2.0, 0.4 * (2**attempt)))
            if payload is not None:
                break
            statuses.append(
                _status(
                    "overpass",
                    "nearby",
                    False,
                    http_status=last_status,
                    error_code=last_error,
                    endpoint=endpoint_name,
                )
            )

    results: list[dict[str, Any]] = []
    if payload:
        for element in payload.get("elements") or []:
            if not isinstance(element, dict):
                continue
            tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
            center = element.get("center") if isinstance(element.get("center"), dict) else {}
            item_lat = core.as_float(element.get("lat"))
            item_lon = core.as_float(element.get("lon"))
            if item_lat is None or item_lon is None:
                item_lat = core.as_float(center.get("lat"))
                item_lon = core.as_float(center.get("lon"))
            if item_lat is None or item_lon is None:
                continue
            category = _overpass_category(tags)
            title = str(tags.get("name") or tags.get("official_name") or category)
            verification = "unverified" if category == "parking" else "provider_reported"
            result = _base_result(
                "openstreetmap",
                "infrastructure",
                title,
                checked=checked,
                ttl_seconds=OVERPASS_TTL_SECONDS,
                confidence=0.72 if tags.get("name") else 0.6,
                verification_status=verification,
            )
            result["coordinates"] = {"lat": item_lat, "lon": item_lon}
            result["address"] = {
                "street": tags.get("addr:street"),
                "housenumber": tags.get("addr:housenumber"),
                "city": tags.get("addr:city"),
            }
            result["categories"] = [category]
            result["distance_m"] = round(_haversine_m(lat, lon, item_lat, item_lon))
            element_type = str(element.get("type") or "node")
            element_id = element.get("id")
            result["provider_payload"] = {
                "osm_type": element_type,
                "osm_id": element_id,
                "fee": tags.get("fee"),
                "access": tags.get("access"),
                "parking": tags.get("parking"),
                "opening_hours": tags.get("opening_hours"),
                "wheelchair": tags.get("wheelchair"),
                "operator": tags.get("operator"),
            }
            if element_id is not None:
                result["source_url"] = f"https://www.openstreetmap.org/{element_type}/{element_id}"
            result["attribution"] = {
                "name": "OpenStreetMap contributors",
                "url": OSM_COPYRIGHT_URL,
            }
            result["deep_links"] = _deep_links(item_lat, item_lon, title)
            results.append(result)

    results.sort(
        key=lambda item: (
            item.get("distance_m") is None,
            item.get("distance_m") or 0,
            item["title"],
        )
    )
    results = results[:result_limit]
    for status in statuses:
        if status.get("success"):
            status["result_count"] = len(results)
    return _envelope(
        "overpass-nearby",
        results,
        statuses,
        checked=checked,
        ttl_seconds=OVERPASS_TTL_SECONDS,
        metadata={"categories": selected, "radius_m": radius},
    )


def _parse_age_limit(value: Any) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _kudago_is_for_children(item: dict[str, Any]) -> bool:
    words: list[str] = []
    for category in item.get("categories") or []:
        if isinstance(category, dict):
            words.extend(
                [str(category.get("slug") or ""), str(category.get("name") or "")]
            )
        else:
            words.append(str(category))
    words.extend(str(tag) for tag in item.get("tags") or [])
    haystack = " ".join(words).lower()
    if any(marker in haystack for marker in ("kids", "child", "дет", "семейн", "family")):
        return True
    age = _parse_age_limit(item.get("age_restriction"))
    return age is not None and age <= 12


def kudago_events(
    *,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    location: str = "msk",
    lat: float | None = None,
    lon: float | None = None,
    radius_m: int | None = None,
    categories: list[str] | None = None,
    free_only: bool = False,
    children_only: bool = False,
    limit: int = 20,
    timeout: float = core.DEFAULT_TIMEOUT,
    urlopen: UrlOpen | None = None,
) -> dict[str, Any]:
    checked = core.utc_now()
    start_dt = parse_datetime(start, default=checked)
    end_dt = parse_datetime(end, default=start_dt + timedelta(days=7))
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt
    result_limit = max(1, int(limit))
    page_size = min(100, result_limit * (3 if children_only else 1))
    fields = (
        "id,title,slug,dates,place,location,categories,age_restriction,"
        "price,is_free,site_url,tags,description,favorites_count,comments_count"
    )
    params: dict[str, str] = {
        "lang": "ru",
        "location": location,
        "actual_since": str(int(start_dt.timestamp())),
        "actual_until": str(int(end_dt.timestamp())),
        "page_size": str(page_size),
        "fields": fields,
        "expand": "place,location,dates",
        "text_format": "text",
        "order_by": "-rank,-id",
    }
    if free_only:
        params["is_free"] = "1"
    if categories:
        params["categories"] = ",".join(categories)
    if lat is not None and lon is not None and radius_m is not None:
        params["lat"] = f"{lat:.6f}"
        params["lon"] = f"{lon:.6f}"
        params["radius"] = str(max(100, min(50_000, int(radius_m))))

    def request_factory() -> urllib.request.Request:
        url = KUDAGO_EVENTS_URL + "?" + urllib.parse.urlencode(params)
        return urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": core.USER_AGENT},
            method="GET",
        )

    payload, status = _cached_json_request(
        "kudago",
        "events",
        params,
        request_factory,
        ttl_seconds=KUDAGO_TTL_SECONDS,
        timeout=timeout,
        rate_interval=0.7,
        urlopen=urlopen,
    )
    results: list[dict[str, Any]] = []
    if payload:
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            if children_only and not _kudago_is_for_children(item):
                continue
            site_url = item.get("site_url")
            if not isinstance(site_url, str) or not site_url.startswith("https://"):
                continue
            place = item.get("place") if isinstance(item.get("place"), dict) else {}
            coords = place.get("coords") if isinstance(place.get("coords"), dict) else {}
            item_lat = core.as_float(coords.get("lat"))
            item_lon = core.as_float(coords.get("lon"))
            title = str(item.get("title") or item.get("short_title") or "Событие")
            result = _base_result(
                "kudago",
                "event",
                title,
                checked=checked,
                ttl_seconds=KUDAGO_TTL_SECONDS,
                confidence=0.82,
                verification_status="provider_reported",
            )
            if item_lat is not None and item_lon is not None:
                result["coordinates"] = {"lat": item_lat, "lon": item_lon}
                if lat is not None and lon is not None:
                    result["distance_m"] = round(
                        _haversine_m(lat, lon, item_lat, item_lon)
                    )
            result["address"] = {
                "formatted": place.get("address"),
                "place_title": place.get("title"),
                "location": (
                    (item.get("location") or {}).get("name")
                    if isinstance(item.get("location"), dict)
                    else location
                ),
            }
            normalized_categories: list[str] = []
            for category in item.get("categories") or []:
                if isinstance(category, dict):
                    normalized_categories.append(
                        str(category.get("slug") or category.get("name") or "")
                    )
                else:
                    normalized_categories.append(str(category))
            result["categories"] = [value for value in normalized_categories if value]
            result["dates"] = item.get("dates") if isinstance(item.get("dates"), list) else []
            result["is_free"] = bool(item.get("is_free"))
            result["age_restriction"] = item.get("age_restriction")
            result["source_url"] = site_url
            result["attribution"] = {
                "name": "KudaGo",
                "url": site_url,
                "required": True,
            }
            result["provider_payload"] = {
                "id": item.get("id"),
                "slug": item.get("slug"),
                "price": item.get("price"),
                "description": item.get("description"),
                "tags": item.get("tags"),
                "favorites_count": item.get("favorites_count"),
                "comments_count": item.get("comments_count"),
                "for_children": _kudago_is_for_children(item),
            }
            result["deep_links"] = _deep_links(item_lat, item_lon, title)
            results.append(result)
            if len(results) >= result_limit:
                break
    status["result_count"] = len(results)
    return _envelope(
        "kudago-events",
        results,
        [status],
        checked=checked,
        ttl_seconds=KUDAGO_TTL_SECONDS,
        metadata={
            "start": core.iso(start_dt),
            "end": core.iso(end_dt),
            "free_only": free_only,
            "children_only": children_only,
            "location": location,
        },
    )


def _normalize_title(value: str) -> str:
    value = value.lower().replace("ё", "е")
    return "".join(char for char in value if char.isalnum())


def _same_place(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _normalize_title(str(left.get("title") or "")) != _normalize_title(
        str(right.get("title") or "")
    ):
        return False
    a = left.get("coordinates") if isinstance(left.get("coordinates"), dict) else None
    b = right.get("coordinates") if isinstance(right.get("coordinates"), dict) else None
    if not a or not b:
        return True
    return _haversine_m(
        float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"])
    ) <= 150


def deduplicate_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for item in results:
        existing = next(
            (candidate for candidate in merged if _same_place(candidate, item)), None
        )
        if existing is None:
            clone = dict(item)
            clone["sources"] = [item.get("source")]
            clone["source_urls"] = (
                [item.get("source_url")] if item.get("source_url") else []
            )
            merged.append(clone)
            continue
        existing["sources"] = list(
            dict.fromkeys((existing.get("sources") or []) + [item.get("source")])
        )
        if item.get("source_url"):
            existing["source_urls"] = list(
                dict.fromkeys(
                    (existing.get("source_urls") or []) + [item["source_url"]]
                )
            )
        existing["confidence"] = max(
            float(existing.get("confidence") or 0),
            float(item.get("confidence") or 0),
        )
        existing["categories"] = list(
            dict.fromkeys(
                (existing.get("categories") or []) + (item.get("categories") or [])
            )
        )
        if not existing.get("coordinates") and item.get("coordinates"):
            existing["coordinates"] = item["coordinates"]
        if existing.get("distance_m") is None and item.get("distance_m") is not None:
            existing["distance_m"] = item["distance_m"]
        payload = existing.setdefault("provider_payload", {})
        payload["merged_duplicates"] = int(payload.get("merged_duplicates") or 0) + 1
    return merged


def rank_results(
    results: list[dict[str, Any]],
    *,
    free_only: bool = False,
    children_only: bool = False,
) -> list[dict[str, Any]]:
    source_bonus = {"kudago": 12.0, "openstreetmap": 8.0}
    for item in results:
        score = float(item.get("confidence") or 0) * 50
        score += source_bonus.get(str(item.get("source") or ""), 0)
        if item.get("coordinates"):
            score += 8
        if item.get("source_url"):
            score += 5
        if item.get("verification_status") == "provider_reported":
            score += 6
        distance = core.as_float(item.get("distance_m"))
        if distance is not None:
            score += max(0.0, 12.0 - distance / 500.0)
        if free_only and item.get("is_free") is True:
            score += 10
        if children_only and bool(
            (item.get("provider_payload") or {}).get("for_children")
        ):
            score += 10
        item["rank_score"] = round(score, 2)
    return sorted(
        results,
        key=lambda item: (
            -float(item.get("rank_score") or 0),
            item.get("distance_m") is None,
            item.get("distance_m") or 0,
            item.get("title") or "",
        ),
    )


def discover(
    *,
    lat: float,
    lon: float,
    radius_m: int = 1500,
    infrastructure_categories: list[str] | None = None,
    event_start: str | datetime | None = None,
    event_end: str | datetime | None = None,
    event_location: str = "msk",
    event_categories: list[str] | None = None,
    free_only: bool = False,
    children_only: bool = False,
    limit: int = 20,
    timeout: float = core.DEFAULT_TIMEOUT,
    urlopen: UrlOpen | None = None,
) -> dict[str, Any]:
    checked = core.utc_now()
    sections = [
        overpass_nearby(
            lat,
            lon,
            infrastructure_categories
            or ["toilets", "playgrounds", "parks", "museums"],
            radius_m=radius_m,
            limit=limit,
            timeout=timeout,
            urlopen=urlopen,
        ),
        kudago_events(
            start=event_start,
            end=event_end,
            location=event_location,
            lat=lat,
            lon=lon,
            radius_m=radius_m,
            categories=event_categories,
            free_only=free_only,
            children_only=children_only,
            limit=limit,
            timeout=timeout,
            urlopen=urlopen,
        ),
    ]
    statuses: list[dict[str, Any]] = []
    raw_results: list[dict[str, Any]] = []
    degraded: list[str] = []
    for section in sections:
        raw_results.extend(section.get("results") or [])
        section_statuses = section.get("provider_status") or []
        statuses.extend(section_statuses)
        if not any(bool(status.get("success")) for status in section_statuses):
            degraded.append(str(section.get("operation") or "unknown"))
    merged = deduplicate_results(raw_results)
    ranked = rank_results(
        merged, free_only=free_only, children_only=children_only
    )
    return {
        "ok": bool(ranked),
        "operation": "discovery",
        "source": "city-travel-concierge",
        "checked_at": core.iso(checked),
        "expires_at": core.iso(checked + timedelta(seconds=KUDAGO_TTL_SECONDS)),
        "confidence": 0.88 if not degraded else 0.68,
        "verification_status": "verified" if not degraded else "partial",
        "results": ranked[: min(MAX_RESULTS, max(1, int(limit)))],
        "raw_result_count": len(raw_results),
        "deduplicated_count": len(merged),
        "degraded_sections": degraded,
        "provider_status": statuses,
    }


def print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="City events and nearby-place discovery")
    parser.add_argument("--timeout", type=float, default=core.DEFAULT_TIMEOUT)
    sub = parser.add_subparsers(dest="command")

    overpass = sub.add_parser("overpass")
    overpass.add_argument("--lat", type=float, required=True)
    overpass.add_argument("--lon", type=float, required=True)
    overpass.add_argument("--radius", type=int, default=1000)
    overpass.add_argument("--category", action="append", required=True)
    overpass.add_argument("--limit", type=int, default=20)

    events = sub.add_parser("events")
    events.add_argument("--start")
    events.add_argument("--end")
    events.add_argument("--location", default="msk")
    events.add_argument("--lat", type=float)
    events.add_argument("--lon", type=float)
    events.add_argument("--radius", type=int)
    events.add_argument("--category", action="append")
    events.add_argument("--free", action="store_true")
    events.add_argument("--children", action="store_true")
    events.add_argument("--limit", type=int, default=20)

    combined = sub.add_parser("discover")
    combined.add_argument("--lat", type=float, required=True)
    combined.add_argument("--lon", type=float, required=True)
    combined.add_argument("--radius", type=int, default=1500)
    combined.add_argument("--infrastructure", action="append")
    combined.add_argument("--event-start")
    combined.add_argument("--event-end")
    combined.add_argument("--event-location", default="msk")
    combined.add_argument("--event-category", action="append")
    combined.add_argument("--free", action="store_true")
    combined.add_argument("--children", action="store_true")
    combined.add_argument("--limit", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "overpass":
        print_json(
            overpass_nearby(
                args.lat,
                args.lon,
                args.category,
                radius_m=args.radius,
                limit=args.limit,
                timeout=args.timeout,
            )
        )
        return 0
    if args.command == "events":
        print_json(
            kudago_events(
                start=args.start,
                end=args.end,
                location=args.location,
                lat=args.lat,
                lon=args.lon,
                radius_m=args.radius,
                categories=args.category,
                free_only=args.free,
                children_only=args.children,
                limit=args.limit,
                timeout=args.timeout,
            )
        )
        return 0
    if args.command == "discover":
        print_json(
            discover(
                lat=args.lat,
                lon=args.lon,
                radius_m=args.radius,
                infrastructure_categories=args.infrastructure,
                event_start=args.event_start,
                event_end=args.event_end,
                event_location=args.event_location,
                event_categories=args.event_category,
                free_only=args.free,
                children_only=args.children,
                limit=args.limit,
                timeout=args.timeout,
            )
        )
        return 0
    build_parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
