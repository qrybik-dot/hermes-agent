import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import city_travel_concierge as core
import city_travel_itinerary as stage2


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _limit=-1):
        return json.dumps(self.payload).encode("utf-8")


class Stage2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "HERMES_HOME": self.tmp.name,
                "GEOAPIFY_API_KEY": "mock-key",
                "DADATA_API_KEY": "mock-key",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def fake_urlopen(self, req, timeout):
        parsed = urllib.parse.urlparse(req.full_url)
        if "open-meteo.com" in parsed.netloc:
            return FakeResponse(
                {
                    "hourly": {
                        "time": [
                            "2026-07-01T10:00",
                            "2026-07-01T11:00",
                            "2026-07-01T12:00",
                        ],
                        "temperature_2m": [20, 29, 30],
                        "apparent_temperature": [20, 31, 32],
                        "precipitation_probability": [10, 60, 70],
                        "precipitation": [0, 0.5, 1.0],
                        "weather_code": [1, 61, 95],
                        "wind_speed_10m": [2, 11, 12],
                        "wind_gusts_10m": [4, 16, 18],
                        "visibility": [10000, 8000, 5000],
                        "uv_index": [2, 7, 8],
                    }
                }
            )
        if "/v1/routing" in parsed.path:
            params = urllib.parse.parse_qs(parsed.query)
            waypoint_count = len(params["waypoints"][0].split("|"))
            if waypoint_count == 2:
                properties = {
                    "distance": 1000,
                    "time": 600,
                    "legs": [{"time": 600}],
                }
            else:
                properties = {
                    "distance": 1400,
                    "time": 900,
                    "legs": [{"time": 300}, {"time": 600}],
                }
            return FakeResponse(
                {
                    "features": [
                        {
                            "type": "Feature",
                            "properties": properties,
                        }
                    ]
                }
            )
        if "/v1/geocode/search" in parsed.path:
            return FakeResponse(
                {
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {
                                "formatted": "Test place",
                                "lat": 55.75,
                                "lon": 37.61,
                                "rank": {"confidence": 0.9},
                            },
                        }
                    ]
                }
            )
        if "suggestions.dadata" in parsed.netloc:
            return FakeResponse({"suggestions": []})
        raise AssertionError(f"unexpected request: {parsed.netloc}{parsed.path}")

    def test_weather_rules_boundary(self):
        advice = stage2.weather_recommendations(
            temperature_c=28,
            apparent_temperature_c=29,
            precipitation_probability_pct=50,
            precipitation_mm=0.2,
            weather_code=95,
            wind_speed_m_s=10,
            wind_gusts_m_s=15,
            uv_index=6,
        )
        codes = [item["code"] for item in advice]
        self.assertEqual(
            codes,
            [
                "thunderstorm",
                "rain",
                "heat",
                "high_uv",
                "strong_wind",
                "indoor_plan_b",
            ],
        )

    def test_normal_weather_rule(self):
        advice = stage2.weather_recommendations(
            temperature_c=20,
            apparent_temperature_c=20,
            precipitation_probability_pct=10,
            precipitation_mm=0,
            weather_code=1,
            wind_speed_m_s=2,
            wind_gusts_m_s=4,
            uv_index=2,
        )
        self.assertEqual([item["code"] for item in advice], ["normal"])

    def test_multi_route_link_contains_all_points(self):
        links = stage2.route_links_multi(
            [
                {"lat": 55.1, "lon": 37.1},
                {"lat": 55.2, "lon": 37.2},
                {"lat": 55.3, "lon": 37.3},
            ]
        )
        query = urllib.parse.parse_qs(urllib.parse.urlparse(links["yandex_maps"]).query)
        self.assertEqual(len(query["rtext"][0].split("~")), 3)

    def test_eta_uses_provider_legs_and_dwell(self):
        points = [
            {"lat": 55.1, "lon": 37.1, "label": "A", "dwell_minutes": 0},
            {"lat": 55.2, "lon": 37.2, "label": "B", "dwell_minutes": 10},
            {"lat": 55.3, "lon": 37.3, "label": "C", "dwell_minutes": 0},
        ]
        schedule = stage2.calculate_eta_schedule(
            points,
            900,
            "2026-07-01T10:00:00Z",
            legs=[{"time": 300}, {"time": 600}],
        )
        self.assertEqual(schedule[1]["eta"], "2026-07-01T10:05:00Z")
        self.assertEqual(schedule[2]["eta"], "2026-07-01T10:25:00Z")
        self.assertEqual(schedule[2]["eta_method"], "provider_legs")

    def test_weather_mock(self):
        result = stage2.open_meteo_weather(
            55.75,
            37.61,
            "2026-07-01T11:20:00Z",
            urlopen=self.fake_urlopen,
        )
        self.assertTrue(result["ok"])
        item = result["results"][0]
        self.assertEqual(item["source"], "open-meteo")
        self.assertEqual(item["actual_at"], "2026-07-01T11:00:00Z")
        codes = [entry["code"] for entry in item["recommendations"]]
        self.assertIn("rain", codes)
        self.assertIn("high_uv", codes)

    def test_route_multi_mock_and_no_live_traffic(self):
        result = stage2.geoapify_route_waypoints(
            [
                {"lat": 55.1, "lon": 37.1},
                {"lat": 55.2, "lon": 37.2},
                {"lat": 55.3, "lon": 37.3},
            ],
            urlopen=self.fake_urlopen,
        )
        self.assertTrue(result["ok"])
        item = result["results"][0]
        self.assertEqual(item["provider_payload"]["distance_m"], 1400)
        self.assertEqual(item["traffic_status"], "not_available")
        self.assertEqual(item["provider_payload"]["waypoint_count"], 3)

    def test_itinerary_compares_routes_and_weather(self):
        result = stage2.compose_itinerary(
            {"lat": 55.1, "lon": 37.1, "label": "Start"},
            {"lat": 55.3, "lon": 37.3, "label": "Finish"},
            [
                {
                    "lat": 55.2,
                    "lon": 37.2,
                    "label": "Coffee",
                    "purpose": "coffee",
                    "dwell_minutes": 10,
                }
            ],
            departure_at="2026-07-01T10:00:00Z",
            priorities=["coffee"],
            urlopen=self.fake_urlopen,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["added_distance_m"], 400)
        self.assertEqual(result["added_duration_s"], 300)
        self.assertEqual(
            result["recommendation"]["selected_variant"], "via_stops"
        )
        self.assertEqual(result["traffic_status"], "not_available")
        self.assertEqual(len(result["resolved_points"]), 3)
        self.assertTrue(result["resolved_points"][1]["weather"])
        self.assertEqual(result["degraded_sections"], [])

    def test_weather_failure_does_not_break_route(self):
        def weather_fails(req, timeout):
            if "open-meteo.com" in urllib.parse.urlparse(req.full_url).netloc:
                raise urllib.error.URLError("offline")
            return self.fake_urlopen(req, timeout)

        result = stage2.compose_itinerary(
            {"lat": 55.1, "lon": 37.1},
            {"lat": 55.3, "lon": 37.3},
            [{"lat": 55.2, "lon": 37.2}],
            departure_at="2026-07-01T10:00:00Z",
            urlopen=weather_fails,
        )
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["variants"]["direct"])
        self.assertIn("weather", result["degraded_sections"])
        self.assertIsNone(result["resolved_points"][1]["weather"])

    def test_route_failure_keeps_points(self):
        def route_fails(req, timeout):
            if "/v1/routing" in urllib.parse.urlparse(req.full_url).path:
                raise urllib.error.URLError("offline")
            return self.fake_urlopen(req, timeout)

        result = stage2.compose_itinerary(
            {"lat": 55.1, "lon": 37.1},
            {"lat": 55.3, "lon": 37.3},
            [{"lat": 55.2, "lon": 37.2}],
            departure_at="2026-07-01T10:00:00Z",
            urlopen=route_fails,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["resolved_points"]), 3)
        self.assertIsNone(result["variants"]["direct"])
        self.assertIn("direct_route", result["degraded_sections"])
        self.assertIn("route_via_stops", result["degraded_sections"])

    def test_priority_fastest_prefers_direct(self):
        recommendation = stage2.recommend_itinerary(
            has_stops=True,
            added_duration_s=60,
            priorities=["fastest"],
            stop_purposes=["coffee"],
            via_available=True,
        )
        self.assertEqual(recommendation["selected_variant"], "direct")


if __name__ == "__main__":
    unittest.main()
