#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

SCRIPT = Path(__file__).with_name('travel_place_save.py')
SPEC = importlib.util.spec_from_file_location('travel_place_save', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

MAX_SIZE = 64 * 1024
ALLOWED_KEYS = {
    'name', 'location', 'description', 'category', 'map_url', 'entity_key',
    'price', 'schedule', 'hours', 'source_url', 'verified_source_url',
    'verified_fields', 'checked_at', 'infrastructure', 'parking',
    'nearby_food', 'important', 'relations',
}


def payload_path():
    home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
    return home / 'tmp' / 'travel-place-save.json'


def list_value(data, key):
    value = data.get(key) or []
    return value if isinstance(value, list) else [value]


def load_args(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('travel payload must be a regular non-symlink file')
    if path.stat().st_size > MAX_SIZE:
        raise ValueError('travel payload is too large')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('travel payload must be a JSON object')
    unknown = sorted(set(data) - ALLOWED_KEYS)
    if unknown:
        raise ValueError('unsupported fields: ' + ', '.join(unknown))
    for key in ('parking', 'nearby_food'):
        if not all(isinstance(item, (dict, str)) for item in list_value(data, key)):
            raise ValueError(f'{key} must contain objects or strings')
    return argparse.Namespace(
        name=data.get('name', ''), location=data.get('location', ''),
        description=data.get('description', ''), category=data.get('category', ''),
        map_url=data.get('map_url', ''), entity_key=data.get('entity_key', ''),
        price=data.get('price', ''), schedule=data.get('schedule', ''),
        hours=data.get('hours', ''), source_url=data.get('source_url', ''),
        verified_source_url=list_value(data, 'verified_source_url'),
        verified_fields=list_value(data, 'verified_fields'),
        checked_at=data.get('checked_at', ''),
        infrastructure=list_value(data, 'infrastructure'),
        parking=list_value(data, 'parking'), nearby_food=list_value(data, 'nearby_food'),
        important=list_value(data, 'important'), relations=list_value(data, 'relations'),
        dry_run=False,
    )


def main():
    try:
        result = MODULE.save_request(MODULE.build_request(load_args(payload_path())))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        result = {'status': 'error', 'saved': False, 'message': str(exc)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    confirmed = bool(result.get('saved') or result.get('already_exists')) and int(result.get('readback_count') or 0) > 0
    return 0 if confirmed else 1


if __name__ == '__main__':
    sys.exit(main())
