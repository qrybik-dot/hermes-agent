from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

SCRIPT = Path('/home/hermes/hermes-v018-integration/scripts/run_agentic_pilot_benchmark.py')
spec = importlib.util.spec_from_file_location('agentic_bench', SCRIPT)
bench = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bench)


def _run_batch(start: int, count: int, *, reset: bool = False) -> None:
    if reset and bench.ROOT.exists():
        shutil.rmtree(bench.ROOT)
    data = bench.load_results()
    base, key = bench._resolve_local_proxy_key()
    selected = bench.CASES[start:start + count]
    existing = {(row['case'], row['model']) for row in data['runs']}
    for index, task in enumerate(selected, start=start):
        order = bench.MODELS if index % 2 == 0 else list(reversed(bench.MODELS))
        for model in order:
            if (task['id'], model) in existing:
                continue
            row = bench.run_one(base, key, model, task)
            data['runs'].append(row)
            data['summary'] = bench.summary(data)
            bench.save_results(data)
            print(json.dumps({k: row[k] for k in ('case','model','passed','seconds','api_calls','tool_calls','tool_errors','nudges')}, ensure_ascii=False), flush=True)
    data['summary'] = bench.summary(data)
    bench.save_results(data)
    print('SUMMARY ' + json.dumps(data['summary'], ensure_ascii=False), flush=True)


def test_batch_00_03():
    _run_batch(0, 4, reset=True)


def test_batch_04_07():
    _run_batch(4, 4)


def test_batch_08_11():
    _run_batch(8, 4)


def test_batch_12_15():
    _run_batch(12, 4)


def test_batch_16_19():
    _run_batch(16, 4)


def test_batch_20_23():
    _run_batch(20, 4)
