"""Convergence and ERT analysis from best-so-far history sidecars.

A sidecar line looks like ``iteration=3 evals=80000 best=1.5e+01`` and is
emitted once per completed generation by the solver's ``--history`` option.
All functions use only the Python standard library.
"""
from __future__ import annotations

import math
import re
import statistics
from pathlib import Path
from typing import Sequence

HISTORY_LINE = re.compile(r"^iteration=(-?\d+) evals=(\d+) best=([^\s]+)$")


Trajectory = list[tuple[int, int, float]]  # (iteration, evals, best_so_far)


def load_history(path: Path) -> Trajectory:
    """Parse one best-so-far sidecar; reject non-monotone or non-finite rows."""
    trajectory: Trajectory = []
    previous_evals = -1
    previous_best = math.inf
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        # sidecar 缺失或不可读属于数据完整性问题，与解析失败同级报错，
        # 避免裸 traceback 掩盖真正的调用方诊断风格。
        raise ValueError(f"sidecar 不可读: {path}: {error}") from error
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = HISTORY_LINE.match(stripped)
        if match is None:
            raise ValueError(f"{path}:{line_number}: malformed history line: {stripped!r}")
        iteration = int(match.group(1))
        evals = int(match.group(2))
        best = float(match.group(3))
        if not math.isfinite(best):
            raise ValueError(f"{path}:{line_number}: best must be finite")
        if evals <= previous_evals:
            raise ValueError(f"{path}:{line_number}: evals must strictly increase")
        if best > previous_best:
            raise ValueError(f"{path}:{line_number}: best-so-far must not increase")
        if iteration != len(trajectory):
            raise ValueError(f"{path}:{line_number}: iteration must be consecutive from 0")
        trajectory.append((iteration, evals, best))
        previous_evals = evals
        previous_best = best
    if not trajectory:
        raise ValueError(f"{path}: empty history")
    return trajectory


def best_at_evals(trajectory: Trajectory,
                  checkpoints: Sequence[int]) -> dict[int, float]:
    """Best-so-far at each eval checkpoint (last row with evals <= checkpoint).

    A checkpoint before the first recorded eval evaluates to the first
    recorded best (the initial population is evaluated at evals=particles).
    """
    if not checkpoints:
        return {}
    result: dict[int, float] = {}
    index = 0
    for checkpoint in sorted(checkpoints):
        while (index + 1 < len(trajectory)
               and trajectory[index + 1][1] <= checkpoint):
            index += 1
        result[checkpoint] = trajectory[index][2]
    return result


def first_hit_evals(trajectory: Trajectory, target: float) -> int | None:
    """Evals at which the best-so-far first reaches target, or None."""
    for _iteration, evals, best in trajectory:
        if best <= target:
            return evals
    return None


def success_summary(trajectories: Sequence[Trajectory], target: float,
                    evals_budget: int) -> dict[str, float | int | None]:
    """Fixed-budget success stats for one target.

    Every run consumes the full budget (no-early-stop), so
    ERT = budget * n_runs / n_success; ERT is None when nothing succeeds.
    """
    if evals_budget <= 0:
        raise ValueError("evals_budget must be positive")
    hits = [first_hit_evals(t, target) for t in trajectories]
    successes = [h for h in hits if h is not None]
    n = len(trajectories)
    if n == 0:
        raise ValueError("no trajectories supplied")
    ert: float | None = None
    if successes:
        ert = evals_budget * n / len(successes)
    return {
        "target": target,
        "n_runs": n,
        "n_success": len(successes),
        "success_rate": len(successes) / n,
        "ert": ert,
        "median_hit_evals": (statistics.median(successes)
                             if successes else None),
    }
