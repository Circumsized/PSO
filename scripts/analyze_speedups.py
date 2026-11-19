#!/usr/bin/env python3
"""Paired speedup table over one formal collection root.

Reads the root manifest's configuration, binds every command to its
recorded log path and per-seed command text, pairs every strong-scaling
log against the serial baseline by seed, and prints bootstrap speedup CIs
(median serial time / median candidate time; values > 1 mean faster).
Uses the deterministic bootstrap from analyze_stats (seed 20240229).
Any missing strong-scaling log aborts with the full list instead of
printing a dash placeholder for it.

日志不是按 configuration 拼名读取，而是逐条来自 manifest 的
experiment → (log, seed→command_text) 绑定；returncode 非 0、缺溯源字段、
命令文本被篡改的 manifest 一律硬失败（与 plot/summarize 同口径）。

Usage:
  python scripts/analyze_speedups.py --manifest-root results/formal-20260912-v4 \
      [--iterations 10000] [--impls mpi_ms,mpi_island]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_stats import (  # noqa: E402
    bootstrap_ratio_ci, load_run_times_bound, paired_by_seed)
from run_experiments import Experiment, log_filename  # noqa: E402

IMPL_LABELS = {"mpi_ms": "mpi-data-parallel", "mpi_island": "mpi-island"}


def load_manifest_bindings(
        root: Path) -> tuple[dict, dict[Experiment, tuple[Path, dict[int, str]]]]:
    """把每条命令绑定为 Experiment → (日志, seed→命令文本)。

    与 plot/summarize 的严格层同口径：status/returncode/seed/command_text/
    experiment/log 缺一即硬失败；日志必须在 root 内且非空。速度比表只允许
    使用这份绑定里的日志，不得再按 configuration 拼路径。
    """
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SystemExit(f"manifest 不可读或损坏: {root} ({error})") from error
    if not isinstance(manifest, dict):
        raise SystemExit(f"manifest 必须是 JSON 对象: {root}")
    if manifest.get("schema") != "pso_experiments_v1":
        raise SystemExit(f"manifest schema 不受支持: {root}")
    if manifest.get("dry_run") is not False:
        raise SystemExit(f"manifest 是 dry-run 空壳，未真实采集: {root}")
    commands = manifest.get("commands")
    if not isinstance(commands, list) or not commands:
        raise SystemExit(f"manifest commands 必须是非空列表: {root}")
    config = manifest.get("configuration")
    if not isinstance(config, dict):
        raise SystemExit(f"manifest 缺少 configuration: {root}")
    missing = [key for key in ("functions", "particles", "dim", "max_evals",
                              "ranks", "threads", "boundaries", "dynamics",
                              "migration_intervals") if key not in config]
    if missing:
        raise SystemExit(f"manifest configuration 缺少键: {missing}")
    if config.get("mode") not in ("all", "baseline", "strong"):
        raise SystemExit("根 manifest 需包含 baseline/strong 套件")
    root_resolved = root.resolve()
    bindings: dict[Experiment, tuple[Path, dict[int, str]]] = {}
    for index, command in enumerate(commands):
        label = f"{root}: command {index}"
        if not isinstance(command, dict):
            raise SystemExit(f"{label} must be an object")
        if command.get("status") != "completed":
            raise SystemExit(f"{label} is not completed")
        returncode = command.get("returncode")
        if isinstance(returncode, bool) or returncode != 0:
            raise SystemExit(f"{label} has non-zero or missing returncode")
        seed = command.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise SystemExit(f"{label} 缺少合法非负整数 seed（防篡改核验需要）")
        command_text = command.get("command_text")
        if not isinstance(command_text, str) or not command_text:
            raise SystemExit(f"{label} 缺少 command_text（防篡改核验需要）")
        plan = command.get("experiment")
        if not isinstance(plan, dict):
            raise SystemExit(f"{label} 缺少 experiment（溯源绑定需要）")
        try:
            experiment = Experiment(**plan)
        except TypeError as error:
            raise SystemExit(f"{label} experiment 字段不匹配: {error}") from error
        log_value = command.get("log")
        if not isinstance(log_value, str) or not log_value:
            raise SystemExit(f"{label} has no log path")
        log_path = Path(log_value)
        if not log_path.is_absolute():
            raise SystemExit(f"{label} log path must be absolute: {log_path}")
        resolved = log_path.resolve()
        if not resolved.is_relative_to(root_resolved):
            raise SystemExit(f"{label} log is outside manifest root: {resolved}")
        if not resolved.is_file() or resolved.stat().st_size == 0:
            raise SystemExit(f"{label} log is missing or empty: {resolved}")
        entry = bindings.setdefault(experiment, (resolved, {}))
        if entry[0] != resolved:
            raise SystemExit(f"{label}: experiment 对应多个日志: {resolved}")
        if seed in entry[1]:
            raise SystemExit(f"{label}: 日志 {resolved} 的 seed {seed} 重复")
        entry[1][seed] = command_text
    return config, bindings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--impls", default="mpi_ms,mpi_island")
    args = parser.parse_args(argv)
    if args.iterations <= 0:
        parser.error("--iterations 必须为正整数")
    impls = [impl.strip() for impl in args.impls.split(",") if impl.strip()]
    if not impls:
        parser.error("--impls 不能为空")
    config, bindings = load_manifest_bindings(args.manifest_root)
    baseline_thread = config["threads"][0]

    def load_times(path: Path, expected_commands: dict[int, str]):
        try:
            return load_run_times_bound(path, expected_commands)
        except (OSError, ValueError) as error:
            raise SystemExit(str(error)) from error

    header = (f"{'function':<10} {'implementation':<18} {'threads':>7}"
              + "".join(f"  r{r:<2}".rjust(22) for r in config["ranks"]))
    # 先完整算出每一行再打印：缺日志时不允许留下半张表。
    rows: list[tuple[str, str, int, list[str]]] = []
    missing: list[Path] = []
    for function in config["functions"]:
        base_experiment = Experiment(
            "baseline", "serial", function, config["particles"],
            config["dim"], config["max_evals"], 1, baseline_thread,
            config["boundaries"][0], config["dynamics"][0], 0)
        entry = bindings.get(base_experiment)
        if entry is None:
            raise SystemExit(
                "缺少基线日志，拒绝出表：\n  "
                + str(args.manifest_root / log_filename(base_experiment)))
        base_path, base_expected = entry
        base_times = load_times(base_path, base_expected)
        for impl in impls:
            # --impls 用下划线 token，manifest 的 implementation 用连字符；
            # 查表前必须归一化，否则 strong 日志全部查不到。
            impl_key = impl.replace("_", "-")
            # strong 岛模型使用第一个迁移间隔（与 build_experiment_matrix 一致）
            migration = (config["migration_intervals"][0]
                         if impl_key == "mpi-island" else 0)
            for thread in config["threads"]:
                cells = []
                for rank in config["ranks"]:
                    experiment = Experiment(
                        "strong", impl_key, function, config["particles"],
                        config["dim"], config["max_evals"], rank, thread,
                        config["boundaries"][0], config["dynamics"][0],
                        migration)
                    entry = bindings.get(experiment)
                    if entry is None:
                        missing.append(
                            args.manifest_root / log_filename(experiment))
                        continue
                    path, expected = entry
                    try:
                        paired = paired_by_seed(
                            base_times, load_times(path, expected))
                        point, low, high = bootstrap_ratio_ci(
                            [base for base, _ in paired],
                            [cand for _, cand in paired],
                            iterations=args.iterations)
                    except (ValueError, OSError) as error:
                        raise SystemExit(str(error)) from error
                    cells.append(f"x{point:.2f} [{low:.2f},{high:.2f}]")
                rows.append((function, IMPL_LABELS.get(impl, impl), thread, cells))
    if missing:
        raise SystemExit("缺少 strong 日志，拒绝以占位符静默降级：\n  "
                         + "\n  ".join(str(path) for path in missing))
    print(header)
    for function, label, thread, cells in rows:
        row = f"{function:<10} {label:<18} {thread:>7}"
        for cell in cells:
            row += "  " + cell.rjust(20)
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
