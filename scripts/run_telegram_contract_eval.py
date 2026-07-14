#!/usr/bin/env python3
"""Grade sanitized observations produced by a Hermes model/gateway run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.telegram_contract.runner import (
    load_observations,
    reference_observations,
    run_suite,
    summary,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", help="JSONL exported by an isolated or shadow model run")
    parser.add_argument(
        "--self-test", action="store_true",
        help="Test the grader with synthetic compliant observations; not model evidence",
    )
    args = parser.parse_args()
    if bool(args.observations) == bool(args.self_test):
        parser.error("choose exactly one of --observations or --self-test")
    observations = reference_observations() if args.self_test else load_observations(args.observations)
    report = summary(run_suite(observations))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
