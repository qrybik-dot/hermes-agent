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
    def __init__(self, payload, status=200):
        self.payload = payload; self.status = status
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def read(self, _limit=-1): return json.dumps(self.payload).encode("utf-8")

class UnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"HERMES_HOME": self.tmp.name}, clear=False); self.env.start(); self.addCleanup(self.env.stop)
    def test_missing_dadata_key_degrades(self):
        os.environ.pop("DADATA_API_KEY", None)
        result = ctc.dadata_address("Москва")
        self.assertFalse(result["ok"])
        self.assertEqual(result["provider_status"][0]["error_code"], "missing_api_key")
    def test_missing_geoapify_key_degrades(self):
        os.environ.pop("GEOAPIFY_API_KEY", None)
        result = ctc.geoapify_geocode("Paris")
        self.assertFalse(result["ok"])
        self.assertEqual(result["provider_status"][0]["error_code"], "missing_api_key")

    def test_deep_links_are_encoded(self):
        link = ctc.yandex_place_link(55.1, 37.2, "Москва & кафе")
        self.assertIn("text=", link)
        self.assertNotIn("Москва & кафе", link)
        self.assertIn("%D0%9C", link)
        self.assertIn("%26", link)
    def test_cache_key_excludes_secret_shaped_values(self):
        key = ctc.stable_cache_key("geoapify", "geocode", {"text": "x", "apiKey": "SECRET"})
        self.assertNotIn("SECRET", key)
        self.assertEqual(len(key), 64)
    def test_normalized_dadata_result(self):
        os.environ["DADATA_API_KEY"] = "unit-test-key"
        payload = {"suggestions": [{"value": "Москва", "unrestricted_value": "г Москва", "data": {"geo_lat": "55.7558", "geo_lon": "37.6176", "fias_id": "abc", "region_with_type": "г Москва", "qc_geo": "0"}}]}
        result = ctc.dadata_address("Москва", urlopen=lambda req, timeout: FakeResponse(payload))
        self.assertTrue(result["ok"])
        item = result["results"][0]
        self.assertEqual(item["source"], "dadata")
        self.assertEqual(item["administrative"]["fias_id"], "abc")
        self.assertEqual(item["verification_status"], "verified")
        self.assertIn("yandex_maps", item["deep_links"])

    def test_street_without_house_is_approximate(self):
        os.environ["DADATA_API_KEY"] = "unit-test-key"
        payload = {
            "suggestions": [
                {
                    "value": "Московская обл, г Королёв, ул Лесная",
                    "unrestricted_value": "Московская обл, г Королёв, ул Лесная",
                    "data": {
                        "geo_lat": "55.920",
                        "geo_lon": "37.820",
                        "city_with_type": "г Королёв",
                        "street_with_type": "ул Лесная",
                        "house": None,
                        "fias_id": "street-fias",
                        "qc_geo": "1",
                    },
                }
            ]
        }
        result = ctc.dadata_address("Королёв, Лесная улица", urlopen=lambda req, timeout: FakeResponse(payload))
        item = result["results"][0]
        self.assertTrue(item["approximate_start"])
        self.assertIn("приблизительная", item["precision_warning"])
        self.assertEqual(item["ambiguity_key"]["street"], "ул Лесная")

    def test_street_with_house_is_not_approximate(self):
        os.environ["DADATA_API_KEY"] = "unit-test-key"
        payload = {
            "suggestions": [
                {
                    "value": "Московская обл, г Королёв, ул Лесная, д 5",
                    "unrestricted_value": "Московская обл, г Королёв, ул Лесная, д 5",
                    "data": {
                        "geo_lat": "55.921",
                        "geo_lon": "37.821",
                        "city_with_type": "г Королёв",
                        "street_with_type": "ул Лесная",
                        "house": "5",
                        "fias_id": "house-fias",
                        "qc_geo": "0",
                    },
                }
            ]
        }
        result = ctc.dadata_address("Королёв, Лесная 5", urlopen=lambda req, timeout: FakeResponse(payload))
        self.assertFalse(result["results"][0]["approximate_start"])

if __name__ == "__main__": unittest.main()
