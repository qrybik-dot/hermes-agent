import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import city_travel_parking as parking

class FakeResponse:
    def __init__(self, payload): self.payload, self.status = payload, 200
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def read(self, _limit=-1): return json.dumps(self.payload).encode()

class ParkingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"HERMES_HOME": self.tmp.name}, clear=False)
        self.env.start(); self.addCleanup(self.env.stop)

    @staticmethod
    def candidate(fee=None, access=None, distance=100):
        return {"source":"openstreetmap","checked_at":"2026-07-01T00:00:00Z","title":"Parking","distance_m":distance,"provider_payload":{"fee":fee,"access":access,"parking":"surface"}}

    def test_fee_no_is_only_likely_free(self):
        item = parking.classify_parking_candidate(self.candidate(fee="no"))
        self.assertEqual(item["parking_status"], "likely_free")
        self.assertIsNone(item["is_free"])
        self.assertTrue(item["eligible_for_recommendation"])

    def test_restricted_access_is_excluded(self):
        for access in ("private", "customers", "permit"):
            item = parking.classify_parking_candidate(self.candidate(fee="no", access=access))
            self.assertEqual(item["parking_status"], "unverified")
            self.assertFalse(item["eligible_for_recommendation"])

    def test_stronger_confirmation_statuses(self):
        official = parking.classify_parking_candidate(self.candidate(), official_free=True)
        confirmed = parking.classify_parking_candidate(self.candidate(), user_confirmed_free=True)
        self.assertEqual((official["parking_status"], official["is_free"]), ("official", True))
        self.assertEqual((confirmed["parking_status"], confirmed["is_free"]), ("user_confirmed", True))

    def test_ranking_prefers_evidence(self):
        items = [
            parking.classify_parking_candidate(self.candidate(distance=50)),
            parking.classify_parking_candidate(self.candidate(fee="no", distance=300)),
            parking.classify_parking_candidate(self.candidate(distance=500), official_free=True),
        ]
        ranked = parking.rank_parking_candidates(items)
        self.assertEqual([x["parking_status"] for x in ranked], ["official", "likely_free", "unverified"])

    def test_parking_nearby_excludes_restricted(self):
        payload = {"elements":[
            {"type":"node","id":1,"lat":55.75,"lon":37.61,"tags":{"amenity":"parking","name":"Likely free","fee":"no"}},
            {"type":"node","id":2,"lat":55.751,"lon":37.611,"tags":{"amenity":"parking","name":"Customers","fee":"no","access":"customers"}},
        ]}
        result = parking.parking_nearby(55.75,37.61,radius_m=1000,limit=5,urlopen=lambda req,timeout: FakeResponse(payload),endpoints=(("mock","https://overpass.test/api/interpreter"),))
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["parking_status"], "likely_free")

if __name__ == "__main__": unittest.main()
