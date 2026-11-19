#!/usr/bin/env python3
"""Validate a PSO --history convergence file.

Usage: check_history.py PATH expected_iterations particles [result_best]

Checks: iteration counts 0..expected_iterations in order, evals arithmetic
(particles per iteration), best monotone non-increasing, and optionally that
the final best matches the reported result-line best.
"""
from __future__ import annotations

import math
import re
import sys

LINE = re.compile(r"^iteration=(-?\d+) evals=(\d+) best=([^\s]+)$")


def fail(message: str) -> None:
    print(f"history check FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if len(sys.argv) not in (4, 5):
        print(__doc__, file=sys.stderr)
        return 2
    path, expected_iterations, particles = (
        sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
    result_best = float(sys.argv[4]) if len(sys.argv) == 5 else None

    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError as error:
        fail(f"cannot read {path}: {error}")
    if len(lines) != expected_iterations + 1:
        fail(f"expected {expected_iterations + 1} records, found {len(lines)}")

    previous_best = None
    for index, line in enumerate(lines):
        match = LINE.match(line)
        if match is None:
            fail(f"record {index} does not match schema: {line!r}")
        iteration = int(match.group(1))
        evals = int(match.group(2))
        try:
            best_text = float(match.group(3))
        except ValueError:
            fail(f"record {index} best is not numeric: {match.group(3)!r}")
        # NaN 与任何比较都为 False，单调校验对它形同虚设；成功运行的
        # 全局 best 一旦有限就单调不增，非有限值只能是坏数据。
        if not math.isfinite(best_text):
            fail(f"record {index} best is not finite: {match.group(3)!r}")
        if iteration != index:
            fail(f"record {index} reports iteration={iteration}")
        if evals != particles * (iteration + 1):
            fail(f"record {index} reports evals={evals}, expected "
                 f"{particles * (iteration + 1)}")
        if previous_best is not None and best_text > previous_best:
            fail(f"best increased at record {index}: {previous_best} -> {best_text}")
        previous_best = best_text

    if result_best is not None and previous_best != result_best:
        fail(f"final best {previous_best} != result line best {result_best}")
    print(f"history check OK: {len(lines)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
