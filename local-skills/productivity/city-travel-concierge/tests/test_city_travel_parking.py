import json, os, sys, tempfile, unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
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
    def candidate(fee=None, access=None, distance=100, lat=55.75, lon=37.61):
        return {"source":"openstreetmap","checked_at":"2026-07-01T00:00:00Z","title":"Parking","distance_m":distance,"coordinates":{"lat":lat,"lon":lon},"provider_payload":{"fee":fee,"access":access,"parking":"surface"}}

    @staticmethod
    def official_record(lat=55.75, lon=37.61):
        return {
            "source_name": parking.OFFICIAL_SOURCE_NAME,
            "source_url": parking.OFFICIAL_SOURCE_URL,
            "checked_at": "2026-07-01T00:00:00Z",
            "zone_number": "0302",
            "parking_name": "Парковка №0302",
            "tariffs": [{"TimeRange": "08:00-21:00", "HourPrice": 380}],
            "hours": "08:00-21:00",
            "coordinates": {"lat": lat, "lon": lon},
        }

    def fake_urlopen(self, req, timeout):
        parsed = urllib.parse.urlparse(req.full_url)
        if "api.crftr.net" in parsed.netloc:
            return FakeResponse(
                {
                    "_items": [
                        {
                            "ParkingName": "Парковка №0302",
                            "ParkingZoneNumber": "0302",
                            "Tariffs": [{"TimeRange": "08:00-21:00", "HourPrice": 380}],
                            "Longitude_WGS84": "37.6101",
                            "Latitude_WGS84": "55.7501",
                            "Address": "Москва",
                            "global_id": 1,
                        }
                    ]
                }
            )
        if "overpass" in parsed.path or "overpass" in parsed.netloc:
            return FakeResponse(
                {
                    "elements": [
                        {"type":"node","id":1,"lat":55.75,"lon":37.61,"tags":{"amenity":"parking","name":"Likely free","fee":"no"}},
                        {"type":"node","id":2,"lat":55.751,"lon":37.611,"tags":{"amenity":"parking","name":"Customers","fee":"no","access":"customers"}},
                    ]
                }
            )
        raise AssertionError(f"unexpected URL {req.full_url}")

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

    def test_official_paid_zone_overrides_osm_fee_no(self):
        item = parking.classify_parking_candidate(self.candidate(fee="no"), official_record=self.official_record())
        self.assertEqual(item["parking_status"], "official")
        self.assertFalse(item["is_free"])
        self.assertEqual(item["parking_evidence"]["official"]["zone_number"], "0302")

    def test_private_access_is_not_recommended_even_with_official_match(self):
        item = parking.classify_parking_candidate(self.candidate(fee="no", access="private"), official_record=self.official_record())
        self.assertEqual(item["parking_status"], "official")
        self.assertFalse(item["eligible_for_recommendation"])

    def test_ranking_prefers_evidence(self):
        items = [
            parking.classify_parking_candidate(self.candidate(distance=50)),
            parking.classify_parking_candidate(self.candidate(fee="no", distance=300)),
            parking.classify_parking_candidate(self.candidate(distance=500), official_free=True),
        ]
        ranked = parking.rank_parking_candidates(items)
        self.assertEqual([x["parking_status"] for x in ranked], ["official", "likely_free", "unverified"])

    def test_parking_nearby_excludes_restricted(self):
        result = parking.parking_nearby(55.75,37.61,radius_m=1000,limit=5,urlopen=self.fake_urlopen,endpoints=(("mock","https://overpass.test/api/interpreter"),))
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["parking_status"], "official")
        self.assertEqual(result["metadata"]["official_matches"], 1)

    def test_fresh_user_confirmation_and_expired_confirmation(self):
        path = Path(self.tmp.name) / "confirmations.json"
        checked = datetime(2026, 7, 1, tzinfo=timezone.utc)
        saved = parking.save_user_confirmation(
            55.75,
            37.61,
            signs_present=True,
            barrier_present=False,
            paid=False,
            parked_successfully=True,
            checked_at=checked,
            path=path,
        )
        fresh = parking.load_user_confirmations(path, now=checked + timedelta(days=1))
        expired = parking.load_user_confirmations(path, now=checked + timedelta(days=40))
        self.assertIn(saved["place_id"], fresh)
        self.assertNotIn(saved["place_id"], expired)

    def test_official_api_failure_keeps_osm_candidates(self):
        def official_fails(req, timeout):
            parsed = urllib.parse.urlparse(req.full_url)
            if "api.crftr.net" in parsed.netloc:
                raise urllib.error.URLError("offline")
            return self.fake_urlopen(req, timeout)

        result = parking.parking_nearby(55.75,37.61,radius_m=1000,limit=5,urlopen=official_fails,endpoints=(("mock","https://overpass.test/api/interpreter"),))
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["parking_status"], "likely_free")
        self.assertFalse(next(s for s in result["provider_status"] if s["provider"] == "data.mos.ru")["success"])

if __name__ == "__main__": unittest.main()
