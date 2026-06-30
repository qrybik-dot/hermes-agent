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
from typing import Any, Callable
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


def classify_parking_candidate(
    candidate: dict[str, Any],
    *,
    official_free: bool | None = None,
    user_confirmed_free: bool | None = None,
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

    if official_free is not None:
        status = "official"
        free_value = bool(official_free)
        reason = (
            "Тариф подтверждён официальным источником."
            if official_free
            else "Официальный источник указывает платную парковку."
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
    classified = [classify_parking_candidate(item) for item in raw.get("results") or []]
    if not include_restricted:
        classified = [item for item in classified if item["eligible_for_recommendation"]]
    ranked = rank_parking_candidates(classified)[:requested]
    statuses = list(raw.get("provider_status") or [])
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
