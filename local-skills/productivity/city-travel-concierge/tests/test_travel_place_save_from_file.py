import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "travel_place_save_from_file.py"
SPEC = importlib.util.spec_from_file_location("travel_place_save_from_file", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class TravelPlaceSaveFromFileTests(unittest.TestCase):
    def test_load_args_reads_valid_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.json"
            path.write_text(json.dumps({
                "name": "FunZone",
                "location": "Moscow",
                "description": "Family venue",
                "source_url": "https://example.com/reel",
                "verified_source_url": "https://example.com/official",
            }), encoding="utf-8")
            args = MODULE.load_args(path)
            self.assertEqual(args.name, "FunZone")
            self.assertEqual(args.verified_source_url, ["https://example.com/official"])

    def test_load_args_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.json"
            path.write_text(json.dumps({"name": "x", "unexpected": True}), encoding="utf-8")
            with self.assertRaises(ValueError):
                MODULE.load_args(path)

    def test_load_args_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = Path(tmp) / "payload.json"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                MODULE.load_args(link)


if __name__ == "__main__":
    unittest.main()
