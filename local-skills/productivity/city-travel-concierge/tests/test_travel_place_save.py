import argparse
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "travel_place_save.py"
SPEC = importlib.util.spec_from_file_location("travel_place_save", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class TravelPlaceSaveTests(unittest.TestCase):
    def _args(self, **updates):
        values = {
            "name": "Example place",
            "location": "Copenhagen, Denmark",
            "description": "A useful place for a future family trip",
            "price": "100 DKK",
            "schedule": "Seasonal",
            "hours": "10:00-18:00",
            "source_url": "https://www.instagram.com/reel/example/",
            "verified_source_url": ["https://example.com/official"],
            "dry_run": True,
        }
        values.update(updates)
        return argparse.Namespace(**values)

    def test_build_request_targets_travel_and_keeps_sources(self):
        request = MODULE.build_request(self._args())
        payload = request["payload"]
        self.assertEqual(payload["knowledge_project"], "travel")
        self.assertEqual(payload["type"], "research")
        self.assertEqual(
            payload["sources"],
            [
                "https://www.instagram.com/reel/example/",
                "https://example.com/official",
            ],
        )
        self.assertIn("Цена: 100 DKK", payload["accepted_facts"])
        self.assertIn("Время работы: 10:00-18:00", payload["accepted_facts"])
        self.assertTrue(request["idempotency_key"].startswith("travel-place:"))

    def test_idempotency_is_stable(self):
        first = MODULE.build_request(self._args())
        second = MODULE.build_request(self._args())
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])

    def test_invalid_source_url_is_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.build_request(self._args(source_url="instagram reel example"))

    def test_required_fields_are_rejected_when_blank(self):
        with self.assertRaises(ValueError):
            MODULE.build_request(self._args(location="   "))


if __name__ == "__main__":
    unittest.main()
