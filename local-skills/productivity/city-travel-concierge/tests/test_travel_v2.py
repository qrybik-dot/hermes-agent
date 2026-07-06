import argparse
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAVE_PATH = ROOT / 'scripts' / 'travel_place_save.py'
SPEC = importlib.util.spec_from_file_location('travel_place_save_v2_test', SAVE_PATH)
SAVE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(SAVE)


def args(**changes):
    data = dict(name='Example Park', location='Московская область, 55.100000, 37.200000', description='Семейный парк у воды', category='Парк', map_url='https://yandex.ru/maps/org/example/123456789', entity_key='', price='500 ₽', schedule='Ежедневно', hours='10:00–20:00', source_url='https://www.instagram.com/reel/one/', verified_source_url=['https://example.org/official'], verified_fields=['price', 'schedule', 'hours'], checked_at='2026-07-05', infrastructure=['туалет', 'детская площадка'], parking=[{'label': 'Парковка у входа', 'url': 'https://yandex.ru/maps/?pt=37.2,55.1'}], nearby_food=[{'label': 'Кафе', 'url': 'https://yandex.ru/maps/org/cafe/1'}], important=['Проверить погоду'], relations=['Отдых с детьми'], dry_run=True)
    data.update(changes)
    return argparse.Namespace(**data)


class TravelPayloadV2Tests(unittest.TestCase):
    def test_entity_key_uses_map_identity_not_reel_url(self):
        first = SAVE.build_request(args(source_url='https://www.instagram.com/reel/one/'))
        second = SAVE.build_request(args(source_url='https://www.instagram.com/reel/two/'))
        self.assertEqual(first['payload']['entity_key'], 'yandex:123456789')
        self.assertEqual(first['idempotency_key'], second['idempotency_key'])

    def test_unverified_value_is_kept_as_source_claim(self):
        request = SAVE.build_request(args(verified_source_url=['https://www.instagram.com/reel/one/'], verified_fields=['price']))
        payload = request['payload']
        self.assertEqual(payload['price'], '500 ₽')
        self.assertEqual(payload['field_provenance']['price']['status'], 'source_claim')
        self.assertNotIn('Цена: 500 ₽', payload['accepted_facts'])
        self.assertIn('Цена: 500 ₽', payload['source_claims'])

    def test_payload_contains_human_and_machine_fields(self):
        payload = SAVE.build_request(args())['payload']
        self.assertEqual(payload['schema_version'], 2)
        self.assertEqual(payload['entity_type'], 'travel_place')
        self.assertIn('Поездки и места', payload['relations'])
        self.assertIn('Московская область', payload['relations'])
        self.assertTrue(payload['parking'])
        self.assertTrue(payload['nearby_food'])
