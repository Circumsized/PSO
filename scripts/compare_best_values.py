#!/usr/bin/env python3
"""Compare per-seed best values between two formal collections.

Primary uses:
- exact (--tol 0): same binaries re-collected under a different schedule
  (e.g. v4 unpinned vs v5 pinned) must match bitwise;
- tolerant (--tol > 0): cross-compiler replication (Linux vs Windows),
  where floating-point contraction may differ within rounding noise.

Each log of the pso_v1 formal naming contributes one (config, seed) pair.
Exit 0 only when every pair present in both collections matches within
--tol and no log/seed is one-sided; otherwise exit 1 with a report.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from summarize_convergence import LOG_NAME  # noqa: E402

RECORD = re.compile(r"^record=result schema=pso_v1 .*")
# \b 是单词边界而不是字段结束：'seed=1.9' / 'best=0.5+' 会被回溯截成
# 合法前缀。先完整吃下非空白 token，再由下方做严格整型/浮点校验。
SEED = re.compile(r"(?:^|\s)seed=(\S+)")
BEST = re.compile(r"(?:^|\s)best=(\S+)")
SEED_INT = re.compile(r"[0-9]+")


def collect_best(root: Path) -> dict[tuple, float]:
    """Map (relative_log_path, seed) -> best for one collection root.

    同名日志可合法地同时出现在根目录与子目录（如跨套件合并的配置），
    因此按相对路径而非配置名配对。
    """
    values: dict[tuple, float] = {}
    if not root.is_dir():
        raise SystemExit(f"对照目录不存在或不是目录: {root}")
    for log_path in sorted(root.rglob("*.log")):
        match = LOG_NAME.match(log_path.name)
        if match is None:
            raise SystemExit(
                f"日志 {log_path} 不符合正式矩阵命名；拒绝比较未知数据")
        relative = log_path.relative_to(root).as_posix()
        if not log_path.is_file():
            raise SystemExit(f"日志路径不是可读文件: {log_path}")
        records = 0
        # 正式日志是纯 ASCII。任何解码失败都是损坏，必须带定位硬失败：
        # errors="replace" 会把坏字节洗成"看似合法"的行（实测 best 末字节
        # 损坏会让 \b 回溯把值静默截断，产生假 OK）。
        try:
            text = log_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise SystemExit(
                f"{log_path}: 编码损坏，拒绝比较: {error}") from error
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            # 正式日志只含 # command: 行与 pso_v1 结果行；任何其它非空行
            # 都是截断/损坏（此前尾部碎片短于结果行前缀时被静默丢弃，
            # 配对集合悄悄缩小仍可能全绿）。
            if (not line.startswith("# command:")
                    and not RECORD.match(line)):
                raise SystemExit(
                    f"{log_path}:{line_number}: 非法行（既非 # command: 亦非"
                    f"pso_v1 结果行），疑似截断: {line[:60]!r}")
            if not RECORD.match(line):
                continue
            seed_tokens = SEED.findall(line)
            best_tokens = BEST.findall(line)
            if len(seed_tokens) != 1 or len(best_tokens) != 1:
                raise SystemExit(
                    f"{log_path}:{line_number}: pso_v1 记录必须恰好各含一个 "
                    f"seed=/best= 字段")
            raw_seed, raw_best = seed_tokens[0], best_tokens[0]
            if SEED_INT.fullmatch(raw_seed) is None:
                raise SystemExit(
                    f"{log_path}:{line_number}: seed 不是非负整数: {raw_seed!r}")
            try:
                best = float(raw_best)
            except ValueError:
                raise SystemExit(
                    f"{log_path}:{line_number}: best 不是数值: "
                    f"{raw_best!r}") from None
            # NaN 会让 delta 比较恒为 False 而静默通过（最强的 --tol 0
            # 逐位一致模式尤其危险），因此非有限值必须与缺失 seed 同等硬失败。
            if not math.isfinite(best):
                raise SystemExit(
                    f"{log_path}:{line_number}: best 非有限: {raw_best!r}")
            key = (relative, int(raw_seed))
            if key in values:
                raise SystemExit(
                    f"{log_path}:{line_number}: seed {raw_seed}"
                    " 重复出现；比较要求单轮干净采集")
            values[key] = best
            records += 1
        # 空文件/截断日志若零记录会静默缩小对比范围（pairs 变少仍可能全绿），
        # 与其余正式消费者的"缺日志硬失败"口径对齐。
        if records == 0:
            raise SystemExit(
                f"日志 {log_path} 不含任何 pso_v1 记录；"
                "疑似空或截断日志，拒绝静默缩小对比范围")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", type=Path, required=True)
    parser.add_argument("--b", type=Path, required=True)
    parser.add_argument("--tol", type=float, default=0.0,
                        help="允许的最大绝对差；0 表示逐位一致")
    parser.add_argument("--allow-subset", action="store_true",
                        help="B 允许为 A 的子集（仅 A 多出的条目降级为"
                             "行信息；B 多出或共享对不匹配仍是失败）")
    parser.add_argument("--max-report", type=int, default=20)
    args = parser.parse_args(argv)
    if not math.isfinite(args.tol) or args.tol < 0:
        parser.error("--tol 必须是有限非负数（0 表示逐位一致）")

    values_a = collect_best(args.a)
    values_b = collect_best(args.b)
    only_a = sorted(set(values_a) - set(values_b))
    only_b = sorted(set(values_b) - set(values_a))
    shared = sorted(set(values_a) & set(values_b))
    if not shared:
        raise SystemExit(
            f"两个集合没有共享的 (日志, seed) 配对"
            f"（A {len(values_a)} 对、B {len(values_b)} 对）；"
            "拒绝零次比较的成功结论。请核对 --a/--b 路径与集合内容")

    worst: dict[str, float] = defaultdict(float)
    mismatches: list[tuple] = []
    for key in shared:
        delta = abs(values_a[key] - values_b[key])
        config = key[0]
        worst[config] = max(worst[config], delta)
        if delta > args.tol:
            mismatches.append((key, values_a[key], values_b[key], delta))

    print(f"A = {args.a}  ({len(values_a)} pairs)")
    print(f"B = {args.b}  ({len(values_b)} pairs)")
    print(f"shared: {len(shared)} | matched(<= {args.tol:g}): "
          f"{len(shared) - len(mismatches)} | mismatched: {len(mismatches)}")
    print(f"only in A: {len(only_a)} | only in B: {len(only_b)}")

    if worst:
        print("\n== per-config max |delta| ==")
        for config in sorted(worst):
            print(f"  {config}: {worst[config]:.17g}")

    status = 0
    if mismatches:
        status = 1
        print(f"\n== first {min(len(mismatches), args.max_report)} "
              "mismatches ==")
        for key, best_a, best_b, delta in mismatches[:args.max_report]:
            print(f"  {key[0]} seed={key[1]}: A={best_a!r} "
                  f"B={best_b!r} |delta|={delta:.3g}")
    if only_b:
        status = 1
    elif only_a:
        if args.allow_subset:
            print(f"subset 模式：A 多出的 {len(only_a)} 项仅为信息，不构成失败")
        else:
            status = 1
    if only_a or only_b:
        for key in only_a[:args.max_report]:
            print(f"  only in A: {key[0]} seed={key[1]}")
        for key in only_b[:args.max_report]:
            print(f"  only in B: {key[0]} seed={key[1]}")
    if status == 0:
        print("COMPARISON OK")
    else:
        print("COMPARISON FAILED", file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
