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
import city_travel_discovery as stage3


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeResponse:
    def __init__(self, payload, status=200, content_type="application/json"):
        self.payload = payload
        self.status = status
        self.headers = FakeHeaders({"Content-Type": content_type})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit=-1):
        if isinstance(self.payload, bytes):
            return self.payload[:limit] if limit and limit > 0 else self.payload
        raw = json.dumps(self.payload, ensure_ascii=False).encode("utf-8")
        return raw[:limit] if limit and limit > 0 else raw


class Stage3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"HERMES_HOME": self.tmp.name}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def fake_urlopen(self, req, timeout):
        parsed = urllib.parse.urlparse(req.full_url)
        if "kudago.com" in parsed.netloc:
            return FakeResponse(
                {
                    "count": 2,
                    "results": [
                        {
                            "id": 1,
                            "title": "Семейный фестиваль",
                            "slug": "family-fest",
                            "dates": [{"start": 1782900000, "end": 1782907200}],
                            "place": {
                                "title": "Парк",
                                "address": "Москва",
                                "coords": {"lat": 55.7559, "lon": 37.6177},
                            },
                            "location": {"slug": "msk", "name": "Москва"},
                            "categories": [{"slug": "kids", "name": "Детям"}],
                            "age_restriction": "6+",
                            "price": "Бесплатно",
                            "is_free": True,
                            "site_url": "https://kudago.com/msk/event/family-fest/",
                            "tags": ["детям"],
                            "description": "Событие для семьи",
                            "favorites_count": 12,
                            "comments_count": 3,
                        },
                        {
                            "id": 2,
                            "title": "Ночной концерт",
                            "slug": "night-concert",
                            "dates": [],
                            "place": {},
                            "location": {"slug": "msk", "name": "Москва"},
                            "categories": [{"slug": "concert", "name": "Концерты"}],
                            "age_restriction": "18+",
                            "is_free": False,
                            "site_url": "https://kudago.com/msk/event/night-concert/",
                            "tags": [],
                        },
                    ],
                }
            )
        if "overpass" in parsed.path or "overpass" in parsed.netloc:
            return FakeResponse(
                {
                    "version": 0.6,
                    "osm3s": {"timestamp_osm_base": "2026-07-01T00:00:00Z"},
                    "elements": [
                        {
                            "type": "node",
                            "id": 101,
                            "lat": 55.7560,
                            "lon": 37.6178,
                            "tags": {
                                "amenity": "toilets",
                                "name": "Городской туалет",
                                "fee": "no",
                                "wheelchair": "yes",
                            },
                        },
                        {
                            "type": "way",
                            "id": 102,
                            "center": {"lat": 55.7562, "lon": 37.6180},
                            "tags": {
                                "amenity": "parking",
                                "name": "Парковка",
                                "fee": "no",
                            },
                        },
                    ],
                }
            )
        raise AssertionError(f"unexpected URL {req.full_url}")

    def test_overpass_query_is_bounded_and_allowlisted(self):
        query, selected, radius = stage3.build_overpass_query(
            55.75, 37.61, 999999, ["toilets", "unknown", "parking"]
        )
        self.assertEqual(radius, stage3.MAX_OVERPASS_RADIUS_M)
        self.assertEqual(selected, ["toilets", "parking"])
        self.assertIn('["amenity"="toilets"]', query)
        self.assertNotIn("unknown", query)

    def test_overpass_normalizes_and_marks_parking_unverified(self):
        result = stage3.overpass_nearby(
            55.7558,
            37.6176,
            ["toilets", "parking"],
            urlopen=self.fake_urlopen,
            endpoints=(("mock", "https://overpass.test/api/interpreter"),),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["results"]), 2)
        parking = next(item for item in result["results"] if "parking" in item["categories"])
        self.assertEqual(parking["verification_status"], "unverified")
        self.assertIn("openstreetmap.org/way/102", parking["source_url"])
        self.assertEqual(parking["provider_payload"]["fee"], "no")

    def test_kudago_children_and_attribution(self):
        result = stage3.kudago_events(
            start="2026-07-01T00:00:00Z",
            end="2026-07-02T00:00:00Z",
            children_only=True,
            free_only=True,
            lat=55.7558,
            lon=37.6176,
            radius_m=2000,
            urlopen=self.fake_urlopen,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["results"]), 1)
        item = result["results"][0]
        self.assertTrue(item["is_free"])
        self.assertTrue(item["provider_payload"]["for_children"])
        self.assertTrue(item["attribution"]["required"])
        self.assertEqual(item["source_url"], item["attribution"]["url"])

    def test_kudago_request_contains_supported_filters(self):
        seen = {}

        def capture(req, timeout):
            seen.update(urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query))
            return self.fake_urlopen(req, timeout)

        stage3.kudago_events(
            start="2026-07-01T00:00:00Z",
            end="2026-07-03T00:00:00Z",
            location="msk",
            categories=["exhibition", "kids"],
            free_only=True,
            lat=55.75,
            lon=37.61,
            radius_m=1500,
            urlopen=capture,
        )
        self.assertEqual(seen["location"], ["msk"])
        self.assertEqual(seen["is_free"], ["1"])
        self.assertEqual(seen["categories"], ["exhibition,kids"])
        self.assertEqual(seen["radius"], ["1500"])

    def test_dedup_merges_sources_with_same_title_and_coordinates(self):
        left = {
            "title": "Музей Тест",
            "source": "openstreetmap",
            "source_url": "https://www.openstreetmap.org/node/1",
            "coordinates": {"lat": 55.75, "lon": 37.61},
            "confidence": 0.7,
            "categories": ["museums"],
            "provider_payload": {},
        }
        right = {
            "title": "Музей тест",
            "source": "kudago",
            "source_url": "https://kudago.com/test",
            "coordinates": {"lat": 55.7501, "lon": 37.6101},
            "confidence": 0.9,
            "categories": ["Музей"],
            "provider_payload": {},
        }
        result = stage3.deduplicate_results([left, right])
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]["sources"]), {"openstreetmap", "kudago"})
        self.assertEqual(result[0]["confidence"], 0.9)
        self.assertEqual(len(result[0]["source_urls"]), 2)

    def test_ranking_prefers_official_and_matching_preferences(self):
        items = [
            {
                "title": "A",
                "source": "openstreetmap",
                "confidence": 0.8,
                "verification_status": "provider_reported",
                "coordinates": {"lat": 1, "lon": 1},
                "source_url": "x",
                "distance_m": 100,
                "is_free": None,
                "provider_payload": {},
            },
            {
                "title": "B",
                "source": "kudago",
                "confidence": 0.8,
                "verification_status": "provider_reported",
                "coordinates": {"lat": 1, "lon": 1},
                "source_url": "y",
                "distance_m": 300,
                "is_free": True,
                "provider_payload": {"for_children": True},
            },
        ]
        ranked = stage3.rank_results(items, free_only=True, children_only=True)
        self.assertEqual(ranked[0]["title"], "B")

    def test_discover_degrades_only_failed_section(self):
        result = stage3.discover(
            lat=55.7558,
            lon=37.6176,
            radius_m=2000,
            infrastructure_categories=["toilets"],
            event_start="2026-07-01T00:00:00Z",
            event_end="2026-07-02T00:00:00Z",
            children_only=True,
            urlopen=self.fake_urlopen,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["degraded_sections"], [])
        self.assertGreaterEqual(len(result["results"]), 2)
        self.assertTrue(all("rank_score" in item for item in result["results"]))

    def test_overpass_failure_does_not_break_kudago(self):
        def overpass_fails(req, timeout):
            parsed = urllib.parse.urlparse(req.full_url)
            if "overpass" in parsed.path or "overpass" in parsed.netloc:
                raise urllib.error.URLError("offline")
            return self.fake_urlopen(req, timeout)

        result = stage3.discover(
            lat=55.7558,
            lon=37.6176,
            radius_m=2000,
            event_start="2026-07-01T00:00:00Z",
            event_end="2026-07-02T00:00:00Z",
            urlopen=overpass_fails,
        )
        self.assertTrue(result["ok"])
        self.assertIn("overpass-nearby", result["degraded_sections"])
        self.assertTrue(any(item["source"] == "kudago" for item in result["results"]))


if __name__ == "__main__":
    unittest.main()
