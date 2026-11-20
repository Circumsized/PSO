#!/usr/bin/env python3
"""Parse PSO benchmark logs and optionally plot timing/quality summaries.

The executable output is intentionally line-oriented. This parser accepts both
pso_v1 result records and compact smoke-test lines emitted by older builds.
It uses only the Python standard library unless matplotlib is installed.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Sequence

KEY_VALUE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
CONFIG_FIELDS = (
    "implementation", "function", "particles", "dim", "max_evals", "max_iters",
    "boundary", "dynamics", "target", "lower", "upper", "chi", "c1", "c2",
    "vmax_ratio", "inertia_start", "inertia_end", "ranks", "threads",
    "migration_interval", "stop_on_target", "timing_scope",
)


def parse_log(path: Path, implementation: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        values = dict(KEY_VALUE.findall(line))
        if "best" not in values or values.get("record") == "progress" or line.startswith("progress "):
            continue
        if "record" in values and values.get("record") != "result":
            continue
        row = dict(values)
        row["implementation"] = values.get("implementation", implementation)
        if "elapsed_max_s" in values:
            row["time"] = values["elapsed_max_s"]
        if "success" not in row and "target" in row:
            row["success"] = "1" if float(row["best"]) <= float(row["target"]) else "0"
        if "fes_to_target" not in row and row.get("success") == "1" and "evals" in row:
            row["fes_to_target"] = row["evals"]
        rows.append(row)
    return rows


def _finite_float(value: str, field: str, path: Path, line_number: int) -> None:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(
            f"{path}:{line_number}: {field} is not a float: {value!r}") from error
    if not math.isfinite(parsed):
        raise ValueError(
            f"{path}:{line_number}: {field} must be finite: {value!r}")


def parse_log_strict(path: Path, expected_results: int, *,
                     expected_commands: dict[int, str] | None = None) -> list[dict[str, str]]:
    """解析完整结果日志，可按 seed 核验实际执行命令。"""
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"log is empty: {path}")

    command_count = 0
    pending_command: str | None = None
    seen_seeds: set[int] = set()
    rows: list[dict[str, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("# command:"):
            if pending_command is not None:
                raise ValueError(f"{path}:{line_number}: command/result 缺少结果")
            command_count += 1
            pending_command = stripped[len("# command:"):].strip()
            continue
        values = dict(KEY_VALUE.findall(line))
        if values.get("record") == "progress" or stripped.startswith("progress "):
            raise ValueError(f"{path}:{line_number}: progress record is not allowed")
        if values.get("record") != "result":
            continue
        if values.get("schema") != "pso_v1":
            raise ValueError(f"{path}:{line_number}: result schema must be pso_v1")
        if pending_command is None:
            raise ValueError(f"{path}:{line_number}: command/result 顺序不匹配")
        if expected_commands is not None:
            raw_seed = values.get("seed", "")
            if not raw_seed.isascii() or not raw_seed.isdigit():
                raise ValueError(f"{path}:{line_number}: seed 无效")
            if pending_command != expected_commands.get(int(raw_seed)):
                raise ValueError(f"{path}:{line_number}: command/result seed 或命令不匹配")
        pending_command = None
        if "best" not in values:
            raise ValueError(f"{path}:{line_number}: result is missing best")
        # --append 复跑会在同一日志留下重复 seed；analyze_stats 的 loader
        # 早已硬失败，这里必须同口径，否则 plot 会静默收进两轮数据。
        raw_seed = values.get("seed", "")
        if raw_seed.isascii() and raw_seed.isdigit():
            seed_value = int(raw_seed)
            if seed_value in seen_seeds:
                raise ValueError(
                    f"{path}:{line_number}: duplicate seeds in log"
                    f"（seed {seed_value} 重复出现）")
            seen_seeds.add(seed_value)
        if "elapsed_max_s" not in values and "time" not in values:
            raise ValueError(
                f"{path}:{line_number}: result is missing elapsed_max_s/time")
        for field in ("best", "elapsed_max_s", "time", "fes_to_target"):
            if field in values:
                _finite_float(values[field], field, path, line_number)
        row = dict(values)
        if "elapsed_max_s" in row:
            row["time"] = row["elapsed_max_s"]
        if "success" not in row and "target" in row:
            _finite_float(row["target"], "target", path, line_number)
            row["success"] = (
                "1" if float(row["best"]) <= float(row["target"]) else "0")
        if "fes_to_target" not in row and row.get("success") == "1" and "evals" in row:
            _finite_float(row["evals"], "evals", path, line_number)
            row["fes_to_target"] = row["evals"]
        rows.append(row)

    if pending_command is not None:
        raise ValueError(f"{path}: command/result 缺少结果")
    if command_count != len(rows):
        raise ValueError(
            f"{path}: command/result count mismatch "
            f"({command_count} commands, {len(rows)} results)")
    if len(rows) != expected_results:
        raise ValueError(
            f"{path}: manifest/result count mismatch "
            f"({expected_results} manifest commands, {len(rows)} results)")
    return rows


_MAX_EVALS_OPTION = re.compile(r"(?:^|\s)--max-evals(?:=|\s+)(\d+)")


def _validate_pinned_fields(
        log_path: Path, rows: list[dict[str, str]],
        expected_commands: dict[int, str]) -> list[dict[str, str]]:
    """钉住 pso_v1 字段集与预算一致性（仅 --manifest-root 严格路径）。

    parse_log_strict 是三个消费者的共享核心，这里只加 plot 自身的契约：
    缺 CONFIG_FIELDS 任一字段会在 summary 里静默产生空单元格并独立成组；
    max_evals/evals 与绑定命令 --max-evals 矛盾曾同样被静默收下。
    summarize_convergence 的身份/轨迹对账更严格，不经过本函数。"""
    for index, row in enumerate(rows, start=1):
        label = f"{log_path}: result {index} (seed {row.get('seed', '?')})"
        missing = [field for field in CONFIG_FIELDS if not row.get(field)]
        if missing:
            raise ValueError(
                f"{label}: 结果缺少 pso_v1 钉住字段: {' '.join(missing)}")
        match = _MAX_EVALS_OPTION.search(
            expected_commands.get(int(row["seed"]), ""))
        if match is None or int(match.group(1)) <= 0:
            continue
        budget = int(match.group(1))
        if row["max_evals"] != str(budget):
            raise ValueError(
                f"{label}: 结果 max_evals={row['max_evals']!r} "
                f"与绑定命令 --max-evals {budget} 不一致")
        particles_text = row["particles"]
        evals_text = row.get("evals", "")
        if not particles_text.isdigit() or not evals_text.isdigit():
            raise ValueError(
                f"{label}: particles/evals 必须是整数: "
                f"{particles_text!r}/{evals_text!r}")
        particles = int(particles_text)
        if particles <= 0:
            raise ValueError(f"{label}: particles 必须为正: {particles}")
        evals = int(evals_text)
        full_budget = budget // particles * particles
        if row.get("stop_on_target") == "0":
            if evals != full_budget:
                raise ValueError(
                    f"{label}: 固定预算应结束于 evals={full_budget}，实际 {evals}")
        elif evals > full_budget:
            raise ValueError(
                f"{label}: evals={evals} 超出绑定预算 {full_budget}")
    return rows


def load_manifest_rows(root: Path) -> list[dict[str, str]]:
    """Load only complete manifests and logs contained by ``root``."""
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise ValueError(f"manifest root is not a directory: {root}")
    manifests = sorted(resolved_root.rglob("manifest.json"))
    if not manifests:
        raise ValueError(f"no manifest.json files found under {root}")

    rows: list[dict[str, str]] = []
    loaded_logs: set[Path] = set()
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid manifest {manifest_path}: {error}") from error
        if not isinstance(manifest, dict):
            raise ValueError(f"manifest must be a JSON object: {manifest_path}")
        if manifest.get("schema") != "pso_experiments_v1":
            raise ValueError(f"unsupported schema in {manifest_path}")
        if manifest.get("dry_run") is not False:
            raise ValueError(f"dry-run manifest is not complete: {manifest_path}")
        commands = manifest.get("commands")
        if not isinstance(commands, list) or not commands:
            raise ValueError(f"manifest commands must be a non-empty list: {manifest_path}")

        log_paths: list[Path] = []
        expected_commands_by_log: dict[Path, dict[int, str]] = {}
        for index, command in enumerate(commands):
            label = f"{manifest_path}: command {index}"
            if not isinstance(command, dict):
                raise ValueError(f"{label} must be an object")
            if command.get("status") != "completed":
                raise ValueError(f"{label} is not completed")
            returncode = command.get("returncode")
            if isinstance(returncode, bool) or returncode != 0:
                raise ValueError(f"{label} has non-zero or missing returncode")
            seed = command.get("seed")
            if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
                raise ValueError(f"{label} 缺少合法非负整数 seed（防篡改核验需要）")
            command_text = command.get("command_text")
            if not isinstance(command_text, str) or not command_text:
                raise ValueError(f"{label} 缺少 command_text（防篡改核验需要）")
            log_value = command.get("log")
            if not isinstance(log_value, str) or not log_value:
                raise ValueError(f"{label} has no log path")
            log_path = Path(log_value)
            if not log_path.is_absolute():
                raise ValueError(f"{label} log path must be absolute: {log_path}")
            resolved_log = log_path.resolve()
            if not resolved_log.is_relative_to(resolved_root):
                raise ValueError(f"{label} log is outside manifest root: {resolved_log}")
            if not resolved_log.is_file() or resolved_log.stat().st_size == 0:
                raise ValueError(f"{label} log is missing or empty: {resolved_log}")
            expected = expected_commands_by_log.setdefault(resolved_log, {})
            if seed in expected:
                raise ValueError(f"{label}: 日志 {resolved_log} 的 seed {seed} 重复")
            expected[seed] = command_text
            log_paths.append(resolved_log)

        expected_by_log = Counter(log_paths)
        for log_path, expected_results in expected_by_log.items():
            if log_path in loaded_logs:
                raise ValueError(
                    f"log is referenced by multiple manifests: {log_path}")
            loaded_logs.add(log_path)
            # seed→命令文本核验：日志里的 # command: 行必须与 manifest 记录
            # 一致，防止篡改命令行（含 --seed）后数据被静默接受。
            parsed = parse_log_strict(
                log_path, expected_results,
                expected_commands=expected_commands_by_log[log_path])
            rows.extend(_validate_pinned_fields(
                log_path, parsed, expected_commands_by_log[log_path]))
    return rows


def quartiles(values: list[float]) -> tuple[float, float]:
    if len(values) == 1:
        return values[0], values[0]
    return tuple(statistics.quantiles(values, n=4, method="inclusive")[::2])  # type: ignore[return-value]


def metric_stats(values: list[float], prefix: str, precision: int) -> dict[str, str]:
    if not values:
        return {f"{prefix}_{name}": "" for name in ("mean", "stdev", "median", "q1", "q3")}
    q1, q3 = quartiles(values)
    fmt = f".{{precision}}g"
    render = lambda value: format(value, fmt.format(precision=precision))
    return {
        f"{prefix}_mean": render(statistics.fmean(values)),
        f"{prefix}_stdev": render(statistics.stdev(values)) if len(values) > 1 else "0.0",
        f"{prefix}_median": render(statistics.median(values)),
        f"{prefix}_q1": render(q1),
        f"{prefix}_q3": render(q3),
    }


def configuration_key(row: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple((field, row.get(field, "")) for field in CONFIG_FIELDS)


def summary(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for key in sorted({configuration_key(row) for row in rows}):
        group = [row for row in rows if configuration_key(row) == key]
        config = dict(key)
        best = [float(row["best"]) for row in group]
        times = [float(row["time"]) for row in group if "time" in row]
        successes = [int(row["success"]) for row in group if "success" in row]
        fes = [float(row["fes_to_target"]) for row in group if "fes_to_target" in row]
        record = {field: config[field] for field in CONFIG_FIELDS}
        record["runs"] = str(len(group))
        record.update(metric_stats(best, "best", 17))
        record.update(metric_stats(times, "time", 9))
        record["success_rate"] = (f"{statistics.fmean(successes):.9g}" if successes else "")
        record.update(metric_stats(fes, "fes_to_target", 17))
        output.append(record)
    return output


def maybe_plot(rows: list[dict[str, str]], output: Path) -> bool:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError:
        return False
    labels = sorted({row["implementation"] for row in rows})
    means = []
    for label in labels:
        values = [float(row["time"]) for row in rows
                  if row["implementation"] == label and "time" in row]
        means.append(statistics.fmean(values) if values else 0.0)
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.bar(labels, means, color=["#4472c4", "#70ad47", "#ed7d31"][:len(labels)])
    axis.set_ylabel("Mean wall time (s)")
    axis.set_title("PSO implementation timing")
    axis.tick_params(axis="x", rotation=15)
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--input", type=Path, default=None,
                        help="directory containing legacy raw_*.log files")
    inputs.add_argument(
        "--manifest-root", type=Path, default=None,
        help="recursively load completed manifests and strictly validate logs")
    parser.add_argument("--output", type=Path, default=None,
                        help="summary CSV path (default: selected input/summary.csv)")
    parser.add_argument("--plot", type=Path, default=None,
                        help="optional PNG output; requires matplotlib")
    args = parser.parse_args(argv)

    selected_root = args.manifest_root or args.input or Path("results")
    if args.manifest_root is not None:
        try:
            rows = load_manifest_rows(args.manifest_root)
        except (OSError, ValueError) as error:
            parser.error(str(error))
    else:
        files = {
            "serial": selected_root / "raw_serial.log",
            "mpi-data-parallel": selected_root / "raw_mpi_ms.log",
            "mpi-island": selected_root / "raw_mpi_island.log",
        }
        rows = []
        for implementation, path in files.items():
            if path.exists():
                rows.extend(parse_log(path, implementation))
    if not rows:
        parser.error(f"no benchmark rows found under {selected_root}")

    destination = args.output or selected_root / "summary.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    records = summary(rows)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(f"wrote {destination}")
    if args.plot is not None:
        print("wrote " + str(args.plot) if maybe_plot(rows, args.plot)
              else "matplotlib unavailable; skipped plot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
