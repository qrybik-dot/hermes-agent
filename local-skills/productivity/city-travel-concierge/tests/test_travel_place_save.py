import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "travel_place_save.py"
SPEC = importlib.util.spec_from_file_location("travel_place_save", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

WRAPPER_SCRIPT = SCRIPT.with_name("travel_place_save_from_file.py")
WRAPPER_SPEC = importlib.util.spec_from_file_location("travel_place_save_from_file", WRAPPER_SCRIPT)
WRAPPER = importlib.util.module_from_spec(WRAPPER_SPEC)
assert WRAPPER_SPEC and WRAPPER_SPEC.loader
WRAPPER_SPEC.loader.exec_module(WRAPPER)


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

    def test_save_request_preserves_semantic_error(self):
        process = mock.Mock(
            stdout=json.dumps(
                {
                    "status": "needs_review",
                    "saved": False,
                    "message": "manual review required",
                }
            ) + "\n",
            stderr="",
            returncode=3,
        )
        with mock.patch.object(MODULE.subprocess, "run", return_value=process):
            result = MODULE.save_request({})
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["message"], "manual review required")

    def test_file_wrapper_loads_valid_json_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.json"
            path.write_text(
                json.dumps(
                    {
                        "name": "Example place",
                        "location": "Copenhagen, Denmark",
                        "description": "Family trip",
                        "source_url": "https://example.com/place",
                    }
                ),
                encoding="utf-8",
            )
            args = WRAPPER.load_args(path)
        self.assertEqual(args.name, "Example place")
        self.assertEqual(args.verified_source_url, [])

    def test_file_wrapper_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.json"
            path.write_text('{"name": "x", "shell": "unsafe"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported fields"):
                WRAPPER.load_args(path)


if __name__ == "__main__":
    unittest.main()
