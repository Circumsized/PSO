"""Statistical analysis for formal benchmark data.

Paired bootstrap confidence intervals for speedup ratios, plus two
distribution comparisons for best values: the Mann-Whitney U rank-sum test
(two independent samples) and the Wilcoxon signed-rank test (paired samples,
which is what the formal matrix actually produces since every configuration
reuses the same 30 seeds). Uses only the Python standard library: the
bootstrap resamples with random.Random(seeded), and both tests use the normal
approximation with tie correction plus a 0.5 continuity correction toward the
expectation (there is no exact small-sample branch).
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Callable, Sequence

from plot_results import parse_log_strict  # noqa: F401  (shared strict parser, re-exported for tests)

RESULT_LINE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
COMMAND_SEED = re.compile(r"(?:^|\s)--seed\s+([0-9]+)(?:\s|$)")


def load_run_times(log_path: Path) -> list[tuple[int, float]]:
    """从日志加载严格正、有限的计时样本。"""
    rows = _parse_rows(log_path, positive_time=True)
    return [(int(row["seed"]), float(row["elapsed_max_s"])) for row in rows]


def load_run_bests(log_path: Path) -> list[tuple[int, float]]:
    """从日志加载有限的 best 样本。"""
    rows = _parse_rows(log_path)
    return [(int(row["seed"]), float(row["best"])) for row in rows]


def load_run_times_bound(log_path: Path,
                         expected_commands: dict[int, str]) -> list[tuple[int, float]]:
    """严格加载 manifest 绑定日志的 (seed, elapsed_max_s)。

    与 load_run_times 的区别：要求每条结果行都有对应 ``# command:`` 行、
    命令文本与 manifest 记录逐字一致（防篡改）、样本数等于 manifest 记录数，
    且 elapsed_max_s 严格为正（时间比统计不接受零/负计时）。
    OSError/ValueError 由调用方包成 SystemExit。
    """
    if not expected_commands:
        raise ValueError("expected_commands 不能为空")
    rows = parse_log_strict(log_path, len(expected_commands),
                            expected_commands=expected_commands)
    pairs: list[tuple[int, float]] = []
    for row in rows:
        value = float(row["elapsed_max_s"])
        if value <= 0.0:
            raise ValueError(
                f"{log_path}: elapsed_max_s 必须严格为正: seed={row['seed']}")
        pairs.append((int(row["seed"]), value))
    return pairs


def _parse_rows(log_path: Path, *, positive_time: bool = False) -> list[dict[str, str]]:
    text = log_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    marked = any(line.strip().startswith("# command:") for line in lines)
    pending_seed: int | None = None
    rows: list[dict[str, str]] = []
    seeds: set[int] = set()
    for number, line in enumerate(lines, 1):
        label = f"{log_path}:{number}"
        if line.strip().startswith("# command:"):
            if pending_seed is not None:
                raise ValueError(f"{label}: command/result 缺少结果")
            command_seeds = COMMAND_SEED.findall(line)
            if len(command_seeds) != 1:
                raise ValueError(f"{label}: command 必须包含唯一 seed")
            pending_seed = int(command_seeds[0])
            continue
        values = dict(RESULT_LINE.findall(line))
        if values.get("record") != "result":
            continue
        if marked and pending_seed is None:
            raise ValueError(f"{label}: command/result 顺序不匹配")
        if values.get("schema") != "pso_v1":
            raise ValueError(f"{label}: 不支持的结果 schema")
        if not all(field in values for field in ("seed", "elapsed_max_s", "best")):
            raise ValueError(f"{label}: result line missing seed/elapsed/best")
        if not re.fullmatch(r"[0-9]+", values["seed"]):
            raise ValueError(f"{label}: seed 必须为非负整数")
        seed = int(values["seed"])
        if marked and seed != pending_seed:
            raise ValueError(f"{label}: command/result seed 不一致")
        pending_seed = None
        if seed in seeds:
            raise ValueError(f"{label}: duplicate seeds in log")
        seeds.add(seed)
        for field in ("best", "elapsed_max_s"):
            try:
                value = float(values[field])
            except ValueError as error:
                raise ValueError(f"{label}: {field} 不是浮点数") from error
            if not math.isfinite(value):
                raise ValueError(f"{label}: {field} 必须有限")
            if field == "elapsed_max_s" and (value < 0 or (positive_time and value == 0)):
                raise ValueError(f"{label}: elapsed_max_s 不能用于时间比统计")
        rows.append(values)
    if pending_seed is not None:
        raise ValueError(f"{log_path}: command/result 缺少 seed={pending_seed} 的结果")
    if not rows:
        raise ValueError(f"{log_path}: no result rows")
    return rows


def paired_by_seed(baseline: Sequence[tuple[int, float]],
                   candidate: Sequence[tuple[int, float]]) -> list[tuple[float, float]]:
    """Join two run lists on seed; require identical seed sets."""
    base_map = dict(baseline)
    cand_map = dict(candidate)
    if set(base_map) != set(cand_map):
        raise ValueError("seed sets differ; paired statistics require identical seeds")
    return [(base_map[s], cand_map[s]) for s in sorted(base_map)]


def bootstrap_ratio_ci(baseline: Sequence[float], candidate: Sequence[float],
                      confidence: float = 0.95, iterations: int = 10000,
                      seed: int = 20240229) -> tuple[float, float, float]:
    """Paired bootstrap CI for median(baseline)/median(candidate).

    Resamples seed indices jointly (paired design), returns
    (ratio_point_estimate, ci_low, ci_high). Deterministic for a fixed seed.
    """
    if len(baseline) != len(candidate) or not baseline:
        raise ValueError("paired bootstrap requires equal-length non-empty samples")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations <= 0:
        raise ValueError("iterations must be a positive integer")
    _require_finite(baseline)
    _require_finite(candidate)
    if any(value <= 0.0 for value in (*baseline, *candidate)):
        raise ValueError("时间比样本必须严格为正")
    rng = random.Random(seed)
    n = len(baseline)
    base_median = _median_sorted(sorted(baseline))
    cand_median = _median_sorted(sorted(candidate))
    point = base_median / cand_median
    if not math.isfinite(point) or point <= 0:
        raise ValueError("时间比超出浮点可表示范围")
    ratios: list[float] = []
    for _ in range(iterations):
        indices = [rng.randrange(n) for _ in range(n)]
        base_sample = sorted(baseline[i] for i in indices)
        cand_sample = sorted(candidate[i] for i in indices)
        ratio = _median_sorted(base_sample) / _median_sorted(cand_sample)
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError("重采样时间比超出浮点可表示范围")
        ratios.append(ratio)
    ratios.sort()
    tail = (1.0 - confidence) / 2.0
    return point, _quantile_sorted(ratios, tail), _quantile_sorted(ratios, 1.0 - tail)


def _require_finite(values: Sequence[float]) -> None:
    if any(not math.isfinite(value) for value in values):
        raise ValueError("统计样本必须有限")


def _quantile_sorted(values: Sequence[float], q: float) -> float:
    """Hyndman–Fan Type 7：在排序样本的 (n-1)*q 位置线性插值。"""
    position = (len(values) - 1) * q
    left = math.floor(position)
    right = min(left + 1, len(values) - 1)
    fraction = position - left
    return values[left] + fraction * (values[right] - values[left])


def _median_sorted(values: Sequence[float]) -> float:
    n = len(values)
    mid = n // 2
    if n % 2 == 1:
        return values[mid]
    return values[mid - 1] + (values[mid] - values[mid - 1]) * 0.5


def mann_whitney_u(sample_a: Sequence[float],
                   sample_b: Sequence[float]) -> tuple[float, float, float]:
    """Two-sided Mann-Whitney U (Wilcoxon rank-sum) test.

    Returns (u_statistic, z_score, p_value). Ties get average ranks; the
    variance is tie-corrected. The normal approximation carries the standard
    continuity correction (0.5 toward the mean), which makes p slightly
    conservative; when u equals its expectation exactly (identical
    distributions) z is 0 and p is 1.
    """
    if not sample_a or not sample_b:
        raise ValueError("samples must be non-empty")
    _require_finite(sample_a)
    _require_finite(sample_b)
    merged = [(value, 0) for value in sample_a]
    merged += [(value, 1) for value in sample_b]
    merged.sort(key=lambda item: item[0])
    ranks: list[float] = []
    i = 0
    while i < len(merged):
        j = i
        while j < len(merged) and merged[j][0] == merged[i][0]:
            j += 1
        average_rank = (i + j - 1) / 2.0 + 1.0
        ranks.extend([average_rank] * (j - i))
        i = j
    rank_sum_a = sum(rank for rank, (_, group) in zip(ranks, merged) if group == 0)
    n_a, n_b = len(sample_a), len(sample_b)
    u_a = rank_sum_a - n_a * (n_a + 1) / 2.0
    u_b = n_a * n_b - u_a
    u = min(u_a, u_b)
    # tie correction
    tie_term = 0.0
    k = 0
    while k < len(merged):
        m = k
        while m < len(merged) and merged[m][0] == merged[k][0]:
            m += 1
        t = m - k
        if t > 1:
            tie_term += t ** 3 - t
        k = m
    n_total = n_a + n_b
    sigma = math.sqrt((n_a * n_b / 12.0)
                      * ((n_total + 1.0) - tie_term / (n_total * (n_total - 1.0))))
    if sigma == 0.0:
        return u, 0.0, 1.0
    mean_u = n_a * n_b / 2.0
    diff = u - mean_u
    # 连续性校正：秩和是离散量，向期望方向收 0.5 使 p 略偏保守。
    if diff < 0.0:
        diff += 0.5
    elif diff > 0.0:
        diff -= 0.5
    z = diff / sigma
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return u, z, p


def wilcoxon_signed_rank(differences: Sequence[float]) -> tuple[float, float, float]:
    """Two-sided Wilcoxon signed-rank test for paired samples.

    Returns (t_statistic, z_score, p_value). Zero differences are dropped and
    n shrinks accordingly; ties get average ranks, the variance is
    tie-corrected, and the normal approximation carries the same 0.5
    continuity correction toward the expectation as mann_whitney_u.

    Use this instead of mann_whitney_u when the two samples come from the same
    seeds: the formal matrix reuses seed 20240229+i across every configuration,
    so configurations are compared in pairs, and rank-sum treats them as
    independent samples.

    小样本和并列秩会影响正态近似精度；不存在适用于所有 n<=5 的 p 值下界。
    """
    _require_finite(differences)
    nz = [value for value in differences if value != 0.0]
    n = len(nz)
    if n == 0:
        raise ValueError("all differences are zero; signed-rank test undefined")
    magnitudes = sorted(abs(value) for value in nz)
    ranks: list[float] = []
    i = 0
    while i < n:
        j = i
        while j < n and magnitudes[j] == magnitudes[i]:
            j += 1
        average_rank = (i + j - 1) / 2.0 + 1.0
        ranks.extend([average_rank] * (j - i))
        i = j
    rank_of: dict[float, float] = {}
    for magnitude, rank in zip(magnitudes, ranks):
        rank_of[magnitude] = rank
    w_plus = sum(rank_of[abs(value)] for value in nz if value > 0.0)
    w_minus = sum(rank_of[abs(value)] for value in nz if value < 0.0)
    t = min(w_plus, w_minus)
    tie_term = 0.0
    k = 0
    while k < n:
        m = k
        while m < n and magnitudes[m] == magnitudes[k]:
            m += 1
        run = m - k
        if run > 1:
            tie_term += run ** 3 - run
        k = m
    mean_t = n * (n + 1) / 4.0
    sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0 - tie_term / 48.0)
    if sigma == 0.0:
        return t, 0.0, 1.0
    diff = t - mean_t
    if diff < 0.0:
        diff += 0.5
    elif diff > 0.0:
        diff -= 0.5
    z = diff / sigma
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return t, z, p


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_log", type=Path)
    parser.add_argument("candidate_log", type=Path)
    parser.add_argument("--metric", choices=("time", "best"), default="time")
    parser.add_argument("--iterations", type=int, default=10000)
    args = parser.parse_args(argv)
    loader: Callable[[Path], list[tuple[int, float]]] = (
        load_run_times if args.metric == "time" else load_run_bests)
    try:
        pairs = paired_by_seed(loader(args.baseline_log), loader(args.candidate_log))
        base_values = [b for b, _ in pairs]
        cand_values = [c for _, c in pairs]
        if args.metric == "time":
            point, low, high = bootstrap_ratio_ci(base_values, cand_values,
                                                  iterations=args.iterations)
            lines = [f"speedup_ratio median_base/median_cand: point={point:.4f} "
                     f"ci95=[{low:.4f}, {high:.4f}]"]
        else:
            u, z, p = mann_whitney_u(base_values, cand_values)
            t, wz, wp = wilcoxon_signed_rank([c - b for b, c in pairs])
            lines = [f"mann-whitney u={u:.1f} z={z:.4f} p={p:.3e}",
                     f"wilcoxon-signed-rank t={t:.1f} z={wz:.4f} p={wp:.3e}"]
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
