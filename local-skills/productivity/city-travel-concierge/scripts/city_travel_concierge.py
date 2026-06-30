#!/usr/bin/env python3
"""Minimal city travel concierge helper for Hermes skills.

No request headers, API-key query strings, or env values are printed. Public
functions return normalized provider data plus redacted provider status only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
GEOAPIFY_GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
GEOAPIFY_PLACES_URL = "https://api.geoapify.com/v2/places"
GEOAPIFY_ROUTE_URL = "https://api.geoapify.com/v1/routing"
USER_AGENT = "Hermes-CityTravelConcierge/0.1"
DEFAULT_TIMEOUT = 8.0
DEFAULT_RETRIES = 2
DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60
PROVIDER_MIN_INTERVAL = {"dadata": 1.0, "geoapify": 1.0}

UrlOpen = Callable[[urllib.request.Request, float], Any]


@dataclass
class ProviderStatus:
    provider: str
    operation: str
    success: bool
    http_status: int | None
    response_ms: int | None
    result_count: int
    cache: str
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "provider": self.provider,
            "operation": self.operation,
            "success": self.success,
            "http_status": self.http_status,
            "response_ms": self.response_ms,
            "result_count": self.result_count,
            "cache": self.cache,
        }
        if self.error_code:
            out["error_code"] = self.error_code
        return out


class ProviderError(RuntimeError):
    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def cache_dir() -> Path:
    root = hermes_home() / "cache" / "city-travel-concierge"
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def load_dotenv_keys(names: set[str]) -> None:
    """Load only requested keys from ~/.hermes/.env without returning values."""
    env_path = hermes_home() / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key in names and key not in os.environ:
                os.environ[key] = value.strip().strip('"\'')
    except OSError:
        return


def get_secret(name: str) -> str | None:
    load_dotenv_keys({name})
    value = os.environ.get(name, "").strip()
    return value or None


def stable_cache_key(provider: str, operation: str, payload: dict[str, Any]) -> str:
    safe_payload = {k: v for k, v in payload.items() if "key" not in k.lower() and "token" not in k.lower()}
    raw = json.dumps({"provider": provider, "operation": operation, "payload": safe_payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_cache(key: str) -> dict[str, Any] | None:
    path = cache_dir() / f"{key}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expires_at = data.get("expires_at_epoch")
    if not isinstance(expires_at, (int, float)) or expires_at < time.time():
        return None
    return data.get("payload") if isinstance(data.get("payload"), dict) else None


def write_cache(key: str, payload: dict[str, Any], ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> None:
    path = cache_dir() / f"{key}.json"
    tmp = path.with_suffix(".tmp")
    data = {"expires_at_epoch": time.time() + ttl_seconds, "payload": payload}
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        path.chmod(0o600)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def rate_limit(provider: str) -> None:
    interval = PROVIDER_MIN_INTERVAL.get(provider, 1.0)
    path = cache_dir() / f".rate-{provider}"
    now = time.time()
    try:
        last = float(path.read_text(encoding="utf-8").strip())
    except Exception:
        last = 0.0
    delay = interval - (now - last)
    if delay > 0:
        time.sleep(delay)
    try:
        path.write_text(str(time.time()), encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass


def _request_json(request: urllib.request.Request, timeout: float, urlopen: UrlOpen | None = None) -> tuple[dict[str, Any], int, int]:
    opener = urlopen or urllib.request.urlopen
    started = time.perf_counter()
    try:
        with opener(request, timeout=timeout) as response:  # type: ignore[arg-type]
            raw = response.read(4 * 1024 * 1024)
            status = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        code = "http_retryable" if status == 429 or 500 <= status <= 599 else "http_error"
        raise ProviderError(code, status=status) from None
    except TimeoutError:
        raise ProviderError("timeout") from None
    except urllib.error.URLError:
        raise ProviderError("network_error") from None
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ProviderError("invalid_json", status=status) from None
    if not isinstance(payload, dict):
        raise ProviderError("invalid_json", status=status)
    return payload, status, elapsed_ms


def fetch_json(provider: str, operation: str, cache_payload: dict[str, Any], request_factory: Callable[[], urllib.request.Request], *, timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES, urlopen: UrlOpen | None = None) -> tuple[dict[str, Any] | None, ProviderStatus]:
    key = stable_cache_key(provider, operation, cache_payload)
    cached = read_cache(key)
    if cached is not None:
        return cached, ProviderStatus(provider, operation, True, 200, 0, _count_provider_results(provider, operation, cached), "hit")

    last_error = "unknown_error"
    last_status: int | None = None
    for attempt in range(retries + 1):
        try:
            rate_limit(provider)
            payload, status, elapsed_ms = _request_json(request_factory(), timeout, urlopen=urlopen)
            write_cache(key, payload)
            return payload, ProviderStatus(provider, operation, True, status, elapsed_ms, _count_provider_results(provider, operation, payload), "miss")
        except ProviderError as exc:
            last_error = exc.code
            last_status = exc.status
            retryable = exc.code in {"timeout", "network_error", "http_retryable"}
            if not retryable or attempt >= retries:
                break
            time.sleep(min(2.0, 0.35 * (2 ** attempt)))
    return None, ProviderStatus(provider, operation, False, last_status, None, 0, "miss", last_error)


def _count_provider_results(provider: str, operation: str, payload: dict[str, Any]) -> int:
    if provider == "dadata":
        value = payload.get("suggestions")
        return len(value) if isinstance(value, list) else 0
    value = payload.get("features")
    return len(value) if isinstance(value, list) else 0


def quote_query(value: str) -> str:
    return urllib.parse.quote(value.strip(), safe="")


def yandex_place_link(lat: float | None, lon: float | None, text: str) -> str | None:
    if lat is not None and lon is not None:
        return "https://yandex.ru/maps/?" + urllib.parse.urlencode({"ll": f"{lon},{lat}", "z": "16", "text": text})
    if text:
        return "https://yandex.ru/maps/?" + urllib.parse.urlencode({"text": text})
    return None


def twogis_place_link(text: str) -> str | None:
    return f"https://2gis.ru/search/{quote_query(text)}" if text else None


def route_links(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> dict[str, str]:
    yandex = "https://yandex.ru/maps/?" + urllib.parse.urlencode({"rtext": f"{a_lat},{a_lon}~{b_lat},{b_lon}", "rtt": "auto"})
    twogis = f"https://2gis.ru/routeSearch/rsType/car/from/{a_lon},{a_lat}/to/{b_lon},{b_lat}"
    return {"yandex_maps": yandex, "2gis": twogis}


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        result = float(value)
        if math.isfinite(result):
            return result
    except (TypeError, ValueError):
        return None
    return None


def envelope(operation: str, results: list[dict[str, Any]], statuses: list[ProviderStatus], checked: datetime | None = None, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> dict[str, Any]:
    checked = checked or utc_now()
    expires = checked + timedelta(seconds=ttl_seconds)
    return {"ok": any(s.success for s in statuses), "operation": operation, "checked_at": iso(checked), "expires_at": iso(expires), "results": results, "provider_status": [s.to_dict() for s in statuses]}


def base_result(source: str, kind: str, title: str, checked: datetime, confidence: float, verification_status: str) -> dict[str, Any]:
    return {"source": source, "checked_at": iso(checked), "expires_at": iso(checked + timedelta(seconds=DEFAULT_CACHE_TTL_SECONDS)), "confidence": round(max(0.0, min(1.0, confidence)), 3), "verification_status": verification_status, "kind": kind, "title": title, "coordinates": None, "address": None, "administrative": {}, "provider_payload": {}, "deep_links": {}}


def dadata_address(query: str, limit: int = 5, *, timeout: float = DEFAULT_TIMEOUT, urlopen: UrlOpen | None = None) -> dict[str, Any]:
    checked = utc_now()
    token = get_secret("DADATA_API_KEY")
    if not token:
        status = ProviderStatus("dadata", "address", False, None, None, 0, "miss", "missing_api_key")
        return envelope("dadata-address", [], [status], checked)
    body = json.dumps({"query": query, "count": limit}, ensure_ascii=False).encode("utf-8")
    def request_factory() -> urllib.request.Request:
        return urllib.request.Request(DADATA_URL, data=body, headers={"Authorization": f"Token {token}", "Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}, method="POST")
    payload, status = fetch_json("dadata", "address", {"query": query, "count": limit}, request_factory, timeout=timeout, urlopen=urlopen)
    results: list[dict[str, Any]] = []
    if payload:
        for item in payload.get("suggestions") or []:
            if not isinstance(item, dict):
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            lat = as_float(data.get("geo_lat")); lon = as_float(data.get("geo_lon"))
            title = str(item.get("unrestricted_value") or item.get("value") or query)
            qc_geo = str(data.get("qc_geo") or "")
            confidence = 0.95 if qc_geo in {"0", "1"} else 0.75 if lat is not None and lon is not None else 0.5
            result = base_result("dadata", "address", title, checked, confidence, "verified" if lat is not None and lon is not None else "partial")
            result["coordinates"] = {"lat": lat, "lon": lon} if lat is not None and lon is not None else None
            result["address"] = {"formatted": title, "raw": item.get("value")}
            result["administrative"] = {"country": data.get("country"), "region": data.get("region_with_type") or data.get("region"), "area": data.get("area_with_type") or data.get("area"), "city": data.get("city_with_type") or data.get("city"), "settlement": data.get("settlement_with_type") or data.get("settlement"), "street": data.get("street_with_type") or data.get("street"), "house": data.get("house"), "postal_code": data.get("postal_code"), "fias_id": data.get("fias_id"), "fias_level": data.get("fias_level"), "fias_actuality_state": data.get("fias_actuality_state"), "kladr_id": data.get("kladr_id"), "qc_geo": data.get("qc_geo")}
            result["provider_payload"] = {"value": item.get("value"), "unrestricted_value": item.get("unrestricted_value")}
            result["deep_links"] = {"yandex_maps": yandex_place_link(lat, lon, title), "2gis": twogis_place_link(title)}
            results.append(result)
    status.result_count = len(results)
    return envelope("dadata-address", results, [status], checked)


def geoapify_geocode(query: str, limit: int = 5, *, timeout: float = DEFAULT_TIMEOUT, urlopen: UrlOpen | None = None) -> dict[str, Any]:
    checked = utc_now(); key = get_secret("GEOAPIFY_API_KEY")
    if not key:
        status = ProviderStatus("geoapify", "geocode", False, None, None, 0, "miss", "missing_api_key")
        return envelope("geoapify-geocode", [], [status], checked)
    safe_params = {"text": query, "limit": str(limit)}
    def request_factory() -> urllib.request.Request:
        params = dict(safe_params); params["apiKey"] = key
        url = GEOAPIFY_GEOCODE_URL + "?" + urllib.parse.urlencode(params)
        return urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    payload, status = fetch_json("geoapify", "geocode", safe_params, request_factory, timeout=timeout, urlopen=urlopen)
    results = _geoapify_feature_results(payload, "geocode", checked, query) if payload else []
    status.result_count = len(results)
    return envelope("geoapify-geocode", results, [status], checked)


def geoapify_poi(lat: float, lon: float, category: str, radius: int = 1000, limit: int = 10, *, timeout: float = DEFAULT_TIMEOUT, urlopen: UrlOpen | None = None) -> dict[str, Any]:
    checked = utc_now(); key = get_secret("GEOAPIFY_API_KEY")
    if not key:
        status = ProviderStatus("geoapify", "poi", False, None, None, 0, "miss", "missing_api_key")
        return envelope("geoapify-poi", [], [status], checked)
    safe_params = {"categories": category, "filter": f"circle:{lon},{lat},{radius}", "bias": f"proximity:{lon},{lat}", "limit": str(limit)}
    def request_factory() -> urllib.request.Request:
        params = dict(safe_params); params["apiKey"] = key
        url = GEOAPIFY_PLACES_URL + "?" + urllib.parse.urlencode(params)
        return urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    payload, status = fetch_json("geoapify", "poi", safe_params, request_factory, timeout=timeout, urlopen=urlopen)
    results = _geoapify_feature_results(payload, "poi", checked, category) if payload else []
    status.result_count = len(results)
    return envelope("geoapify-poi", results, [status], checked)


def geoapify_route(from_lat: float, from_lon: float, to_lat: float, to_lon: float, mode: str = "drive", *, timeout: float = DEFAULT_TIMEOUT, urlopen: UrlOpen | None = None) -> dict[str, Any]:
    checked = utc_now(); key = get_secret("GEOAPIFY_API_KEY")
    if not key:
        status = ProviderStatus("geoapify", "route", False, None, None, 0, "miss", "missing_api_key")
        return envelope("geoapify-route", [], [status], checked)
    resolved_mode = {"drive": "drive", "walk": "walk", "bicycle": "bicycle"}.get(mode, "drive")
    safe_params = {"waypoints": f"{from_lat},{from_lon}|{to_lat},{to_lon}", "mode": resolved_mode}
    def request_factory() -> urllib.request.Request:
        params = dict(safe_params); params["apiKey"] = key
        url = GEOAPIFY_ROUTE_URL + "?" + urllib.parse.urlencode(params)
        return urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    payload, status = fetch_json("geoapify", "route", safe_params, request_factory, timeout=timeout, urlopen=urlopen)
    results: list[dict[str, Any]] = []
    if payload:
        for feature in payload.get("features") or []:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
            result = base_result("geoapify", "route", f"{resolved_mode} route", checked, 0.85, "verified")
            result["coordinates"] = {"from": {"lat": from_lat, "lon": from_lon}, "to": {"lat": to_lat, "lon": to_lon}}
            result["provider_payload"] = {"distance_m": props.get("distance"), "time_s": props.get("time"), "mode": resolved_mode}
            result["deep_links"] = route_links(from_lat, from_lon, to_lat, to_lon)
            results.append(result)
    status.result_count = len(results)
    return envelope("geoapify-route", results, [status], checked)


def _geoapify_feature_results(payload: dict[str, Any] | None, kind: str, checked: datetime, fallback_title: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not payload:
        return results
    for feature in payload.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        lat = as_float(props.get("lat")); lon = as_float(props.get("lon"))
        if (lat is None or lon is None) and isinstance(feature.get("geometry"), dict):
            coords = feature["geometry"].get("coordinates")
            if isinstance(coords, list) and len(coords) >= 2:
                lon = as_float(coords[0]); lat = as_float(coords[1])
        title = str(props.get("formatted") or props.get("name") or props.get("address_line1") or fallback_title)
        rank = props.get("rank") if isinstance(props.get("rank"), dict) else {}
        confidence = as_float(rank.get("confidence"))
        result = base_result("geoapify", kind, title, checked, confidence if confidence is not None else 0.75, "verified" if lat is not None and lon is not None else "partial")
        result["coordinates"] = {"lat": lat, "lon": lon} if lat is not None and lon is not None else None
        result["address"] = {"formatted": props.get("formatted"), "name": props.get("name"), "street": props.get("street"), "housenumber": props.get("housenumber")}
        result["administrative"] = {"country": props.get("country"), "state": props.get("state"), "city": props.get("city"), "postcode": props.get("postcode")}
        result["provider_payload"] = {"place_id": props.get("place_id"), "categories": props.get("categories"), "distance": props.get("distance")}
        result["deep_links"] = {"yandex_maps": yandex_place_link(lat, lon, title), "2gis": twogis_place_link(title)}
        results.append(result)
    return results


def _status_from_dict(status: dict[str, Any]) -> ProviderStatus:
    return ProviderStatus(provider=status.get("provider"), operation=status.get("operation"), success=status.get("success"), http_status=status.get("http_status"), response_ms=status.get("response_ms"), result_count=status.get("result_count"), cache=status.get("cache"), error_code=status.get("error_code"))


def city(query: str, poi_category: str | None = None, radius: int = 1000, limit: int = 5, *, timeout: float = DEFAULT_TIMEOUT, urlopen: UrlOpen | None = None) -> dict[str, Any]:
    checked = utc_now(); statuses: list[ProviderStatus] = []; results: list[dict[str, Any]] = []
    for section in (dadata_address(query, limit=limit, timeout=timeout, urlopen=urlopen), geoapify_geocode(query, limit=limit, timeout=timeout, urlopen=urlopen)):
        results.extend(section.get("results") or [])
        statuses.extend(_status_from_dict(s) for s in section.get("provider_status") or [])
    if poi_category:
        seed = next((r for r in results if isinstance(r.get("coordinates"), dict) and r["coordinates"].get("lat") is not None and r["coordinates"].get("lon") is not None), None)
        if seed:
            coords = seed["coordinates"]
            poi = geoapify_poi(float(coords["lat"]), float(coords["lon"]), poi_category, radius=radius, limit=limit, timeout=timeout, urlopen=urlopen)
            results.extend(poi.get("results") or [])
            statuses.extend(_status_from_dict(s) for s in poi.get("provider_status") or [])
    return envelope("city", results, statuses, checked)


def self_check() -> dict[str, Any]:
    return {"ok": True, "skill": "city-travel-concierge", "cache_dir": str(cache_dir()), "providers_configured": {"dadata": bool(get_secret("DADATA_API_KEY")), "geoapify": bool(get_secret("GEOAPIFY_API_KEY"))}}


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="City travel concierge helper")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--self-check", action="store_true")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("dadata-address"); p.add_argument("query"); p.add_argument("--limit", type=int, default=5)
    p = sub.add_parser("geoapify-geocode"); p.add_argument("query"); p.add_argument("--limit", type=int, default=5)
    p = sub.add_parser("poi"); p.add_argument("--lat", type=float, required=True); p.add_argument("--lon", type=float, required=True); p.add_argument("--category", required=True); p.add_argument("--radius", type=int, default=1000); p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("route"); p.add_argument("--from-lat", type=float, required=True); p.add_argument("--from-lon", type=float, required=True); p.add_argument("--to-lat", type=float, required=True); p.add_argument("--to-lon", type=float, required=True); p.add_argument("--mode", choices=["drive", "walk", "bicycle"], default="drive")
    p = sub.add_parser("city"); p.add_argument("query"); p.add_argument("--poi-category"); p.add_argument("--radius", type=int, default=1000); p.add_argument("--limit", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_check:
        print_json(self_check()); return 0
    if args.command == "dadata-address": print_json(dadata_address(args.query, limit=args.limit, timeout=args.timeout))
    elif args.command == "geoapify-geocode": print_json(geoapify_geocode(args.query, limit=args.limit, timeout=args.timeout))
    elif args.command == "poi": print_json(geoapify_poi(args.lat, args.lon, args.category, radius=args.radius, limit=args.limit, timeout=args.timeout))
    elif args.command == "route": print_json(geoapify_route(args.from_lat, args.from_lon, args.to_lat, args.to_lon, mode=args.mode, timeout=args.timeout))
    elif args.command == "city": print_json(city(args.query, poi_category=args.poi_category, radius=args.radius, limit=args.limit, timeout=args.timeout))
    else:
        build_parser().print_help(sys.stderr); return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
