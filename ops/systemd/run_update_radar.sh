#!/bin/sh
set -eu
export HOME=/home/hermes
export HERMES_HOME=/home/hermes/.hermes
export PATH=/home/hermes/hermes-v018-integration/.venv/bin:/home/hermes/.local/bin:/usr/local/bin:/usr/bin:/bin
STATE_DIR="$HERMES_HOME/update-radar"
LOG_DIR="$STATE_DIR/logs"
mkdir -p "$LOG_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
ERR_LOG="$LOG_DIR/run-$STAMP.log"
OUTPUT=$(/home/hermes/hermes-v018-integration/.venv/bin/python /home/hermes/hermes-v018-integration/scripts/update_radar.py --telegram-card 2>"$ERR_LOG") || {
  STATUS=$?
  printf 'Update Radar failed with exit %s. See %s\n' "$STATUS" "$ERR_LOG" >&2
  exit "$STATUS"
}
if [ -z "$OUTPUT" ]; then
  rm -f "$ERR_LOG"
  exit 0
fi
# Telegram messages have practical size limits. The full report is already saved by the collector.
MESSAGE=$(printf '%s' "$OUTPUT" | head -c 3900)
/home/hermes/hermes-v018-integration/.venv/bin/python -m hermes_cli.main send --to telegram "$MESSAGE"
