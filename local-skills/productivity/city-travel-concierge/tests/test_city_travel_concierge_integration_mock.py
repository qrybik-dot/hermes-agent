import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import city_travel_concierge as ctc

class FakeResponse:
    def __init__(self, payload, status=200): self.payload = payload; self.status = status
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def read(self, _limit=-1): return json.dumps(self.payload).encode("utf-8")

class MockIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"HERMES_HOME": self.tmp.name, "DADATA_API_KEY": "fake-dadata", "GEOAPIFY_API_KEY": "fake-geoapify"}, clear=False)
        self.env.start(); self.addCleanup(self.env.stop)
    def fake_urlopen(self, req, timeout):
        url = req.full_url
        if "suggestions.dadata" in url:
            return FakeResponse({"suggestions": [{"value": "Красная площадь", "unrestricted_value": "г Москва, Красная пл", "data": {"geo_lat": "55.7539", "geo_lon": "37.6208", "city": "Москва", "fias_id": "fias-red", "qc_geo": "0"}}]})
        if "/v1/geocode/search" in url:
            return FakeResponse({"features": [{"type": "Feature", "properties": {"formatted": "Red Square, Moscow", "lat": 55.7539, "lon": 37.6208, "rank": {"confidence": 0.9}, "city": "Moscow", "country": "Russia"}}]})
        if "/v2/places" in url:
            return FakeResponse({"features": [{"type": "Feature", "properties": {"name": "Cafe Test", "formatted": "Cafe Test, Moscow", "lat": 55.754, "lon": 37.621, "categories": ["catering.cafe"], "distance": 120}}]})
        if "/v1/routing" in url:
            return FakeResponse({"features": [{"type": "Feature", "properties": {"distance": 600, "time": 480}}]})
        raise AssertionError("unexpected URL host/path")
    def test_city_degrades_and_merges_mock_providers(self):
        result = ctc.city("Красная площадь", poi_category="catering.cafe", urlopen=self.fake_urlopen)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(len(result["results"]), 3)
        self.assertEqual({s["provider"] for s in result["provider_status"]}, {"dadata", "geoapify"})
        self.assertTrue(all("apiKey" not in json.dumps(s) for s in result["provider_status"]))
    def test_route_mock(self):
        result = ctc.geoapify_route(55.7539, 37.6208, 55.7517, 37.6178, mode="walk", urlopen=self.fake_urlopen)
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["provider_payload"]["distance_m"], 600)
        self.assertIn("2gis", result["results"][0]["deep_links"])

if __name__ == "__main__": unittest.main()
