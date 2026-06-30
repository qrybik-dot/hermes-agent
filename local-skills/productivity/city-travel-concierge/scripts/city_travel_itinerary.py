#!/usr/bin/env python3
"""Stage 2 for city-travel-concierge: multi-stop routes, ETA, and weather.

The module reuses the stage-1 helper and intentionally stays stdlib-only.
It never prints API keys, request headers, or full provider URLs.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import city_travel_concierge as core

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_CACHE_TTL_SECONDS = 60 * 60
WEATHER_MIN_INTERVAL_SECONDS = 0.5
UrlOpen = Callable[[urllib.request.Request, float], Any]


def parse_datetime(value: str | datetime | None) -> datetime:
    if value is None:
        return core.utc_now()
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


def status_from_dict(value: dict[str, Any]) -> core.ProviderStatus:
    return core.ProviderStatus(
        provider=str(value.get("provider") or "unknown"),
        operation=str(value.get("operation") or "unknown"),
        success=bool(value.get("success")),
        http_status=value.get("http_status"),
        response_ms=value.get("response_ms"),
        result_count=int(value.get("result_count") or 0),
        cache=str(value.get("cache") or "miss"),
        error_code=value.get("error_code"),
    )


def route_links_multi(points: list[dict[str, Any]]) -> dict[str, str]:
    normalized: list[tuple[float, float]] = []
    for point in points:
        lat = core.as_float(point.get("lat"))
        lon = core.as_float(point.get("lon"))
        if lat is None or lon is None:
            return {}
        normalized.append((lat, lon))
    if len(normalized) < 2:
        return {}
    rtext = "~".join(f"{lat:.6f},{lon:.6f}" for lat, lon in normalized)
    yandex = "https://yandex.ru/maps/?" + urllib.parse.urlencode(
        {"rtext": rtext, "rtt": "auto"}
    )
    first_lat, first_lon = normalized[0]
    last_lat, last_lon = normalized[-1]
    twogis = (
        "https://2gis.ru/routeSearch/rsType/car/"
        f"from/{first_lon:.6f},{first_lat:.6f}/"
        f"to/{last_lon:.6f},{last_lat:.6f}"
    )
    return {"yandex_maps": yandex, "2gis": twogis}


def _normalized_waypoints(
    waypoints: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    normalized: list[dict[str, Any]] = []
    for index, point in enumerate(waypoints):
        lat = core.as_float(point.get("lat"))
        lon = core.as_float(point.get("lon"))
        if lat is None or lon is None:
            return [], "invalid_waypoint"
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return [], "invalid_waypoint"
        normalized.append(
            {
                "lat": lat,
                "lon": lon,
                "label": str(point.get("label") or f"point-{index + 1}"),
                "purpose": point.get("purpose"),
                "dwell_minutes": max(0, int(point.get("dwell_minutes") or 0)),
                "role": point.get("role"),
            }
        )
    if len(normalized) < 2 or len(normalized) > 20:
        return [], "invalid_waypoint_count"
    return normalized, None


def geoapify_route_waypoints(
    waypoints: list[dict[str, Any]],
    mode: str = "drive",
    *,
    timeout: float = core.DEFAULT_TIMEOUT,
    urlopen: UrlOpen | None = None,
) -> dict[str, Any]:
    checked = core.utc_now()
    key = core.get_secret("GEOAPIFY_API_KEY")
    if not key:
        status = core.ProviderStatus(
            "geoapify", "route", False, None, None, 0, "miss", "missing_api_key"
        )
        return core.envelope("geoapify-route-multi", [], [status], checked)

    normalized, error = _normalized_waypoints(waypoints)
    if error:
        status = core.ProviderStatus(
            "geoapify", "route", False, None, None, 0, "miss", error
        )
        return core.envelope("geoapify-route-multi", [], [status], checked)

    resolved_mode = {"drive": "drive", "walk": "walk", "bicycle": "bicycle"}.get(
        mode, "drive"
    )
    safe_params = {
        "waypoints": "|".join(
            f"{point['lat']},{point['lon']}" for point in normalized
        ),
        "mode": resolved_mode,
    }

    def request_factory() -> urllib.request.Request:
        params = dict(safe_params)
        params["apiKey"] = key
        url = core.GEOAPIFY_ROUTE_URL + "?" + urllib.parse.urlencode(params)
        return urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": core.USER_AGENT},
            method="GET",
        )

    payload, status = core.fetch_json(
        "geoapify",
        "route",
        safe_params,
        request_factory,
        timeout=timeout,
        urlopen=urlopen,
    )
    results: list[dict[str, Any]] = []
    if payload:
        for feature in payload.get("features") or []:
            if not isinstance(feature, dict):
                continue
            props = (
                feature.get("properties")
                if isinstance(feature.get("properties"), dict)
                else {}
            )
            distance_m = core.as_float(props.get("distance"))
            duration_s = core.as_float(props.get("time"))
            legs = props.get("legs") if isinstance(props.get("legs"), list) else []
            result = core.base_result(
                "geoapify",
                "route",
                f"{resolved_mode} route",
                checked,
                0.85 if distance_m is not None and duration_s is not None else 0.65,
                "verified"
                if distance_m is not None and duration_s is not None
                else "partial",
            )
            result["coordinates"] = {
                "waypoints": normalized,
                "from": {"lat": normalized[0]["lat"], "lon": normalized[0]["lon"]},
                "to": {"lat": normalized[-1]["lat"], "lon": normalized[-1]["lon"]},
            }
            result["provider_payload"] = {
                "distance_m": distance_m,
                "time_s": duration_s,
                "mode": resolved_mode,
                "legs": legs,
                "waypoint_count": len(normalized),
            }
            result["traffic_status"] = "not_available"
            result["route_notice"] = (
                "Geoapify estimate does not include live traffic. "
                "Check Yandex Maps before departure."
            )
            result["deep_links"] = route_links_multi(normalized)
            results.append(result)
    status.result_count = len(results)
    return core.envelope("geoapify-route-multi", results, [status], checked)


def weather_recommendations(
    *,
    temperature_c: float | None,
    apparent_temperature_c: float | None,
    precipitation_probability_pct: float | None,
    precipitation_mm: float | None,
    weather_code: int | None,
    wind_speed_m_s: float | None,
    wind_gusts_m_s: float | None,
    uv_index: float | None,
) -> list[dict[str, str]]:
    advice: list[dict[str, str]] = []
    code = weather_code if weather_code is not None else -1
    thunder = code in {95, 96, 99}
    wet_code = code in set(range(51, 68)) | set(range(71, 87)) | {80, 81, 82}
    rain = (
        thunder
        or wet_code
        or (
            precipitation_probability_pct is not None
            and precipitation_probability_pct >= 50
        )
        or (precipitation_mm is not None and precipitation_mm >= 0.2)
    )
    heat_candidates = [
        value
        for value in (temperature_c, apparent_temperature_c)
        if value is not None
    ]
    cold_candidates = list(heat_candidates)
    heat_value = max(heat_candidates) if heat_candidates else None
    cold_value = min(cold_candidates) if cold_candidates else None
    strong_wind = (
        (wind_speed_m_s is not None and wind_speed_m_s >= 10.0)
        or (wind_gusts_m_s is not None and wind_gusts_m_s >= 15.0)
    )
    frost = cold_value is not None and cold_value <= -10.0

    if thunder:
        advice.append(
            {
                "code": "thunderstorm",
                "severity": "critical",
                "text": "Исключите открытые площадки и переждите грозу в капитальном помещении.",
            }
        )
    if rain:
        advice.append(
            {
                "code": "rain",
                "severity": "warning",
                "text": "Возьмите зонт; ребёнку пригодится дождевик. Подготовьте крытый план B.",
            }
        )
    if heat_value is not None and heat_value >= 28.0:
        advice.append(
            {
                "code": "heat",
                "severity": "warning",
                "text": "Возьмите воду, планируйте тень и остановки в помещении с кондиционером.",
            }
        )
    if uv_index is not None and uv_index >= 6.0:
        advice.append(
            {
                "code": "high_uv",
                "severity": "warning",
                "text": "Нужны солнцезащитный крем, очки и головной убор.",
            }
        )
    if strong_wind:
        advice.append(
            {
                "code": "strong_wind",
                "severity": "warning",
                "text": "Избегайте открытых набережных, высот и плохо закреплённых конструкций.",
            }
        )
    if frost:
        advice.append(
            {
                "code": "frost",
                "severity": "warning",
                "text": "Сократите пешую часть и добавьте тёплые остановки.",
            }
        )
    if rain or thunder or strong_wind or frost:
        advice.append(
            {
                "code": "indoor_plan_b",
                "severity": "info",
                "text": "Держите запасной крытый вариант: музей, игровую комнату или кафе.",
            }
        )
    if not advice:
        advice.append(
            {
                "code": "normal",
                "severity": "info",
                "text": "Особых погодных ограничений для этой остановки не выявлено.",
            }
        )
    return advice


def _weather_rate_limit() -> None:
    path = core.cache_dir() / ".rate-open-meteo"
    now = time.time()
    try:
        last = float(path.read_text(encoding="utf-8").strip())
    except Exception:
        last = 0.0
    delay = WEATHER_MIN_INTERVAL_SECONDS - (now - last)
    if delay > 0:
        time.sleep(delay)
    try:
        path.write_text(str(time.time()), encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass


def _fetch_weather_payload(
    safe_params: dict[str, Any],
    request_factory: Callable[[], urllib.request.Request],
    *,
    timeout: float,
    retries: int = core.DEFAULT_RETRIES,
    urlopen: UrlOpen | None = None,
) -> tuple[dict[str, Any] | None, core.ProviderStatus]:
    key = core.stable_cache_key("open-meteo", "weather", safe_params)
    cached = core.read_cache(key)
    if cached is not None:
        return cached, core.ProviderStatus(
            "open-meteo", "weather", True, 200, 0, 1, "hit"
        )

    last_error = "unknown_error"
    last_status: int | None = None
    for attempt in range(retries + 1):
        try:
            _weather_rate_limit()
            payload, status, elapsed_ms = core._request_json(
                request_factory(), timeout, urlopen=urlopen
            )
            core.write_cache(key, payload, ttl_seconds=WEATHER_CACHE_TTL_SECONDS)
            return payload, core.ProviderStatus(
                "open-meteo", "weather", True, status, elapsed_ms, 1, "miss"
            )
        except core.ProviderError as exc:
            last_error = exc.code
            last_status = exc.status
            retryable = exc.code in {"timeout", "network_error", "http_retryable"}
            if not retryable or attempt >= retries:
                break
            time.sleep(min(2.0, 0.35 * (2**attempt)))
    return None, core.ProviderStatus(
        "open-meteo",
        "weather",
        False,
        last_status,
        None,
        0,
        "miss",
        last_error,
    )


def _hourly_value(hourly: dict[str, Any], field: str, index: int) -> Any:
    values = hourly.get(field)
    if isinstance(values, list) and 0 <= index < len(values):
        return values[index]
    return None


def open_meteo_weather(
    lat: float,
    lon: float,
    at: str | datetime | None = None,
    *,
    timeout: float = core.DEFAULT_TIMEOUT,
    urlopen: UrlOpen | None = None,
) -> dict[str, Any]:
    checked = core.utc_now()
    target = parse_datetime(at)
    fields = [
        "temperature_2m",
        "apparent_temperature",
        "precipitation_probability",
        "precipitation",
        "weather_code",
        "wind_speed_10m",
        "wind_gusts_10m",
        "visibility",
        "uv_index",
    ]
    request_params = {
        "latitude": f"{lat:.6f}",
        "longitude": f"{lon:.6f}",
        "hourly": ",".join(fields),
        "timezone": "UTC",
        "forecast_days": "16",
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms",
        "precipitation_unit": "mm",
    }
    cache_params = {
        **request_params,
        "target_hour": target.strftime("%Y-%m-%dT%H:00Z"),
    }

    def request_factory() -> urllib.request.Request:
        url = OPEN_METEO_FORECAST_URL + "?" + urllib.parse.urlencode(request_params)
        return urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": core.USER_AGENT},
            method="GET",
        )

    payload, status = _fetch_weather_payload(
        cache_params,
        request_factory,
        timeout=timeout,
        urlopen=urlopen,
    )
    results: list[dict[str, Any]] = []
    if payload:
        hourly = (
            payload.get("hourly") if isinstance(payload.get("hourly"), dict) else {}
        )
        times = hourly.get("time") if isinstance(hourly.get("time"), list) else []
        parsed: list[tuple[int, datetime]] = []
        for index, raw in enumerate(times):
            try:
                parsed.append((index, parse_datetime(str(raw))))
            except (TypeError, ValueError):
                continue
        if parsed:
            index, actual = min(
                parsed, key=lambda item: abs((item[1] - target).total_seconds())
            )
            delta_s = abs((actual - target).total_seconds())
            temperature = core.as_float(_hourly_value(hourly, "temperature_2m", index))
            apparent = core.as_float(
                _hourly_value(hourly, "apparent_temperature", index)
            )
            probability = core.as_float(
                _hourly_value(hourly, "precipitation_probability", index)
            )
            precipitation = core.as_float(
                _hourly_value(hourly, "precipitation", index)
            )
            code_value = core.as_float(_hourly_value(hourly, "weather_code", index))
            weather_code = int(code_value) if code_value is not None else None
            wind = core.as_float(_hourly_value(hourly, "wind_speed_10m", index))
            gusts = core.as_float(_hourly_value(hourly, "wind_gusts_10m", index))
            visibility = core.as_float(_hourly_value(hourly, "visibility", index))
            uv = core.as_float(_hourly_value(hourly, "uv_index", index))
            result = core.base_result(
                "open-meteo",
                "weather",
                f"Weather near {lat:.4f},{lon:.4f}",
                checked,
                0.9 if delta_s <= 3600 else 0.7,
                "verified" if delta_s <= 3 * 3600 else "partial",
            )
            result["expires_at"] = core.iso(
                checked + timedelta(seconds=WEATHER_CACHE_TTL_SECONDS)
            )
            result["actual_at"] = core.iso(actual)
            result["coordinates"] = {"lat": lat, "lon": lon}
            result["provider_payload"] = {
                "temperature_c": temperature,
                "apparent_temperature_c": apparent,
                "precipitation_probability_pct": probability,
                "precipitation_mm": precipitation,
                "weather_code": weather_code,
                "wind_speed_m_s": wind,
                "wind_gusts_m_s": gusts,
                "visibility_m": visibility,
                "uv_index": uv,
                "requested_at": core.iso(target),
                "forecast_delta_s": int(delta_s),
            }
            result["recommendations"] = weather_recommendations(
                temperature_c=temperature,
                apparent_temperature_c=apparent,
                precipitation_probability_pct=probability,
                precipitation_mm=precipitation,
                weather_code=weather_code,
                wind_speed_m_s=wind,
                wind_gusts_m_s=gusts,
                uv_index=uv,
            )
            results.append(result)
    status.result_count = len(results)
    return core.envelope(
        "open-meteo-weather",
        results,
        [status],
        checked,
        ttl_seconds=WEATHER_CACHE_TTL_SECONDS,
    )


def haversine_m(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat1 = math.radians(float(a["lat"]))
    lon1 = math.radians(float(a["lon"]))
    lat2 = math.radians(float(b["lat"]))
    lon2 = math.radians(float(b["lon"]))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(value))


def _provider_leg_times(legs: list[Any], expected: int) -> list[float] | None:
    values: list[float] = []
    for leg in legs:
        if not isinstance(leg, dict):
            return None
        duration = core.as_float(leg.get("time"))
        if duration is None:
            duration = core.as_float(leg.get("duration"))
        if duration is None:
            return None
        values.append(max(0.0, duration))
    return values if len(values) == expected else None


def calculate_eta_schedule(
    points: list[dict[str, Any]],
    total_duration_s: float | None,
    departure_at: str | datetime | None,
    *,
    legs: list[Any] | None = None,
) -> list[dict[str, Any]]:
    if not points:
        return []
    departure = parse_datetime(departure_at)
    segment_count = max(0, len(points) - 1)
    segment_times = _provider_leg_times(legs or [], segment_count)
    method = "provider_legs"
    if segment_times is None:
        distances = [
            haversine_m(points[index], points[index + 1])
            for index in range(segment_count)
        ]
        total_distance = sum(distances)
        if total_duration_s is None:
            segment_times = [0.0] * segment_count
            method = "unavailable"
        elif total_distance > 0:
            segment_times = [
                max(0.0, total_duration_s) * distance / total_distance
                for distance in distances
            ]
            method = "proportional_estimate"
        elif segment_count:
            segment_times = [max(0.0, total_duration_s) / segment_count] * segment_count
            method = "proportional_estimate"
        else:
            segment_times = []
            method = "provider_legs"

    schedule: list[dict[str, Any]] = []
    cursor = departure
    for index, point in enumerate(points):
        if index > 0:
            cursor += timedelta(seconds=segment_times[index - 1])
        item = dict(point)
        item["eta"] = core.iso(cursor)
        item["eta_method"] = method
        item["dwell_minutes"] = max(0, int(point.get("dwell_minutes") or 0))
        schedule.append(item)
        if index < len(points) - 1:
            cursor += timedelta(minutes=item["dwell_minutes"])
    return schedule


def _route_metrics(section: dict[str, Any]) -> dict[str, Any] | None:
    results = section.get("results") if isinstance(section.get("results"), list) else []
    if not results:
        return None
    payload = results[0].get("provider_payload")
    if not isinstance(payload, dict):
        return None
    return {
        "distance_m": core.as_float(payload.get("distance_m")),
        "duration_s": core.as_float(payload.get("time_s")),
        "legs": payload.get("legs") if isinstance(payload.get("legs"), list) else [],
        "deep_links": results[0].get("deep_links") or {},
        "traffic_status": results[0].get("traffic_status") or "not_available",
        "verification_status": results[0].get("verification_status") or "partial",
    }


def _coordinate_pair(value: str) -> dict[str, Any] | None:
    if "," not in value:
        return None
    left, right = value.split(",", 1)
    lat = core.as_float(left.strip())
    lon = core.as_float(right.strip())
    if (
        lat is None
        or lon is None
        or not (-90 <= lat <= 90)
        or not (-180 <= lon <= 180)
    ):
        return None
    return {"lat": lat, "lon": lon, "label": value.strip()}


def resolve_location(
    value: str | dict[str, Any],
    *,
    timeout: float = core.DEFAULT_TIMEOUT,
    urlopen: UrlOpen | None = None,
) -> tuple[dict[str, Any] | None, list[core.ProviderStatus]]:
    statuses: list[core.ProviderStatus] = []
    query = ""
    if isinstance(value, dict):
        source = (
            value.get("coordinates")
            if isinstance(value.get("coordinates"), dict)
            else value
        )
        lat = core.as_float(source.get("lat"))
        lon = core.as_float(source.get("lon"))
        if lat is not None and lon is not None:
            return {
                "lat": lat,
                "lon": lon,
                "label": str(
                    value.get("label") or value.get("title") or f"{lat},{lon}"
                ),
                "purpose": value.get("purpose"),
                "dwell_minutes": max(0, int(value.get("dwell_minutes") or 0)),
            }, statuses
        query = str(value.get("query") or value.get("label") or "").strip()
    else:
        query = value.strip()
        pair = _coordinate_pair(query)
        if pair is not None:
            return pair, statuses
    if not query:
        return None, statuses

    providers = (
        lambda: core.geoapify_geocode(
            query, limit=1, timeout=timeout, urlopen=urlopen
        ),
        lambda: core.dadata_address(query, limit=1, timeout=timeout, urlopen=urlopen),
    )
    for call in providers:
        section = call()
        statuses.extend(
            status_from_dict(item) for item in section.get("provider_status") or []
        )
        for result in section.get("results") or []:
            coords = result.get("coordinates")
            if not isinstance(coords, dict):
                continue
            lat = core.as_float(coords.get("lat"))
            lon = core.as_float(coords.get("lon"))
            if lat is None or lon is None:
                continue
            return {
                "lat": lat,
                "lon": lon,
                "label": str(result.get("title") or query),
                "purpose": value.get("purpose") if isinstance(value, dict) else None,
                "dwell_minutes": max(
                    0,
                    int(value.get("dwell_minutes") or 0)
                    if isinstance(value, dict)
                    else 0,
                ),
            }, statuses
    return None, statuses


def recommend_itinerary(
    *,
    has_stops: bool,
    added_duration_s: float | None,
    priorities: list[str] | None,
    stop_purposes: list[str],
    via_available: bool,
) -> dict[str, Any]:
    normalized = {item.strip().lower() for item in (priorities or []) if item.strip()}
    purposes = {item.strip().lower() for item in stop_purposes if item.strip()}
    if not has_stops:
        return {
            "selected_variant": "direct",
            "reason": "Промежуточные остановки не заданы.",
        }
    if not via_available:
        return {
            "selected_variant": "direct",
            "reason": "Маршрут через остановки недоступен; найденные точки сохранены.",
        }
    if normalized & {"fastest", "minimum_detour", "минимальный крюк", "быстрее"}:
        return {
            "selected_variant": "direct",
            "reason": "Приоритет пользователя — минимальный крюк или время.",
        }
    useful = bool(normalized & purposes) or bool(
        normalized
        & {
            "coffee",
            "children",
            "kids",
            "кофе",
            "дети",
            "minimum_walking",
            "минимум пешком",
        }
    )
    if useful and (added_duration_s is None or added_duration_s <= 30 * 60):
        return {
            "selected_variant": "via_stops",
            "reason": "Остановка соответствует приоритету, а добавленное время не превышает 30 минут.",
        }
    if added_duration_s is not None and added_duration_s <= 15 * 60:
        return {
            "selected_variant": "via_stops",
            "reason": "Крюк небольшой; остановку можно сохранить без существенной потери времени.",
        }
    return {
        "selected_variant": "direct",
        "reason": "Польза остановки не подтверждена при заметном увеличении времени.",
    }


def compose_itinerary(
    start: str | dict[str, Any],
    destination: str | dict[str, Any],
    stops: list[str | dict[str, Any]] | None = None,
    *,
    departure_at: str | datetime | None = None,
    mode: str = "drive",
    priorities: list[str] | None = None,
    timeout: float = core.DEFAULT_TIMEOUT,
    urlopen: UrlOpen | None = None,
) -> dict[str, Any]:
    checked = core.utc_now()
    departure = parse_datetime(departure_at)
    statuses: list[core.ProviderStatus] = []
    unresolved: list[str] = []
    raw_points: list[tuple[str, str | dict[str, Any]]] = [("start", start)]
    raw_points.extend(
        (f"stop-{index + 1}", value) for index, value in enumerate(stops or [])
    )
    raw_points.append(("destination", destination))
    points: list[dict[str, Any]] = []

    for role, value in raw_points:
        point, point_statuses = resolve_location(
            value, timeout=timeout, urlopen=urlopen
        )
        statuses.extend(point_statuses)
        if point is None:
            unresolved.append(role)
            continue
        point["role"] = role
        points.append(point)

    complete = len(points) == len(raw_points)
    direct_metrics: dict[str, Any] | None = None
    via_metrics: dict[str, Any] | None = None
    if complete:
        direct = geoapify_route_waypoints(
            [points[0], points[-1]],
            mode=mode,
            timeout=timeout,
            urlopen=urlopen,
        )
        statuses.extend(
            status_from_dict(item) for item in direct.get("provider_status") or []
        )
        direct_metrics = _route_metrics(direct)
        if len(points) > 2:
            via = geoapify_route_waypoints(
                points, mode=mode, timeout=timeout, urlopen=urlopen
            )
            statuses.extend(
                status_from_dict(item) for item in via.get("provider_status") or []
            )
            via_metrics = _route_metrics(via)
        else:
            via_metrics = direct_metrics

    route_for_eta = via_metrics or direct_metrics
    schedule = calculate_eta_schedule(
        points,
        route_for_eta.get("duration_s") if route_for_eta else None,
        departure,
        legs=route_for_eta.get("legs") if route_for_eta else None,
    )
    weather_failures = 0
    for item in schedule[1:]:
        weather = open_meteo_weather(
            float(item["lat"]),
            float(item["lon"]),
            item["eta"],
            timeout=timeout,
            urlopen=urlopen,
        )
        statuses.extend(
            status_from_dict(value) for value in weather.get("provider_status") or []
        )
        item["weather"] = (weather.get("results") or [None])[0]
        if item["weather"] is None:
            weather_failures += 1

    direct_distance = direct_metrics.get("distance_m") if direct_metrics else None
    direct_duration = direct_metrics.get("duration_s") if direct_metrics else None
    via_distance = via_metrics.get("distance_m") if via_metrics else None
    via_duration = via_metrics.get("duration_s") if via_metrics else None
    added_distance = (
        max(0.0, via_distance - direct_distance)
        if via_distance is not None and direct_distance is not None
        else None
    )
    added_duration = (
        max(0.0, via_duration - direct_duration)
        if via_duration is not None and direct_duration is not None
        else None
    )
    stop_purposes = [
        str(point.get("purpose") or "")
        for point in points
        if str(point.get("role", "")).startswith("stop-")
    ]
    recommendation = recommend_itinerary(
        has_stops=len(points) > 2,
        added_duration_s=added_duration,
        priorities=priorities,
        stop_purposes=stop_purposes,
        via_available=via_metrics is not None,
    )

    degraded: list[str] = []
    if unresolved:
        degraded.append("location_resolution")
    if direct_metrics is None:
        degraded.append("direct_route")
    if len(points) > 2 and via_metrics is None:
        degraded.append("route_via_stops")
    if weather_failures:
        degraded.append("weather")

    return {
        "ok": bool(points),
        "operation": "itinerary",
        "source": "city-travel-concierge",
        "checked_at": core.iso(checked),
        "expires_at": core.iso(
            checked + timedelta(seconds=WEATHER_CACHE_TTL_SECONDS)
        ),
        "confidence": 0.9 if not degraded else 0.65,
        "verification_status": "verified" if not degraded else "partial",
        "traffic_status": "not_available",
        "route_notice": (
            "Geoapify estimate does not include live traffic. "
            "Open the Yandex Maps link before departure."
        ),
        "departure_at": core.iso(departure),
        "mode": mode,
        "resolved_points": schedule,
        "unresolved_roles": unresolved,
        "variants": {
            "direct": direct_metrics,
            "via_stops": via_metrics if len(points) > 2 else None,
        },
        "added_distance_m": added_distance,
        "added_duration_s": added_duration,
        "recommendation": recommendation,
        "degraded_sections": degraded,
        "provider_status": [status.to_dict() for status in statuses],
    }


def parse_location_arg(value: str) -> str | dict[str, Any]:
    pair = _coordinate_pair(value)
    return pair if pair is not None else value


def print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="City itinerary and weather helper")
    parser.add_argument("--timeout", type=float, default=core.DEFAULT_TIMEOUT)
    sub = parser.add_subparsers(dest="command")
    weather = sub.add_parser("weather")
    weather.add_argument("--lat", type=float, required=True)
    weather.add_argument("--lon", type=float, required=True)
    weather.add_argument("--at")
    route = sub.add_parser("route-multi")
    route.add_argument("--waypoint", action="append", required=True)
    route.add_argument(
        "--mode", choices=["drive", "walk", "bicycle"], default="drive"
    )
    itinerary = sub.add_parser("itinerary")
    itinerary.add_argument("--start", required=True)
    itinerary.add_argument("--destination", required=True)
    itinerary.add_argument("--stop", action="append", default=[])
    itinerary.add_argument("--stop-dwell-minutes", type=int, default=0)
    itinerary.add_argument("--departure-at")
    itinerary.add_argument(
        "--mode", choices=["drive", "walk", "bicycle"], default="drive"
    )
    itinerary.add_argument("--priority", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "weather":
        print_json(
            open_meteo_weather(
                args.lat, args.lon, args.at, timeout=args.timeout
            )
        )
        return 0
    if args.command == "route-multi":
        points = [parse_location_arg(value) for value in args.waypoint]
        if not all(isinstance(point, dict) for point in points):
            raise SystemExit("route-multi waypoints must use 'lat,lon'")
        print_json(
            geoapify_route_waypoints(
                points, mode=args.mode, timeout=args.timeout
            )
        )
        return 0
    if args.command == "itinerary":
        parsed_stops: list[dict[str, Any]] = []
        for value in args.stop:
            parsed = parse_location_arg(value)
            if isinstance(parsed, dict):
                stop = dict(parsed)
            else:
                stop = {"query": parsed, "label": parsed}
            stop["dwell_minutes"] = max(0, args.stop_dwell_minutes)
            parsed_stops.append(stop)
        print_json(
            compose_itinerary(
                parse_location_arg(args.start),
                parse_location_arg(args.destination),
                parsed_stops,
                departure_at=args.departure_at,
                mode=args.mode,
                priorities=args.priority,
                timeout=args.timeout,
            )
        )
        return 0
    build_parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
