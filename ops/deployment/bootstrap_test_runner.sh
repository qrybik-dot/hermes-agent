#!/usr/bin/env bash
set -euo pipefail

RUNTIME_VENV=${HERMES_RUNTIME_VENV:-/home/hermes/hermes-runtime/shared/venv}
TEST_VENV=${HERMES_TEST_VENV:-/home/hermes/hermes-runtime/shared/test-venv}
UV=${UV_BIN:-/home/hermes/.local/bin/uv}

if [[ ! -x "$RUNTIME_VENV/bin/python" ]]; then
  echo "runtime venv is unavailable: $RUNTIME_VENV" >&2
  exit 2
fi
if [[ ! -x "$TEST_VENV/bin/python" ]]; then
  cp -a --reflink=auto "$RUNTIME_VENV" "$TEST_VENV"
fi

"$UV" pip install --python "$TEST_VENV/bin/python" \
  "pytest==9.0.2" \
  "pytest-asyncio==1.3.0"
"$TEST_VENV/bin/python" -c \
  'import pytest, pytest_asyncio; assert pytest.__version__ == "9.0.2"; assert pytest_asyncio.__version__ == "1.3.0"'
