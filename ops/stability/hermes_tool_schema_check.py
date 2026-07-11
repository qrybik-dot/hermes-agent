#!/usr/bin/env python3
from __future__ import annotations
import json, subprocess, sys, tempfile, textwrap
from pathlib import Path
PYTHON = '/home/hermes/hermes-runtime/shared/venv/bin/python'
REPO = '/home/hermes/hermes-runtime/current'
CODE = r'''
import json
from toolsets import resolve_toolset
import model_tools
from tools.registry import registry
fail=[]
checked=0
for name in sorted(resolve_toolset('hermes-cli')):
    schema=registry.get_schema(name)
    if not isinstance(schema, dict):
        fail.append((name,'missing schema'))
        continue
    checked += 1
    for key in ('name','description','parameters'):
        if key not in schema or not schema.get(key):
            fail.append((name,'missing '+key))
    params=schema.get('parameters')
    if not isinstance(params, dict) or params.get('type') != 'object':
        fail.append((name,'parameters not object schema'))
print(json.dumps({'checked':checked,'failures':fail}, ensure_ascii=False))
'''
def main():
    p=subprocess.run([PYTHON,'-c',CODE], cwd=REPO, text=True, capture_output=True, timeout=60)
    if p.returncode:
        print('Schema check failed to run')
        print((p.stdout+p.stderr).strip())
        return 2
    data=json.loads(p.stdout)
    if data['failures']:
        print('🔴 core tool schema check failed')
        for name,msg in data['failures'][:30]: print(f'- {name}: {msg}')
        return 1
    print(f"🟢 core tool schema check ok: {data['checked']} tools")
    return 0
if __name__ == '__main__': raise SystemExit(main())
