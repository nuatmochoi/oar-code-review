"""Freeze a budget/depth sensitivity audit without reviewer calls."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .disambiguation_audit import audit_disambiguation_pair


def run_sweep(pair: str, output: str) -> dict:
    records = []
    with tempfile.TemporaryDirectory(prefix="ace-factorial-") as directory:
        for variant in ("safe", "unsafe"):
            for budget in (16, 24, 32, 64, 128, 1200):
                for depth in (3, 4, 6):
                    records.append(audit_disambiguation_pair(
                        pair, Path(directory) / f"{variant}-{budget}-{depth}.json",
                        variant=variant, budget=budget, max_depth=depth,
                    ))
    report = {
        "scope": "one reused synthetic family, sensitivity cells are not independent samples",
        "reviewer_calls": 0,
        "cells": len(records),
        "token_accounting": "sum of node content estimates, excluding edge and packet overhead",
        "records": records,
    }
    with Path(output).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pair")
    parser.add_argument("output")
    args = parser.parse_args()
    report = run_sweep(args.pair, args.output)
    print(json.dumps({"cells": report["cells"], "reviewer_calls": 0}))


if __name__ == "__main__":
    main()
