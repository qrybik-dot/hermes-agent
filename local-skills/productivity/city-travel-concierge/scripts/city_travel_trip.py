#!/usr/bin/env python3
"""Aggregate route + parking helper for city-travel-concierge.

This entrypoint is intentionally thin: it reuses the existing address,
geocoding, routing, and parking modules so Telegram route requests can finish
in one deterministic tool call instead of asking the model to orchestrate many
provider calls manually.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

import city_travel_concierge as core
import city_travel_itinerary as itinerary
import city_travel_parking as parking


def normalize_trip_query(value: str, *, role: str) -> str:
    text = " ".join((value or "").strip().split())
    if role == "destination":
        text = re.sub(r"(?i)^парка\s+", "парк ", text)
        text = re.sub(r"(?i)^усадьбы\s+", "усадьба ", text)
        text = re.sub(r"(?i)^музея\s+", "музей ", text)
    if role == "start":
        text = re.sub(r"(?i)\bКорол[её]ва\b", "Королёв", text)
        text = re.sub(r"(?i)\bМосквы\b", "Москва", text)
    return text


def _status_items(section: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(section, dict):
        return []
    return [item for item in section.get("provider_status") or [] if isinstance(item, dict)]


def _first_with_coordinates(section: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(section, dict):
        return None
    for item in section.get("results") or []:
        if isinstance(item, dict) and isinstance(item.get("coordinates"), dict):
            coords = item["coordinates"]
            if coords.get("lat") is not None and coords.get("lon") is not None:
                return item
    return None


def _resolve_start(query: str, *, limit: int, timeout: float, urlopen=None) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    statuses: list[dict[str, Any]] = []
    degraded: list[str] = []
    dadata = core.dadata_address(query, limit=limit, timeout=timeout, urlopen=urlopen)
    statuses.extend(_status_items(dadata))
    start = _first_with_coordinates(dadata)
    if start is not None:
        return start, statuses, degraded
    degraded.append("dadata-address")
    geo = core.geoapify_geocode(query, limit=limit, timeout=timeout, urlopen=urlopen)
    statuses.extend(_status_items(geo))
    start = _first_with_coordinates(geo)
    if start is None:
        degraded.append("geoapify-start-geocode")
    return start, statuses, degraded


def _parking_summary(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "Парковки рядом не найдены."
    best = candidates[0]
    title = best.get("title") or "кандидат парковки"
    status = best.get("parking_status") or "unverified"
    if status in {"official", "user_confirmed"} and best.get("is_free") is True:
        prefix = "Есть подтверждённый бесплатный вариант"
    elif status == "likely_free":
        prefix = "Есть кандидат со статусом likely_free, но бесплатность не подтверждена официально"
    elif status == "official" and best.get("is_free") is False:
        prefix = "Ближайший официальный источник указывает платную парковку"
    else:
        prefix = "Есть непроверенный кандидат парковки"
    return f"{prefix}: {title}."


def _build_answer(result: dict[str, Any]) -> str:
    route = result.get("route") if isinstance(result.get("route"), dict) else {}
    distance_m = route.get("distance_m")
    duration_s = route.get("duration_s")
    parts: list[str] = []
    if duration_s is not None:
        minutes = round(float(duration_s) / 60)
        distance_km = round(float(distance_m or 0) / 1000, 1) if distance_m is not None else None
        distance_text = f", {distance_km} км" if distance_km is not None else ""
        parts.append(f"Маршрут: примерно {minutes} мин{distance_text}.")
    else:
        parts.append("Маршрут построить не удалось, но координаты и парковочные кандидаты сохранены частично.")
    if result.get("approximate_start"):
        parts.append(result.get("start_warning") or "Старт без номера дома, точка приблизительная.")
    parts.append("Geoapify не учитывает live traffic; проверьте пробки в Яндекс Картах перед выездом.")
    yandex = ((route.get("deep_links") or {}).get("yandex_maps") if isinstance(route.get("deep_links"), dict) else None)
    if yandex:
        parts.append(f"Яндекс Карты: {yandex}")
    parking_candidates = result.get("parking_candidates") if isinstance(result.get("parking_candidates"), list) else []
    parts.append(_parking_summary(parking_candidates))
    parts.append("Перед парковкой проверьте знаки, разметку, шлагбаум и платную зону на месте.")
    degraded = result.get("degraded_sections") if isinstance(result.get("degraded_sections"), list) else []
    if degraded:
        parts.append("Деградация источников: " + ", ".join(str(item) for item in degraded[:5]) + ".")
    return "\n".join(parts)


def trip(
    *,
    start: str,
    destination: str,
    departure_at: str | None = None,
    parking_radius_m: int = 1200,
    parking_limit: int = 5,
    resolve_limit: int = 3,
    timeout: float = core.DEFAULT_TIMEOUT,
    parking_timeout: float = 3.0,
    urlopen=None,
) -> dict[str, Any]:
    checked = core.utc_now()
    statuses: list[dict[str, Any]] = []
    degraded: list[str] = []
    original_start = start
    original_destination = destination
    start = normalize_trip_query(start, role="start")
    destination = normalize_trip_query(destination, role="destination")

    resolved_start, start_statuses, start_degraded = _resolve_start(
        start, limit=resolve_limit, timeout=timeout, urlopen=urlopen
    )
    statuses.extend(start_statuses)
    degraded.extend(start_degraded)

    dest_section = core.geoapify_geocode(destination, limit=resolve_limit, timeout=timeout, urlopen=urlopen)
    statuses.extend(_status_items(dest_section))
    resolved_destination = _first_with_coordinates(dest_section)
    if resolved_destination is None:
        degraded.append("geoapify-destination-geocode")

    route_item: dict[str, Any] | None = None
    parking_section: dict[str, Any] | None = None
    if resolved_start and resolved_destination:
        start_coords = resolved_start["coordinates"]
        dest_coords = resolved_destination["coordinates"]
        route_section = itinerary.geoapify_route_waypoints(
            [
                {"lat": float(start_coords["lat"]), "lon": float(start_coords["lon"]), "label": start, "role": "start"},
                {"lat": float(dest_coords["lat"]), "lon": float(dest_coords["lon"]), "label": destination, "role": "destination"},
            ],
            timeout=timeout,
            urlopen=urlopen,
        )
        statuses.extend(_status_items(route_section))
        route_item = (route_section.get("results") or [None])[0] if isinstance(route_section, dict) else None
        if not route_item:
            degraded.append("geoapify-route")
        parking_section = parking.parking_nearby(
            float(dest_coords["lat"]),
            float(dest_coords["lon"]),
            radius_m=parking_radius_m,
            limit=parking_limit,
            timeout=parking_timeout,
            urlopen=urlopen,
        )
        statuses.extend(_status_items(parking_section))
        if not (parking_section or {}).get("results"):
            degraded.append("parking-candidates")

    route_payload = {
        "distance_m": (route_item.get("provider_payload") or {}).get("distance_m") if isinstance(route_item, dict) else None,
        "duration_s": (route_item.get("provider_payload") or {}).get("time_s") if isinstance(route_item, dict) else None,
        "traffic_status": "not_available",
        "deep_links": route_item.get("deep_links") if isinstance(route_item, dict) else {},
    }
    parking_candidates = (parking_section or {}).get("results") or []
    result = {
        "ok": bool(resolved_start and resolved_destination),
        "operation": "trip",
        "source": "city-travel-concierge",
        "checked_at": core.iso(checked),
        "start_query": start,
        "destination_query": destination,
        "original_start_query": original_start,
        "original_destination_query": original_destination,
        "departure_at": departure_at,
        "resolved_start": resolved_start,
        "approximate_start": bool((resolved_start or {}).get("approximate_start")),
        "start_warning": (resolved_start or {}).get("precision_warning"),
        "resolved_destination": resolved_destination,
        "coordinates": {
            "start": (resolved_start or {}).get("coordinates"),
            "destination": (resolved_destination or {}).get("coordinates"),
        },
        "route": route_payload,
        "traffic_status": "not_available",
        "parking_candidates": parking_candidates,
        "parking_statuses": sorted({str(item.get("parking_status") or "unverified") for item in parking_candidates if isinstance(item, dict)}),
        "degraded_sections": list(dict.fromkeys(degraded)),
        "provider_status": statuses,
        "answer_ready": bool(route_payload.get("deep_links", {}).get("yandex_maps") and parking_candidates),
    }
    result["answer_text"] = _build_answer(result)
    return result


def print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate route and parking trip helper")
    parser.add_argument("--start", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--departure-at")
    parser.add_argument("--parking-radius", type=int, default=1200)
    parser.add_argument("--parking-limit", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=core.DEFAULT_TIMEOUT)
    parser.add_argument("--parking-timeout", type=float, default=3.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print_json(
        trip(
            start=args.start,
            destination=args.destination,
            departure_at=args.departure_at,
            parking_radius_m=args.parking_radius,
            parking_limit=args.parking_limit,
            timeout=args.timeout,
            parking_timeout=args.parking_timeout,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
