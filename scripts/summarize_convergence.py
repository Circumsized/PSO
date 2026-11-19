#!/usr/bin/env python3
"""Summarize convergence and ERT from a manifest-root with history sidecars.

Reads every completed manifest under a root, joins each result row with its
per-run history sidecar (manifest ``history`` field), and prints:
1. median best-so-far at eval checkpoints per (suite, implementation, config)
2. fixed-budget ERT for a grid of quality targets per log group

Usage:
  python scripts/summarize_convergence.py --manifest-root results/formal-20260912-v4 \
      [--targets 300,250,200] [--checkpoints 10000,25000,50000,100000]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_convergence import (  # noqa: E402
    Trajectory, best_at_evals, first_hit_evals, load_history, success_summary,
)
from plot_results import parse_log_strict  # noqa: E402
from run_experiments import Experiment, format_command, log_filename  # noqa: E402

LOG_NAME = re.compile(
    r"^(?P<suite>baseline|strong|weak|ablation)__(?P<impl>\w+)__fn-(?P<fn>\w+)"
    r"__p-(?P<p>\d+)__d-(?P<d>\d+)__r-(?P<r>\d+)__t-(?P<t>\d+)"
    r"__b-(?P<b>\w+)__dyn-(?P<dyn>[\w-]+)__mi-(?P<mi>\d+)\.log$")


def _command_value(argv: list[str], option: str, label: str,
                   default: str | None = None) -> str:
    indices = [i for i, value in enumerate(argv) if value == option]
    if not indices and default is not None:
        return default
    if len(indices) != 1 or indices[0] + 1 >= len(argv):
        raise SystemExit(f"{label}: command 必须包含唯一 {option} 值")
    return argv[indices[0] + 1]


def _validate_identity(log: Path, history: Path, command: dict,
                       row: dict[str, str]) -> None:
    label = f"{log}: seed {command['seed']}"
    plan = command.get("experiment")
    if not isinstance(plan, dict):
        raise SystemExit(f"{label}: 缺少 experiment 配置")
    try:
        experiment = Experiment(**plan)
    except TypeError as error:
        raise SystemExit(f"{label}: experiment 配置无效: {error}") from error
    for field in ("particles", "dim", "ranks", "threads", "max_evals", "migration_interval"):
        value = plan.get(field)
        minimum = 0 if field in ("max_evals", "migration_interval") else 1
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise SystemExit(f"{label}: experiment {field} 无效")
    for field in ("scaling", "implementation", "function", "boundary", "dynamics"):
        if not isinstance(plan.get(field), str):
            raise SystemExit(f"{label}: experiment {field} 无效")
    if log.name != log_filename(experiment):
        raise SystemExit(f"{label}: 日志命名配置与 experiment 不一致")
    for field in ("particles", "dim", "ranks", "threads", "max_evals", "migration_interval",
                  "function", "boundary", "dynamics", "implementation"):
        expected = plan[field]
        if field == "implementation" and expected == "mpi-ms":
            expected = "mpi-data-parallel"
        if row.get(field) != str(expected):
            raise SystemExit(f"{label}: 结果 {field} 与 experiment 不一致")
    argv = command.get("command")
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
        raise SystemExit(f"{label}: command 必须为非空字符串数组")
    command_text = command.get("command_text")
    if command_text not in (format_command(argv, windows=True), format_command(argv, windows=False)):
        raise SystemExit(f"{label}: command_text 与 command 不一致")
    for option, expected in (("--seed", command["seed"]), ("--function", plan["function"]),
                             ("--particles", plan["particles"]), ("--dim", plan["dim"]),
                             ("--max-evals", plan["max_evals"]), ("--boundary", plan["boundary"])):
        if _command_value(argv, option, label) != str(expected):
            raise SystemExit(f"{label}: command {option} 与计划不一致")
    history_arg = Path(_command_value(argv, "--history", label))
    if not history_arg.is_absolute() or history_arg.resolve() != history:
        raise SystemExit(f"{label}: command --history 与轨迹引用不一致")
    if history != Path(f"{log}.seed-{command['seed']}.history"):
        raise SystemExit(f"{label}: history 路径与日志、seed 不配对")
    expected_dynamics = "linear-inertia" if "--linear-inertia" in argv else "constriction"
    if plan["dynamics"] != expected_dynamics:
        raise SystemExit(f"{label}: command dynamics 与计划不一致")
    if row.get("stop_on_target") != ("0" if "--no-early-stop" in argv else "1"):
        raise SystemExit(f"{label}: command stop_on_target 与结果不一致")
    if row.get("max_iters") != _command_value(argv, "--max-iters", label, "1000"):
        raise SystemExit(f"{label}: command max_iters 与结果不一致")
    if plan["implementation"] != "serial":
        if _command_value(argv, "-n", label) != str(plan["ranks"]):
            raise SystemExit(f"{label}: command ranks 与计划不一致")
    if plan["implementation"] == "mpi-island":
        if _command_value(argv, "--migration-interval", label) != str(plan["migration_interval"]):
            raise SystemExit(f"{label}: command migration_interval 与计划不一致")
    environment = command.get("environment")
    if not isinstance(environment, dict) or environment.get("OMP_NUM_THREADS") != str(plan["threads"]):
        raise SystemExit(f"{label}: environment threads 与计划不一致")
    try:
        target = float(_command_value(argv, "--target", label, "1e-8"))
        if not math.isfinite(target) or target < 0 or float(row["target"]) != target:
            raise ValueError
    except (ValueError, KeyError):
        raise SystemExit(f"{label}: command target 与结果不一致")


def _validated_commands(manifest_path: Path) -> list[dict]:
    """只接纳非空、全部成功完成的正式 manifest。"""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SystemExit(f"manifest 不可读: {manifest_path}: {error}") from error
    if not isinstance(manifest, dict):
        raise SystemExit(f"manifest 必须是 JSON 对象: {manifest_path}")
    if manifest.get("schema") != "pso_experiments_v1":
        raise SystemExit(
            f"manifest schema 不受支持: {manifest_path} "
            f"(实际 {manifest.get('schema')!r})")
    if manifest.get("dry_run") is not False:
        raise SystemExit(
            f"manifest 是 dry-run 空壳，未真实采集: {manifest_path}")
    commands = manifest.get("commands")
    if not isinstance(commands, list) or not commands:
        raise SystemExit(f"manifest commands 必须是非空列表: {manifest_path}")
    for index, command in enumerate(commands):
        label = f"{manifest_path}: command {index}"
        if not isinstance(command, dict):
            raise SystemExit(f"{label} 必须是对象")
        if command.get("status") != "completed":
            raise SystemExit(
                f"{label} 未完成 (status={command.get('status')!r})")
        returncode = command.get("returncode")
        if isinstance(returncode, bool) or returncode != 0:
            raise SystemExit(
                f"{label} 返回码非零或缺失 ({returncode!r})")
        seed = command.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise SystemExit(f"{label} 缺少合法非负整数 seed")
        command_text = command.get("command_text")
        if not isinstance(command_text, str) or not command_text:
            raise SystemExit(f"{label} 缺少 command_text（日志防篡改核验需要）")
    return commands


def _contained_path(value: object, field: str, root: Path, label: str) -> Path:
    """路径规范化后检查边界，也用于识别别名重复。"""
    if not isinstance(value, str) or not value.strip():
        hint = ""
        if field == "history":
            hint = ("——history 字段缺失或无效：该集合可能未用 --history "
                    "采集，本工具依赖收敛 sidecar 无法工作")
        raise SystemExit(f"{label}: {field} 必须是非空路径{hint}")
    path = Path(value)
    if not path.is_absolute():
        raise SystemExit(f"{label}: {field} 必须是绝对路径: {value}")
    try:
        path = path.resolve()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"{label}: {field} 路径无效: {value}: {error}") from error
    if not path.is_relative_to(root):
        raise SystemExit(f"{label}: {field} 超出 manifest root: {path}")
    return path


def _history_column(manifest_root: Path) -> dict[Path, dict[int, tuple[Path, dict]]]:
    """保留全部命令，以日志和 seed 关联唯一 sidecar。"""
    root = manifest_root.resolve()
    if not root.is_dir():
        raise SystemExit(f"manifest root 不是目录: {manifest_root}")
    manifests = sorted(root.rglob("manifest.json"))
    if not manifests:
        raise SystemExit(f"根目录下没有 manifest.json: {root}")
    mapping: dict[Path, dict[int, tuple[Path, dict]]] = defaultdict(dict)
    log_manifests: dict[Path, Path] = {}
    used_histories: set[Path] = set()
    for manifest_path in manifests:
        for index, command in enumerate(_validated_commands(manifest_path)):
            label = f"{manifest_path}: command {index}"
            log = _contained_path(command.get("log"), "log", root, label)
            history = _contained_path(command.get("history"), "history", root, label)
            owner = log_manifests.setdefault(log, manifest_path)
            if owner != manifest_path:
                raise SystemExit(f"{label}: 日志被不同 manifest 重复引用: {log}")
            seed = command["seed"]
            if seed in mapping[log]:
                raise SystemExit(
                    f"{label}: 日志 {log} 的 seed {seed} 重复出现；"
                    "history 配对只在单次干净采集（未 --append 追加）下成立")
            if history in used_histories:
                raise SystemExit(f"{label}: history 共用: {history}")
            used_histories.add(history)
            mapping[log][seed] = (history, command)
    return dict(mapping)


def _result_int(row: dict[str, str], field: str, label: str,
                minimum: int = 0) -> int:
    """结果计数必须为整数，不能截断浮点值。"""
    value = row.get(field, "")
    if not re.fullmatch(r"[0-9]+", value) or int(value) < minimum:
        raise SystemExit(f"{label}: {field} 必须是 >= {minimum} 的整数: {value!r}")
    return int(value)


def _validated_trajectories(log: Path, histories: dict[int, tuple[Path, dict]],
                            fixed_budget: bool) -> list[Trajectory]:
    """每个日志只解析一次，先核齐 seed，再逐轨迹对账。"""
    try:
        # seed→命令文本核验：日志里的 # command: 行必须与 manifest 记录
        # 一致，防止篡改命令行（含 --seed）后数据被静默接受。
        rows = parse_log_strict(
            log, expected_results=len(histories),
            expected_commands={
                seed: command["command_text"]
                for seed, (_, command) in histories.items()})
    except (OSError, ValueError) as error:
        raise SystemExit(f"日志解析失败: {log}: {error}") from error
    rows_by_seed: dict[int, dict[str, str]] = {}
    for row in rows:
        seed = _result_int(row, "seed", str(log))
        if seed in rows_by_seed:
            raise SystemExit(f"日志 {log} 的结果 seed {seed} 重复出现")
        rows_by_seed[seed] = row
    if rows_by_seed.keys() != histories.keys():
        missing = sorted(histories.keys() - rows_by_seed.keys())
        extra = sorted(rows_by_seed.keys() - histories.keys())
        raise SystemExit(f"日志 {log} 的 seed 不匹配: 缺少 {missing}，多出 {extra}")

    trajectories: list[Trajectory] = []
    for seed, (history, command) in histories.items():
        row = rows_by_seed[seed]
        label = f"{log}: seed {seed} ({history})"
        particles = _result_int(row, "particles", label, minimum=1)
        evals = _result_int(row, "evals", label, minimum=1)
        iterations = _result_int(row, "iterations", label)
        _validate_identity(log, history, command, row)
        try:
            trajectory = load_history(history)
        except ValueError as error:
            raise SystemExit(f"sidecar 解析失败: {error}") from error
        if trajectory[0][1] != particles:
            raise SystemExit(f"{label}: 初始 evals 必须等于 particles={particles}")
        for field, actual, expected in (("evals", trajectory[-1][1], evals),
                                        ("best", trajectory[-1][2], float(row["best"])),
                                        ("iteration", trajectory[-1][0], iterations)):
            if actual != expected:
                raise SystemExit(
                    f"{label}: 末行 {field}={actual} 与结果 {expected} 不一致")
        if any(e != (iteration + 1) * particles for iteration, e, _ in trajectory):
            raise SystemExit(f"{label}: evals 必须逐代推进 particles={particles}")
        # 结果行的 fes_to_target 必须与轨迹首次命中同一代：两侧都由求解器
        # 在整代结束后写定（main_serial.c 的 reached→fes_to_target 与
        # pso_history_append 使用同一 evals），不等即说明数据被拼改。
        first_hit = first_hit_evals(trajectory, float(row["target"]))
        if row.get("success") == "1":
            if first_hit is None or _result_int(
                    row, "fes_to_target", label) != first_hit:
                raise SystemExit(
                    f"{label}: fes_to_target={row.get('fes_to_target')} "
                    f"与轨迹首次命中 {first_hit} 不一致")
        elif first_hit is not None:
            raise SystemExit(
                f"{label}: success={row.get('success')} 但轨迹已在 "
                f"evals={first_hit} 命中 target")
        if fixed_budget:
            if row.get("stop_on_target") != "0":
                raise SystemExit(f"{label}: 固定预算 ERT 要求 stop_on_target=0")
            max_evals = _result_int(row, "max_evals", label)
            max_iters = _result_int(row, "max_iters", label, minimum=1)
            # max_evals 非零时覆盖迭代上限，只执行完整一代。
            budget = (max_evals // particles * particles if max_evals
                      else (max_iters + 1) * particles)
            if evals != budget:
                raise SystemExit(
                    f"{label}: 固定预算应结束于 evals={budget}，实际 {evals}")
        trajectories.append(trajectory)
    return trajectories


# 输出组标签与 pso_v1 结果行的 implementation 字段对齐（文件名里的
# mpi_ms/mpi_island 是命名约定，不应出现在给用户看的分组标签里）。
IMPL_LABELS = {"mpi_ms": "mpi-data-parallel", "mpi_island": "mpi-island"}


def group_label(key: tuple) -> str:
    (suite, impl, fn, particles, dim, ranks, threads,
     boundary, dynamics, migration) = key
    impl = IMPL_LABELS.get(impl, impl)
    return (f"{suite}/{impl}/{fn}/p{particles}d{dim}"
            f"/r{ranks}t{threads}/{boundary}/{dynamics}/mi{migration}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--targets", default="300,250,200")
    parser.add_argument("--checkpoints", default="10000,25000,50000,100000")
    args = parser.parse_args(argv)

    try:
        targets = [float(t) for t in args.targets.split(",")]
        if any(not math.isfinite(t) or t < 0 for t in targets):
            raise ValueError
    except ValueError:
        parser.error("--targets 必须是逗号分隔的非空有限非负数列表")
    try:
        checkpoints = [int(c) for c in args.checkpoints.split(",")]
        if any(c <= 0 for c in checkpoints):
            raise ValueError
    except ValueError:
        parser.error("--checkpoints 必须是逗号分隔的非空正整数列表")
    history_by_log = _history_column(args.manifest_root)

    groups: dict[tuple, list[Trajectory]] = defaultdict(list)
    unmatched: list[str] = []
    for log, seeds in history_by_log.items():
        match = LOG_NAME.fullmatch(log.name)
        if match is None:
            unmatched.append(str(log))
            continue
        key = (match.group("suite"), match.group("impl"), match.group("fn"),
               int(match.group("p")), int(match.group("d")),
               int(match.group("r")), int(match.group("t")),
               match.group("b"), match.group("dyn"), int(match.group("mi")))
        groups[key].extend(_validated_trajectories(
            log, seeds, fixed_budget=key[0] in ("baseline", "strong")))
    if unmatched:
        # 命名漂移时静默跳过会让分组悄悄丢数据，比直接失败更危险。
        raise SystemExit(
            "以下日志不符合正式矩阵命名约定，拒绝静默跳过：\n  "
            + "\n  ".join(sorted(unmatched)))
    # 固定预算 ERT 还要求组内全部 run 的实际预算一致。
    for key in sorted(groups):
        if key[0] not in ("baseline", "strong"):
            continue
        budgets = {traj[-1][1] for traj in groups[key]}
        if len(budgets) != 1:
            raise SystemExit(
                f"组 {group_label(key)} 的结束评估数不一致 {sorted(budgets)}；"
                "ERT 要求组内全部 run 使用同一固定预算")

    # 全部校验通过后才输出，避免坏数据留下半张表。
    # 列宽取最长组标签，避免长函数名/多位迁移间隔把表格挤错位。
    label_width = max([72] + [len(group_label(key)) for key in groups])

    print("== median best-so-far at eval checkpoints (suite/impl/fn/config) ==")
    header = f"{'group':<{label_width}} " + " ".join(f"{c:>9}" for c in checkpoints)
    print(header)
    for key in sorted(groups):
        trajs = groups[key]
        medians = []
        for checkpoint in checkpoints:
            values = [best_at_evals(traj, [checkpoint])[checkpoint]
                      for traj in trajs]
            medians.append(statistics.median(values))
        print(f"{group_label(key):<{label_width}} "
              + " ".join(f"{m:>9.4g}" for m in medians))

    print()
    print("== fixed-budget ERT (per-group budget from final trajectory row) ==")
    print(f"{'group':<{label_width}} " + " ".join(
        f"T={t:g}:S/ERT" for t in targets))
    for key in sorted(groups):
        if key[0] not in ("baseline", "strong"):
            continue
        trajs = groups[key]
        evals_budget = trajs[0][-1][1]  # 已整组校验一致
        cells = []
        for target in targets:
            summary = success_summary(trajs, target, evals_budget)
            ert = summary["ert"]
            ert_text = "na" if ert is None else f"{ert:.3g}"
            cells.append(f"{summary['n_success']}/{ert_text}")
        print(f"{group_label(key):<{label_width}} "
              + " ".join(f"{c:>12}" for c in cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
