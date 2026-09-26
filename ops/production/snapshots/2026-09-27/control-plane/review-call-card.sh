#!/usr/bin/env bash
set -euo pipefail
WS="${1:?workspace required}"
RUNNER=/home/hermes/hermes-improvement-lab/runner/static_gate.py
CASES=/home/hermes/hermes-improvement-lab/cases/call-card-routing.tsv
TARGET="$WS/candidate/SKILL.md"
[ -s "$TARGET" ] || TARGET="$WS/staged-candidate.SKILL.md"
OUT="$WS/protected-review-control.txt"
if [ ! -s "$TARGET" ]; then
  echo "VERDICT=FAIL"
  echo "FAIL|Process|candidate-missing"
  exit 2
fi
set +e
python3 "$RUNNER" --cases "$CASES" --target "$TARGET" --out "$OUT"
rc=$?
set -e
if grep -Eq '\\\\n- \[ \]|\\\\"' "$TARGET"; then
  echo 'FAIL|TrustSafety|structural-hygiene|literal escaped markdown detected' >> "$OUT"
  rc=2
fi
cat "$OUT"
exit "$rc"
