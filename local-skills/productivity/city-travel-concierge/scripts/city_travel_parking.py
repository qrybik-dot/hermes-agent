#!/usr/bin/env python3
"""Parking candidate classification for city-travel-concierge.

OpenStreetMap is used only to discover candidate parking locations. OSM tags do
not prove current legality, tariff, access, or availability. The helper never
calls an OSM `fee=no` object definitively free: it receives `likely_free` until
confirmed by an official source or a recent user observation.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
import urllib.parse
import urllib.request

import city_travel_concierge as core
import city_travel_discovery as discovery

UrlOpen = Callable[[urllib.request.Request, float], Any]

RESTRICTED_ACCESS = {
    "private",
    "customers",
    "permit",
    "delivery",
    "destination",
    "no",
    "residents",
}

STATUS_PRIORITY = {
    "official": 0,
    "user_confirmed": 1,
    "likely_free": 2,
    "unverified": 3,
}

OFFICIAL_PAID_PARKING_URL = "https://api.crftr.net/open/rawapi/v3/datamos/paidparkingontheroadnetwork"
OFFICIAL_SOURCE_NAME = "data.mos.ru: Платные парковки на улично-дорожной сети"
OFFICIAL_SOURCE_URL = "https://data.mos.ru/opendata/623"
OFFICIAL_MATCH_RADIUS_M = 80
USER_CONFIRMATION_TTL_DAYS = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    value = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(value))


def _candidate_coords(candidate: dict[str, Any]) -> tuple[float, float] | None:
    coords = candidate.get("coordinates")
    if not isinstance(coords, dict):
        return None
    lat = core.as_float(coords.get("lat"))
    lon = core.as_float(coords.get("lon"))
    if lat is None or lon is None:
        return None
    return lat, lon


def user_confirmations_path() -> Path:
    root = core.hermes_home() / "state" / "city-travel-concierge"
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root / "parking_confirmations.json"


def _stable_place_id(lat: float, lon: float) -> str:
    return f"{round(lat, 5):.5f},{round(lon, 5):.5f}"


def load_user_confirmations(path: Path | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    path = path or user_confirmations_path()
    now = now or _utc_now()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = payload.get("confirmations") if isinstance(payload, dict) else {}
    if not isinstance(items, dict):
        return {}
    fresh: dict[str, Any] = {}
    for key, value in items.items():
        if not isinstance(value, dict):
            continue
        checked_at = _parse_time(value.get("checked_at"))
        expires_at = _parse_time(value.get("expires_at"))
        if checked_at is None:
            continue
        if expires_at is None:
            expires_at = checked_at + timedelta(days=USER_CONFIRMATION_TTL_DAYS)
        if expires_at <= now:
            continue
        fresh[str(key)] = {**value, "expires_at": core.iso(expires_at)}
    return fresh


def save_user_confirmation(
    lat: float,
    lon: float,
    *,
    signs_present: bool,
    barrier_present: bool,
    paid: bool,
    parked_successfully: bool,
    checked_at: datetime | None = None,
    ttl_days: int = USER_CONFIRMATION_TTL_DAYS,
    path: Path | None = None,
) -> dict[str, Any]:
    checked_at = checked_at or _utc_now()
    expires_at = checked_at + timedelta(days=max(1, int(ttl_days)))
    path = path or user_confirmations_path()
    place_id = _stable_place_id(lat, lon)
    current = load_user_confirmations(path, now=checked_at)
    current[place_id] = {
        "place_id": place_id,
        "coordinates": {"lat": round(float(lat), 6), "lon": round(float(lon), 6)},
        "checked_at": core.iso(checked_at),
        "expires_at": core.iso(expires_at),
        "signs_present": bool(signs_present),
        "barrier_present": bool(barrier_present),
        "paid": bool(paid),
        "parked_successfully": bool(parked_successfully),
    }
    payload = {"confirmations": current}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)
    path.chmod(0o600)
    return current[place_id]


def _matching_user_confirmation(candidate: dict[str, Any], confirmations: dict[str, Any]) -> dict[str, Any] | None:
    coords = _candidate_coords(candidate)
    if coords is None:
        return None
    place_id = _stable_place_id(*coords)
    direct = confirmations.get(place_id)
    if isinstance(direct, dict):
        return direct
    best: tuple[float, dict[str, Any]] | None = None
    for value in confirmations.values():
        if not isinstance(value, dict) or not isinstance(value.get("coordinates"), dict):
            continue
        lat = core.as_float(value["coordinates"].get("lat"))
        lon = core.as_float(value["coordinates"].get("lon"))
        if lat is None or lon is None:
            continue
        distance = _haversine_m(coords[0], coords[1], lat, lon)
        if distance <= 35 and (best is None or distance < best[0]):
            best = (distance, value)
    return best[1] if best else None


def _normalize_official_record(record: dict[str, Any], checked_at: str) -> dict[str, Any] | None:
    lat = core.as_float(record.get("Latitude_WGS84"))
    lon = core.as_float(record.get("Longitude_WGS84"))
    if (lat is None or lon is None) and isinstance(record.get("geodata_center"), dict):
        value = str(record["geodata_center"].get("value") or "")
        parts = value.replace(",", " ").split()
        if len(parts) >= 2:
            lon = core.as_float(parts[0])
            lat = core.as_float(parts[1])
    if lat is None or lon is None:
        return None
    return {
        "source": "official",
        "source_name": OFFICIAL_SOURCE_NAME,
        "source_url": OFFICIAL_SOURCE_URL,
        "checked_at": checked_at,
        "parking_name": record.get("ParkingName"),
        "zone_number": record.get("ParkingZoneNumber"),
        "tariffs": record.get("Tariffs") if isinstance(record.get("Tariffs"), list) else [],
        "hours": "См. Tariffs.TimeRange официального набора.",
        "coordinates": {"lat": lat, "lon": lon},
        "address": record.get("Address"),
        "global_id": record.get("global_id"),
    }


def official_paid_parking_nearby(
    lat: float,
    lon: float,
    *,
    radius_m: int = 1500,
    limit: int = 200,
    timeout: float = core.DEFAULT_TIMEOUT,
    urlopen: UrlOpen | None = None,
) -> dict[str, Any]:
    checked = core.utc_now()
    safe_params = {"limit": str(max(1, min(int(limit), 5000)))}
    cache_key = core.stable_cache_key("data.mos.ru", "paid-parking", safe_params)
    cached = core.read_cache(cache_key)
    status: dict[str, Any]
    payload: dict[str, Any] | None = cached
    if cached is not None:
        status = {"provider": "data.mos.ru", "operation": "paid-parking", "success": True, "http_status": 200, "response_ms": 0, "result_count": 0, "cache": "hit", "source_url": OFFICIAL_SOURCE_URL}
    else:
        url = OFFICIAL_PAID_PARKING_URL + "?" + urllib.parse.urlencode(safe_params)
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": core.USER_AGENT}, method="GET")
        try:
            payload, http_status, elapsed_ms = core._request_json(request, timeout, urlopen=urlopen)
            core.write_cache(cache_key, payload, ttl_seconds=24 * 60 * 60)
            status = {"provider": "data.mos.ru", "operation": "paid-parking", "success": True, "http_status": http_status, "response_ms": elapsed_ms, "result_count": 0, "cache": "miss", "source_url": OFFICIAL_SOURCE_URL}
        except core.ProviderError as exc:
            return {
                "ok": False,
                "operation": "official-paid-parking",
                "checked_at": core.iso(checked),
                "expires_at": core.iso(checked + timedelta(hours=1)),
                "results": [],
                "provider_status": [{"provider": "data.mos.ru", "operation": "paid-parking", "success": False, "http_status": exc.status, "response_ms": None, "result_count": 0, "cache": "miss", "error_code": exc.code, "source_url": OFFICIAL_SOURCE_URL}],
            }
    records = payload.get("_items") if isinstance(payload, dict) else []
    if not isinstance(records, list):
        records = []
    checked_iso = core.iso(checked)
    matches: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        normalized = _normalize_official_record(record, checked_iso)
        if normalized is None:
            continue
        coords = normalized["coordinates"]
        distance = _haversine_m(lat, lon, coords["lat"], coords["lon"])
        if distance <= max(OFFICIAL_MATCH_RADIUS_M, int(radius_m)):
            normalized["distance_m"] = round(distance)
            matches.append(normalized)
    matches.sort(key=lambda item: item["distance_m"])
    status["result_count"] = len(matches)
    return {
        "ok": bool(matches),
        "operation": "official-paid-parking",
        "checked_at": checked_iso,
        "expires_at": core.iso(checked + timedelta(hours=24)),
        "results": matches,
        "provider_status": [status],
    }


def _matching_official_record(candidate: dict[str, Any], official_records: list[dict[str, Any]]) -> dict[str, Any] | None:
    coords = _candidate_coords(candidate)
    if coords is None:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for record in official_records:
        if not isinstance(record.get("coordinates"), dict):
            continue
        lat = core.as_float(record["coordinates"].get("lat"))
        lon = core.as_float(record["coordinates"].get("lon"))
        if lat is None or lon is None:
            continue
        distance = _haversine_m(coords[0], coords[1], lat, lon)
        if distance <= OFFICIAL_MATCH_RADIUS_M and (best is None or distance < best[0]):
            best = (distance, record)
    return best[1] if best else None


def classify_parking_candidate(
    candidate: dict[str, Any],
    *,
    official_free: bool | None = None,
    official_record: dict[str, Any] | None = None,
    user_confirmed_free: bool | None = None,
    user_confirmation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy with a conservative parking confidence classification."""
    item = dict(candidate)
    payload = dict(item.get("provider_payload") or {})
    access = str(payload.get("access") or "").strip().lower()
    fee = str(payload.get("fee") or "").strip().lower()
    parking_type = str(payload.get("parking") or "").strip().lower()

    restricted = access in RESTRICTED_ACCESS
    status = "unverified"
    free_value: bool | None = None
    reason = "Нет свежего подтверждения тарифа и правил парковки."

    if official_record is not None:
        status = "official"
        free_value = False
        reason = "Официальный городской источник указывает платную парковку."
    elif official_free is not None:
        status = "official"
        free_value = bool(official_free)
        reason = (
            "Тариф подтверждён официальным источником."
            if official_free
            else "Официальный источник указывает платную парковку."
        )
    elif user_confirmation is not None:
        status = "user_confirmed"
        free_value = not bool(user_confirmation.get("paid"))
        reason = (
            "Пользователь недавно подтвердил успешную парковку без оплаты и шлагбаума."
            if free_value
            else "Пользователь недавно подтвердил платность или ограничение."
        )
    elif user_confirmed_free is not None:
        status = "user_confirmed"
        free_value = bool(user_confirmed_free)
        reason = (
            "Пользователь недавно подтвердил отсутствие оплаты и ограничений."
            if user_confirmed_free
            else "Пользователь недавно подтвердил оплату или ограничение доступа."
        )
    elif restricted:
        reason = f"OSM указывает ограниченный доступ: {access}."
    elif fee in {"yes", "paid", "interval"}:
        free_value = False
        reason = "OSM сообщает о плате, но актуальный тариф нужно проверить."
    elif fee == "no":
        status = "likely_free"
        reason = "В OSM стоит fee=no, но знаки, зона и шлагбаум не проверены."

    item["parking_status"] = status
    item["is_free"] = free_value
    item["eligible_for_recommendation"] = not restricted
    item["parking_evidence"] = {
        "source": item.get("source"),
        "fee": fee or None,
        "access": access or None,
        "parking_type": parking_type or None,
        "reason": reason,
        "checked_at": item.get("checked_at"),
    }
    if official_record is not None:
        item["parking_evidence"]["official"] = {
            "source_name": official_record.get("source_name"),
            "source_url": official_record.get("source_url"),
            "zone_number": official_record.get("zone_number"),
            "parking_name": official_record.get("parking_name"),
            "tariffs": official_record.get("tariffs"),
            "hours": official_record.get("hours"),
            "checked_at": official_record.get("checked_at"),
        }
    if user_confirmation is not None:
        item["parking_evidence"]["user_confirmation"] = {
            "checked_at": user_confirmation.get("checked_at"),
            "expires_at": user_confirmation.get("expires_at"),
            "signs_present": user_confirmation.get("signs_present"),
            "barrier_present": user_confirmation.get("barrier_present"),
            "paid": user_confirmation.get("paid"),
            "parked_successfully": user_confirmation.get("parked_successfully"),
        }
    item["parking_warning"] = (
        "Перед парковкой проверьте дорожные знаки, разметку, шлагбаум и платную зону в Яндекс Картах."
    )
    item["verification_status"] = status
    return item


def rank_parking_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda item: (
            not bool(item.get("eligible_for_recommendation")),
            STATUS_PRIORITY.get(str(item.get("parking_status") or "unverified"), 9),
            item.get("distance_m") is None,
            item.get("distance_m") or 0,
            item.get("title") or "",
        ),
    )


def parking_nearby(
    lat: float,
    lon: float,
    *,
    radius_m: int = 1500,
    limit: int = 10,
    include_restricted: bool = False,
    timeout: float = core.DEFAULT_TIMEOUT,
    urlopen: UrlOpen | None = None,
    endpoints: tuple[tuple[str, str], ...] = discovery.OVERPASS_ENDPOINTS,
    confirmations_path: Path | None = None,
) -> dict[str, Any]:
    requested = max(1, min(50, int(limit)))
    raw = discovery.overpass_nearby(
        lat,
        lon,
        ["parking"],
        radius_m=radius_m,
        limit=max(requested * 3, requested),
        timeout=timeout,
        urlopen=urlopen,
        endpoints=endpoints,
    )
    official = official_paid_parking_nearby(lat, lon, radius_m=radius_m, timeout=timeout, urlopen=urlopen)
    official_records = list(official.get("results") or [])
    confirmations = load_user_confirmations(confirmations_path)
    classified = [
        classify_parking_candidate(
            item,
            official_record=_matching_official_record(item, official_records),
            user_confirmation=_matching_user_confirmation(item, confirmations),
        )
        for item in raw.get("results") or []
    ]
    if not include_restricted:
        classified = [item for item in classified if item["eligible_for_recommendation"]]
    ranked = rank_parking_candidates(classified)[:requested]
    statuses = list(raw.get("provider_status") or []) + list(official.get("provider_status") or [])
    for status in statuses:
        if status.get("success"):
            status["result_count"] = len(ranked)
    return {
        "ok": bool(ranked),
        "operation": "parking-nearby",
        "checked_at": raw.get("checked_at"),
        "expires_at": raw.get("expires_at"),
        "results": ranked,
        "provider_status": statuses,
        "metadata": {
            "radius_m": max(50, min(discovery.MAX_OVERPASS_RADIUS_M, int(radius_m))),
            "statuses": ["official", "user_confirmed", "likely_free", "unverified"],
            "free_claim_policy": "Only official or user_confirmed can be called definitely free.",
            "official_source": OFFICIAL_SOURCE_URL,
            "official_matches": len(official_records),
        },
    }


def print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Conservative nearby parking candidates")
    parser.add_argument("--timeout", type=float, default=core.DEFAULT_TIMEOUT)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--radius", type=int, default=1500)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--include-restricted", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print_json(
        parking_nearby(
            args.lat,
            args.lon,
            radius_m=args.radius,
            limit=args.limit,
            include_restricted=args.include_restricted,
            timeout=args.timeout,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
