#!/usr/bin/env python3
import sys
from gateway.status import write_planned_stop_marker

if len(sys.argv) != 2:
    raise SystemExit(2)
try:
    pid = int(sys.argv[1])
except ValueError:
    raise SystemExit(2)
raise SystemExit(0 if write_planned_stop_marker(pid) else 1)
