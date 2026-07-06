#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

runtime = Path(os.environ.get("HERMES_RUNTIME_DIR", "/home/hermes/hermes-v018-integration"))
if str(runtime) not in sys.path:
    sys.path.insert(0, str(runtime))

from hermes_cli.daily_card import main

raise SystemExit(main(["morning", "--cron"]))
