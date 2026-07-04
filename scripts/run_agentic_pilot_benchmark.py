#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path('/home/hermes/hermes-v018-integration')
ROOT = Path('/tmp/agentic-pilot-20260704')
RESULTS = ROOT / 'results.json'
sys.path.insert(0, str(REPO))
from scripts.update_radar import _resolve_local_proxy_key

MODELS = ['gemini-3-flash-agent', 'gemini-3.5-flash-low']


def case(case_id: str, files: dict[str, str], instruction: str, expected: Any) -> dict[str, Any]:
    return {'id': case_id, 'files': files, 'instruction': instruction, 'expected': expected}


CASES = [
    case('compare_configs', {
        'old.json': '{"timeout":60,"retries":2,"model":"gemini-3.5-flash-low","streaming":false}',
        'new.json': '{"timeout":45,"retries":2,"model":"gemini-3-flash-agent","streaming":false}',
    }, 'Read old.json and new.json. Write output.json with schema {"changed":{key:{"old":value,"new":value}}}. Include only changed keys, sorted alphabetically.',
       {'changed': {'model': {'old': 'gemini-3.5-flash-low', 'new': 'gemini-3-flash-agent'}, 'timeout': {'old': 60, 'new': 45}}}),
    case('merge_contacts', {
        'contacts_a.json': '[{"email":"anna@example.com","name":"Анна","phone":"111","updated":"2026-06-01"},{"email":"boris@example.com","name":"Борис","phone":"222","updated":"2026-06-03"}]',
        'contacts_b.json': '[{"email":"anna@example.com","name":"Анна","phone":"999","updated":"2026-07-01"},{"email":"clara@example.com","name":"Клара","phone":"333","updated":"2026-06-20"}]',
    }, 'Merge contacts_a.json and contacts_b.json by email. For duplicates keep the record with the latest updated date. Write output.json as {"contacts":[...]}, sorted by email.',
       {'contacts': [
           {'email': 'anna@example.com', 'name': 'Анна', 'phone': '999', 'updated': '2026-07-01'},
           {'email': 'boris@example.com', 'name': 'Борис', 'phone': '222', 'updated': '2026-06-03'},
           {'email': 'clara@example.com', 'name': 'Клара', 'phone': '333', 'updated': '2026-06-20'},
       ]}),
    case('overdue_tasks', {
        'tasks.csv': 'id,title,due,status,owner\n1,Подготовить отчет,2026-07-02,open,Антон\n2,Проверить календарь,2026-07-05,open,Вера\n3,Обновить роутер,2026-07-01,done,Антон\n4,Позвонить врачу,2026-07-03,open,Вера\n',
    }, 'Today is 2026-07-04. Read tasks.csv. Write output.json with {"overdue":[{"id":number,"title":string,"owner":string,"days_overdue":number}]}. Include only open overdue tasks, sorted by id.',
       {'overdue': [
           {'id': 1, 'title': 'Подготовить отчет', 'owner': 'Антон', 'days_overdue': 2},
           {'id': 4, 'title': 'Позвонить врачу', 'owner': 'Вера', 'days_overdue': 1},
       ]}),
    case('log_error_counts', {
        'app.log': '2026-07-04T10:00:01 INFO start\n2026-07-04T10:00:02 ERROR timeout provider=custom\n2026-07-04T10:00:03 WARN retry\n2026-07-04T10:00:04 ERROR auth provider=custom\n2026-07-04T10:00:05 ERROR timeout provider=custom\n2026-07-04T10:00:06 INFO recovered\n',
    }, 'Read app.log. Count ERROR records by error type, where the error type is the word immediately after ERROR. Write output.json as {"total_errors":number,"by_type":{type:number},"last_error":type}. Sort by_type keys alphabetically.',
       {'total_errors': 3, 'by_type': {'auth': 1, 'timeout': 2}, 'last_error': 'timeout'}),
    case('version_mismatch', {
        'service_a.json': '{"name":"gateway","python":"3.11.15","openai_sdk":"2.24.0","schema":29}',
        'service_b.json': '{"name":"worker","python":"3.11.15","openai_sdk":"2.23.1","schema":28}',
        'required.json': '{"python":"3.11.15","openai_sdk":"2.24.0","schema":29}',
    }, 'Compare service_a.json and service_b.json with required.json. Write output.json as {"compliant":[service names],"non_compliant":{service:{field:{"actual":value,"required":value}}}}. Sort service names and field names.',
       {'compliant': ['gateway'], 'non_compliant': {'worker': {'openai_sdk': {'actual': '2.23.1', 'required': '2.24.0'}, 'schema': {'actual': 28, 'required': 29}}}}),
    case('missing_fields', {
        'places.json': '[{"id":1,"name":"Парк","location":"Москва","hours":"10:00-20:00"},{"id":2,"name":"Музей","location":"","hours":""},{"id":3,"name":"Кафе","hours":"09:00-22:00"}]',
        'required.txt': 'name\nlocation\nhours\n',
    }, 'Read places.json and required.txt. Empty strings and absent keys count as missing. Write output.json as {"invalid":[{"id":number,"missing":[field names]}]}, sorted by id and missing field name.',
       {'invalid': [{'id': 2, 'missing': ['hours', 'location']}, {'id': 3, 'missing': ['location']}]}),
    case('budget_summary', {
        'expenses.csv': 'date,category,amount\n2026-07-01,Продукты,1200\n2026-07-01,Транспорт,350\n2026-07-02,Продукты,800\n2026-07-03,Дети,1500\n2026-07-03,Транспорт,150\n',
    }, 'Read expenses.csv. Write output.json as {"total":number,"by_category":{category:number},"largest_category":string}. Sort category keys alphabetically. If tied, choose alphabetically first.',
       {'total': 4000, 'by_category': {'Дети': 1500, 'Продукты': 2000, 'Транспорт': 500}, 'largest_category': 'Продукты'}),
    case('candidate_shortlist', {
        'candidates.json': '[{"name":"Алина","score":84,"risk":"low"},{"name":"Борис","score":91,"risk":"high"},{"name":"Виктор","score":88,"risk":"low"},{"name":"Галина","score":84,"risk":"low"}]',
    }, 'Select candidates with score >=84 and risk=low. Sort by score descending, then name ascending. Write output.json as {"shortlist":[{"rank":number,"name":string,"score":number}]}.',
       {'shortlist': [{'rank': 1, 'name': 'Виктор', 'score': 88}, {'rank': 2, 'name': 'Алина', 'score': 84}, {'rank': 3, 'name': 'Галина', 'score': 84}]}),
    case('action_items', {
        'meeting_1.txt': 'Решили: Антон проверит роутинг до 6 июля. Вера подготовит список вопросов до 7 июля. Идея про новый дизайн отложена.',
        'meeting_2.txt': 'На следующем созвоне: Антон пришлет результаты тестов до 8 июля. Дизайн пока не трогаем.',
    }, 'Read both meeting files. Extract only explicit assigned actions with owner and deadline. Write output.json as {"actions":[{"owner":string,"action":string,"due":"2026-07-DD"}]}, sorted by due then owner. Do not include postponed ideas.',
       {'actions': [
           {'owner': 'Антон', 'action': 'проверит роутинг', 'due': '2026-07-06'},
           {'owner': 'Вера', 'action': 'подготовит список вопросов', 'due': '2026-07-07'},
           {'owner': 'Антон', 'action': 'пришлет результаты тестов', 'due': '2026-07-08'},
       ]}),
    case('duplicate_places', {
        'places_a.json': '[{"name":"Лес Приключений","lat":55.7181,"lon":37.3812},{"name":"Музей техники","lat":55.8200,"lon":37.6400}]',
        'places_b.json': '[{"name":"лес приключений","lat":55.7181,"lon":37.3812},{"name":"Парк Останкино","lat":55.8290,"lon":37.6120}]',
    }, 'Find exact duplicates across places_a.json and places_b.json using case-insensitive name plus identical lat and lon. Write output.json as {"duplicates":[{"name":canonical name from places_a,"sources":[file names]}]}, sorted by name.',
       {'duplicates': [{'name': 'Лес Приключений', 'sources': ['places_a.json', 'places_b.json']}]}),
    case('inventory_reconcile', {
        'expected.csv': 'sku,qty\nA1,5\nB2,3\nC3,8\n',
        'actual.csv': 'sku,qty\nA1,5\nB2,1\nC3,10\nD4,2\n',
    }, 'Compare expected.csv and actual.csv. Write output.json as {"differences":[{"sku":string,"expected":number,"actual":number,"delta":number}]}. Include unexpected SKUs with expected=0. Sort by sku.',
       {'differences': [
           {'sku': 'B2', 'expected': 3, 'actual': 1, 'delta': -2},
           {'sku': 'C3', 'expected': 8, 'actual': 10, 'delta': 2},
           {'sku': 'D4', 'expected': 0, 'actual': 2, 'delta': 2},
       ]}),
    case('schedule_conflicts', {
        'events.json': '[{"id":"A","start":"2026-07-05T09:00","end":"2026-07-05T10:00"},{"id":"B","start":"2026-07-05T09:30","end":"2026-07-05T11:00"},{"id":"C","start":"2026-07-05T11:00","end":"2026-07-05T12:00"},{"id":"D","start":"2026-07-05T11:30","end":"2026-07-05T12:30"}]',
    }, 'Find overlapping event pairs. End time equal to another start time is not a conflict. Write output.json as {"conflicts":[[id1,id2]]}, with IDs inside each pair ascending and pairs sorted.',
       {'conflicts': [['A', 'B'], ['C', 'D']]}),
    case('policy_diff', {
        'policy_old.json': '{"max_iterations":24,"auto_approve":false,"reviewer":true,"fallbacks":2}',
        'policy_new.json': '{"max_iterations":10,"auto_approve":false,"reviewer":true,"fallbacks":1}',
    }, 'Compare policy files. Write output.json as {"changes":[{"field":string,"old":value,"new":value,"direction":"increase"|"decrease"|"changed"}]}, sorted by field.',
       {'changes': [
           {'field': 'fallbacks', 'old': 2, 'new': 1, 'direction': 'decrease'},
           {'field': 'max_iterations', 'old': 24, 'new': 10, 'direction': 'decrease'},
       ]}),
    case('source_freshness', {
        'sources.json': '[{"title":"A","published":"2026-07-04"},{"title":"B","published":"2026-05-01"},{"title":"C","published":"2026-06-20"},{"title":"D","published":"unknown"}]',
    }, 'Today is 2026-07-04. A source is fresh if published within the last 30 days inclusive. Unknown dates are unverified. Write output.json as {"fresh":[titles],"stale":[titles],"unverified":[titles]}, each sorted alphabetically.',
       {'fresh': ['A', 'C'], 'stale': ['B'], 'unverified': ['D']}),
    case('owner_load', {
        'work.json': '[{"owner":"Антон","hours":3,"status":"open"},{"owner":"Вера","hours":2,"status":"open"},{"owner":"Антон","hours":4,"status":"done"},{"owner":"Вера","hours":5,"status":"open"},{"owner":"Игорь","hours":1,"status":"open"}]',
    }, 'Sum open hours by owner. Write output.json as {"by_owner":{owner:number},"most_loaded":string}. Sort owner keys alphabetically. If tied, choose alphabetically first.',
       {'by_owner': {'Антон': 3, 'Вера': 7, 'Игорь': 1}, 'most_loaded': 'Вера'}),
    case('transcript_facts', {
        'part1.txt': 'Релиз запланирован на 9 июля. Основной риск — общая квота Antigravity.',
        'part2.txt': 'Решили оставить GPT-5.5 для coding и server debug. Flash Agent тестируем только на обратимых операциях.',
        'part3.txt': 'Критерий успеха пилота: не менее 90% полностью завершенных задач.',
    }, 'Read all three transcript parts. Write output.json as {"release_date":"YYYY-MM-DD","risk":string,"coding_model":string,"pilot_scope":string,"success_threshold_percent":number}. Use concise values grounded only in files.',
       {'release_date': '2026-07-09', 'risk': 'общая квота Antigravity', 'coding_model': 'GPT-5.5', 'pilot_scope': 'обратимые операции', 'success_threshold_percent': 90}),
    case('dependency_order', {
        'steps.json': '[{"id":"deploy","depends_on":["tests","backup"]},{"id":"tests","depends_on":["code"]},{"id":"backup","depends_on":[]},{"id":"code","depends_on":[]}]',
    }, 'Create one valid execution order satisfying all dependencies. When multiple steps are available, choose alphabetically first. Write output.json as {"order":[ids]}.',
       {'order': ['backup', 'code', 'tests', 'deploy']}),
    case('discover_marker', {
        'docs/a.txt': 'обычный документ',
        'docs/b.txt': 'ещё один документ',
        'archive/c.txt': 'TARGET_MARKER=hermes-safe-pilot\nowner=Anton\n',
        'archive/d.txt': 'старый архив',
    }, 'Use list_files to discover files, then read files until you find TARGET_MARKER. Write output.json as {"file":relative path,"marker":string,"owner":string}.',
       {'file': 'archive/c.txt', 'marker': 'hermes-safe-pilot', 'owner': 'Anton'}),
    case('benchmark_compare', {
        'baseline.json': '{"model":"flash-low","success":18,"total":20,"avg_seconds":12.4,"errors":2}',
        'candidate.json': '{"model":"flash-agent","success":19,"total":20,"avg_seconds":8.1,"errors":1}',
    }, 'Compare baseline.json and candidate.json. Write output.json as {"winner":model,"success_rate_delta_pp":number,"speedup_percent":number,"error_delta":number}. Round speedup_percent to one decimal. success_rate_delta_pp is candidate minus baseline.',
       {'winner': 'flash-agent', 'success_rate_delta_pp': 5, 'speedup_percent': 34.7, 'error_delta': -1}),
    case('alert_filter', {
        'events.json': '[{"id":1,"severity":"info","new":true},{"id":2,"severity":"critical","new":true},{"id":3,"severity":"warning","new":false},{"id":4,"severity":"warning","new":true},{"id":5,"severity":"critical","new":false}]',
    }, 'Notify only new warning or critical events. Write output.json as {"notify_ids":[numbers],"silent_ids":[numbers]}, both sorted ascending.',
       {'notify_ids': [2, 4], 'silent_ids': [1, 3, 5]}),
    case('normalize_addresses', {
        'addresses.txt': 'Королёв, ул. Лесная, д. 10\nкоролев улица Лесная 10\nМосква, пр-т Мира, 119\n',
    }, 'Normalize obvious textual variants and group duplicates. Write output.json as {"groups":[{"canonical":string,"count":number}]}, sorted by canonical. Use canonical forms exactly: "Королёв, улица Лесная, 10" and "Москва, проспект Мира, 119".',
       {'groups': [{'canonical': 'Королёв, улица Лесная, 10', 'count': 2}, {'canonical': 'Москва, проспект Мира, 119', 'count': 1}]}),
    case('release_notes', {
        'commits.txt': 'fix: prevent duplicate travel saves\nfeat: add agentic routing flag\ndocs: update human report format\nrefactor: rename helper\nfix: stop fallback after tool call\n',
    }, 'Read commits.txt. Write output.json as {"features":[messages without prefix],"fixes":[messages without prefix],"other_count":number}. Sort features and fixes alphabetically. docs and refactor count as other.',
       {'features': ['add agentic routing flag'], 'fixes': ['prevent duplicate travel saves', 'stop fallback after tool call'], 'other_count': 2}),
    case('feature_flags', {
        'defaults.json': '{"agentic":false,"long_context_split":false,"sonnet_ab":false}',
        'production.json': '{"agentic":true,"long_context_split":false,"sonnet_ab":false}',
        'allowed.json': '{"can_enable":["agentic"],"must_remain_disabled":["long_context_split","sonnet_ab"]}',
    }, 'Validate production.json against defaults.json and allowed.json. Write output.json as {"valid":boolean,"enabled":[flags],"violations":[strings]}. Sort lists.',
       {'valid': True, 'enabled': ['agentic'], 'violations': []}),
    case('handoff_checkpoint', {
        'changed_files.txt': 'gateway/task_router.py\ntests/gateway/test_model_routing_experiments.py\n',
        'commands.txt': 'pytest tests/gateway/test_model_routing_experiments.py\ngit diff --check\n',
        'results.txt': '42 passed\ndiff check passed\n',
        'remaining.txt': 'collect 20-30 real pilot tasks\nkeep long_context_split disabled\n',
    }, 'Build a concise handoff checkpoint from all files. Write output.json as {"changed_files":[strings],"commands":[strings],"verified":[strings],"remaining":[strings]}, preserving file order and removing blank lines.',
       {'changed_files': ['gateway/task_router.py', 'tests/gateway/test_model_routing_experiments.py'], 'commands': ['pytest tests/gateway/test_model_routing_experiments.py', 'git diff --check'], 'verified': ['42 passed', 'diff check passed'], 'remaining': ['collect 20-30 real pilot tasks', 'keep long_context_split disabled']}),
    case('stale_documents', {
        'documents.json': '[{"name":"runbook","updated":"2026-07-01","active":true},{"name":"legacy-routing","updated":"2025-12-01","active":false},{"name":"travel-skill","updated":"2026-06-15","active":true},{"name":"old-api","updated":"2026-01-01","active":true}]',
    }, 'Today is 2026-07-04. Active documents older than 120 days are stale. Inactive documents are archived, not stale. Write output.json as {"stale":[names],"archived":[names],"current":[names]}, each sorted.',
       {'stale': ['old-api'], 'archived': ['legacy-routing'], 'current': ['runbook', 'travel-skill']}),
]

TOOLS = [
    {'type': 'function', 'function': {'name': 'list_files', 'description': 'List files recursively within the workspace.', 'parameters': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': 'Relative directory, default .'}}, 'required': []}}},
    {'type': 'function', 'function': {'name': 'read_file', 'description': 'Read a UTF-8 text file within the workspace.', 'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}},
    {'type': 'function', 'function': {'name': 'write_file', 'description': 'Write UTF-8 text to output.json. No other file is writable.', 'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}}, 'required': ['path', 'content']}}},
]

SYSTEM = '''You are a bounded file-workflow agent. Use the provided tools to inspect the workspace and complete the task. You must read the relevant source files before deciding. You may write only output.json. Never modify source files. Do not use outside knowledge when the files contain the answer. Write valid JSON with exactly the requested schema, then reply only DONE. If a tool returns an error, correct the call instead of inventing a result.'''


def safe_path(workspace: Path, raw: str) -> Path:
    raw = str(raw or '').strip().replace('\\', '/')
    if raw.startswith('/'):
        raise ValueError('absolute paths are forbidden')
    target = (workspace / raw).resolve()
    root = workspace.resolve()
    if target != root and root not in target.parents:
        raise ValueError('path escapes workspace')
    return target


def hash_sources(workspace: Path) -> dict[str, str]:
    result = {}
    for path in sorted(workspace.rglob('*')):
        if path.is_file() and path.name != 'output.json':
            result[str(path.relative_to(workspace))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def execute_tool(workspace: Path, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    try:
        if name == 'list_files':
            base = safe_path(workspace, args.get('path') or '.')
            if not base.exists() or not base.is_dir():
                raise ValueError('directory not found')
            files = [str(p.relative_to(workspace)) for p in sorted(base.rglob('*')) if p.is_file()]
            return json.dumps({'files': files}, ensure_ascii=False), True
        if name == 'read_file':
            path = safe_path(workspace, args.get('path'))
            if not path.exists() or not path.is_file():
                raise ValueError('file not found')
            if path.stat().st_size > 200_000:
                raise ValueError('file too large')
            return json.dumps({'path': str(path.relative_to(workspace)), 'content': path.read_text(encoding='utf-8')}, ensure_ascii=False), True
        if name == 'write_file':
            path = safe_path(workspace, args.get('path'))
            if path.name != 'output.json' or path.parent != workspace.resolve():
                raise ValueError('only output.json in workspace root is writable')
            content = args.get('content')
            if not isinstance(content, str):
                raise ValueError('content must be string')
            path.write_text(content, encoding='utf-8')
            return json.dumps({'written': 'output.json', 'bytes': len(content.encode('utf-8'))}), True
        raise ValueError('unknown tool')
    except Exception as exc:
        return json.dumps({'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False), False


def call_model(base: str, key: str, model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {'model': model, 'messages': messages, 'tools': TOOLS, 'tool_choice': 'auto', 'stream': False, 'temperature': 0}
    req = urllib.request.Request(base.rstrip('/') + '/chat/completions', data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.load(response)


def normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def run_one(base: str, key: str, model: str, spec: dict[str, Any]) -> dict[str, Any]:
    workspace = ROOT / 'workspaces' / model / spec['id']
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    for rel, content in spec['files'].items():
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    before = hash_sources(workspace)
    messages: list[dict[str, Any]] = [
        {'role': 'system', 'content': SYSTEM},
        {'role': 'user', 'content': spec['instruction']},
    ]
    started = time.monotonic()
    api_calls = 0
    tool_calls = 0
    reads = 0
    writes = 0
    lists = 0
    tool_errors = 0
    nudges = 0
    final_text = ''
    error = ''
    call_trace: list[str] = []
    try:
        for _turn in range(8):
            api_calls += 1
            data = call_model(base, key, model, messages)
            msg = ((data.get('choices') or [{}])[0].get('message') or {})
            content = msg.get('content') or ''
            calls = msg.get('tool_calls') or []
            if calls:
                assistant_msg = {'role': 'assistant', 'content': content, 'tool_calls': calls}
                messages.append(assistant_msg)
                for call in calls:
                    tool_calls += 1
                    fn = call.get('function') or {}
                    name = str(fn.get('name') or '')
                    call_trace.append(name)
                    try:
                        args = json.loads(fn.get('arguments') or '{}')
                    except Exception as exc:
                        args = {}
                        result = json.dumps({'error': f'invalid arguments: {exc}'})
                        ok = False
                    else:
                        result, ok = execute_tool(workspace, name, args)
                    if name == 'read_file': reads += 1
                    elif name == 'write_file': writes += 1
                    elif name == 'list_files': lists += 1
                    if not ok: tool_errors += 1
                    messages.append({'role': 'tool', 'tool_call_id': call.get('id') or f'call-{tool_calls}', 'name': name, 'content': result})
                continue
            final_text = str(content).strip()
            if not (workspace / 'output.json').exists() and nudges == 0:
                nudges = 1
                messages.append({'role': 'assistant', 'content': content})
                messages.append({'role': 'user', 'content': 'output.json is missing. Continue with the tools and create it now.'})
                continue
            break
    except (urllib.error.URLError, TimeoutError, Exception) as exc:
        error = f'{type(exc).__name__}: {exc}'
    elapsed = time.monotonic() - started
    after = hash_sources(workspace)
    source_mutated = before != after
    output_exists = (workspace / 'output.json').exists()
    parsed = None
    parse_error = ''
    exact = False
    if output_exists:
        try:
            parsed = json.loads((workspace / 'output.json').read_text(encoding='utf-8'))
            exact = normalize(parsed) == normalize(spec['expected'])
        except Exception as exc:
            parse_error = f'{type(exc).__name__}: {exc}'
    passed = bool(output_exists and exact and not source_mutated and tool_errors == 0 and not error)
    return {
        'case': spec['id'], 'model': model, 'passed': passed, 'exact': exact,
        'output_exists': output_exists, 'source_mutated': source_mutated,
        'api_calls': api_calls, 'tool_calls': tool_calls, 'reads': reads, 'writes': writes,
        'lists': lists, 'tool_errors': tool_errors, 'nudges': nudges,
        'seconds': round(elapsed, 2), 'final_text': final_text[-500:],
        'parse_error': parse_error, 'error': error, 'trace': call_trace,
        'parsed': parsed,
    }


def load_results() -> dict[str, Any]:
    if RESULTS.exists():
        return json.loads(RESULTS.read_text(encoding='utf-8'))
    return {'created_at': time.time(), 'models': MODELS, 'cases': [c['id'] for c in CASES], 'runs': []}


def save_results(data: dict[str, Any]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def summary(data: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for model in MODELS:
        rows = [r for r in data['runs'] if r['model'] == model]
        if not rows:
            continue
        result[model] = {
            'passed': sum(r['passed'] for r in rows), 'total': len(rows),
            'pass_rate': round(100 * sum(r['passed'] for r in rows) / len(rows), 1),
            'avg_seconds': round(sum(r['seconds'] for r in rows) / len(rows), 2),
            'median_seconds': sorted(r['seconds'] for r in rows)[len(rows)//2],
            'avg_api_calls': round(sum(r['api_calls'] for r in rows) / len(rows), 2),
            'avg_tool_calls': round(sum(r['tool_calls'] for r in rows) / len(rows), 2),
            'tool_errors': sum(r['tool_errors'] for r in rows),
            'nudges': sum(r['nudges'] for r in rows),
            'source_mutations': sum(r['source_mutated'] for r in rows),
            'failed_cases': [r['case'] for r in rows if not r['passed']],
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--count', type=int, default=len(CASES))
    parser.add_argument('--reset', action='store_true')
    args = parser.parse_args()
    if args.reset and ROOT.exists():
        shutil.rmtree(ROOT)
    data = load_results()
    base, key = _resolve_local_proxy_key()
    selected = CASES[args.start:args.start + args.count]
    existing = {(r['case'], r['model']) for r in data['runs']}
    for index, spec in enumerate(selected, start=args.start):
        order = MODELS if index % 2 == 0 else list(reversed(MODELS))
        for model in order:
            if (spec['id'], model) in existing:
                continue
            row = run_one(base, key, model, spec)
            data['runs'].append(row)
            data['summary'] = summary(data)
            save_results(data)
            print(json.dumps({k: row[k] for k in ('case','model','passed','seconds','api_calls','tool_calls','tool_errors','nudges')}, ensure_ascii=False), flush=True)
    data['summary'] = summary(data)
    data['updated_at'] = time.time()
    save_results(data)
    print('SUMMARY ' + json.dumps(data['summary'], ensure_ascii=False), flush=True)
    print(str(RESULTS), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
