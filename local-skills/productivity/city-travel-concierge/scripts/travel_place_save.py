#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

HELPER_PATH = '/opt/hermes-knowledge-mcp/travel_quick_save_cli.py'
MAX_FIELD = 4000


def clean(value: Any, limit: int = MAX_FIELD) -> str:
    return ' '.join(str(value or '').split())[:limit]


def values(args, name: str) -> list[Any]:
    value = getattr(args, name, None)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def http_url(value: Any, field: str, required: bool = False) -> str:
    text = clean(value, 2000)
    if not text and not required:
        return ''
    parsed = urlparse(text)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError(f'{field} must be an absolute http(s) URL')
    return text


def canonical_url(value: str) -> str:
    parsed = urlparse(value)
    return f'{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip("/")}'


def is_map_url(value: str) -> bool:
    host = urlparse(value).netloc.lower()
    path = urlparse(value).path.lower()
    return ('yandex.' in host and '/maps' in path) or ('google.' in host and '/maps' in path)


def entity_key(name: str, location: str, map_url: str, explicit: str = '') -> str:
    if clean(explicit, 300):
        return clean(explicit, 300)
    for url in [map_url]:
        match = re.search(r'/org/[^/]+/(\d+)', url)
        if match:
            return f'yandex:{match.group(1)}'
        parsed = urlparse(url)
        place_id = parse_qs(parsed.query).get('place_id') or parse_qs(parsed.query).get('query_place_id')
        if place_id:
            return f'google:{place_id[0]}'
    coords = re.search(r'(-?\d{1,3}\.\d+)\s*[,;]\s*(-?\d{1,3}\.\d+)', location)
    if coords:
        return f'geo:{float(coords.group(1)):.6f},{float(coords.group(2)):.6f}'
    material = f'{name.casefold()}|{location.casefold()}'
    return 'travel:' + hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]


def source_label(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if 'instagram.' in host:
        return 'Instagram Reel'
    if 'yandex.' in host and '/maps' in urlparse(url).path.lower():
        return 'Яндекс Карты'
    if 'google.' in host and '/maps' in urlparse(url).path.lower():
        return 'Google Maps'
    return 'Источник'


def build_request(args: argparse.Namespace) -> dict[str, Any]:
    name = clean(args.name, 240)
    location = clean(args.location, 700)
    description = clean(args.description)
    source_url = http_url(args.source_url, 'source URL', required=True)
    map_url = http_url(getattr(args, 'map_url', ''), 'map URL')
    if not name or not location or not description:
        raise ValueError('name, location and description are required')

    checked_at = clean(getattr(args, 'checked_at', ''), 20) or datetime.now(timezone.utc).date().isoformat()
    verified_urls = []
    for item in values(args, 'verified_source_url'):
        url = http_url(item, 'verified source URL')
        if url and url not in verified_urls:
            verified_urls.append(url)
    sources = []
    for url in [source_url, map_url, *verified_urls]:
        if url and url not in sources:
            sources.append(url)

    verified_fields = {str(x) for x in values(args, 'verified_fields') if str(x) in {'price', 'schedule', 'hours'}}
    independent_verified = [url for url in verified_urls if canonical_url(url) != canonical_url(source_url)]
    can_verify = bool(independent_verified) or is_map_url(source_url)
    provenance = {}
    facts = [f'Название: {name}', f'Локация: {location}', f'Описание: {description}']
    claims = []
    optional = {
        'price': clean(getattr(args, 'price', '')),
        'schedule': clean(getattr(args, 'schedule', '')),
        'hours': clean(getattr(args, 'hours', '')),
    }
    labels = {'price': 'Цена', 'schedule': 'Расписание', 'hours': 'Время работы'}
    for key, value in optional.items():
        if not value:
            continue
        verified = key in verified_fields and can_verify
        provenance[key] = {
            'status': 'verified' if verified else 'source_claim',
            'checked_at': checked_at,
            'source_urls': independent_verified if verified and independent_verified else [source_url],
        }
        (facts if verified else claims).append(f'{labels[key]}: {value}')

    category = clean(getattr(args, 'category', ''), 200)
    relations = ['Поездки и места']
    if category:
        relations.append(category[:1].upper() + category[1:])
    if 'Московская область' in location:
        relations.append('Московская область')
    elif re.search(r'(^|,|\s)Москва($|,|\s)', location):
        relations.append('Москва')
    for item in values(args, 'relations'):
        item = clean(item, 200)
        if item and item not in relations:
            relations.append(item)
    source_records = [{'url': source_url, 'label': source_label(source_url), 'role': 'source'}]
    for url in [map_url, *verified_urls]:
        if url and url != source_url:
            source_records.append({'url': url, 'label': source_label(url) if is_map_url(url) else 'Проверочный источник', 'role': 'verified'})

    summary_parts = [f'{name}. {location}', description]
    summary_parts.extend(f'{labels[key]}: {value}' for key, value in optional.items() if value)
    payload = {
        'schema_version': 2,
        'knowledge_project': 'travel',
        'type': 'research',
        'workflow_type': 'research',
        'entity_type': 'travel_place',
        'entity_key': entity_key(name, location, map_url, getattr(args, 'entity_key', '')),
        'title': name,
        'display_name': name,
        'name': name,
        'location': location,
        'description': description,
        'category': category,
        'map_url': map_url,
        'price': optional['price'],
        'schedule': optional['schedule'],
        'hours': optional['hours'],
        'infrastructure': [clean(x) for x in values(args, 'infrastructure') if clean(x)],
        'parking': values(args, 'parking'),
        'nearby_food': values(args, 'nearby_food'),
        'important': [clean(x) for x in values(args, 'important') if clean(x)],
        'relations': relations,
        'checked_at': checked_at,
        'verified_at': checked_at if any(v.get('status') == 'verified' for v in provenance.values()) else '',
        'field_provenance': provenance,
        'summary': '\n'.join(summary_parts)[:8000],
        'accepted_facts': facts,
        'source_claims': claims,
        'sources': sources,
        'source_records': source_records,
        'sensitivity': 'internal',
        'gpt_access': 'allowed',
        'source_system': 'hermes',
        'source_workspace': 'telegram',
    }
    return {
        'payload': payload,
        'idempotency_key': 'travel-place:' + hashlib.sha256(payload['entity_key'].encode('utf-8')).hexdigest(),
        'wait_seconds': 60,
    }


def save_request(request):
    proc = subprocess.run(
        ['sudo', '-n', '-u', 'hermes-promoter', '/usr/bin/python3', HELPER_PATH],
        input=json.dumps(request, ensure_ascii=False), text=True, capture_output=True,
        timeout=90, check=False,
    )
    lines = (proc.stdout or '').strip().splitlines()
    if not lines:
        raise RuntimeError('knowledge travel save returned no JSON')
    result = json.loads(lines[-1])
    if proc.returncode != 0:
        result.setdefault('status', 'error')
        result.setdefault('saved', False)
        result.setdefault('message', clean(proc.stderr or 'travel save failed', 1000))
    return result


def json_item(value):
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return parsed


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", required=True)
    p.add_argument("--location", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--category", default="")
    p.add_argument("--map-url", default="")
    p.add_argument("--entity-key", default="")
    p.add_argument("--price", default="")
    p.add_argument("--schedule", default="")
    p.add_argument("--hours", default="")
    p.add_argument("--source-url", required=True)
    p.add_argument("--verified-source-url", action="append", default=[])
    p.add_argument("--verified-field", dest="verified_fields", action="append", choices=("price", "schedule", "hours"), default=[])
    p.add_argument("--checked-at", default="")
    p.add_argument("--infrastructure", action="append", default=[])
    p.add_argument("--parking", type=json_item, action="append", default=[])
    p.add_argument("--nearby-food", type=json_item, action="append", default=[])
    p.add_argument("--important", action="append", default=[])
    p.add_argument("--relation", dest="relations", action="append", default=[])
    p.add_argument("--dry-run", action="store_true")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        request = build_request(args)
        result = request if args.dry_run else save_request(request)
    except (ValueError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "saved": False, "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.dry_run:
        return 0
    confirmed = bool(result.get("saved") or result.get("already_exists")) and int(result.get("readback_count") or 0) > 0
    return 0 if confirmed else 1


if __name__ == "__main__":
    sys.exit(main())
