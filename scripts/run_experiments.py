#!/usr/bin/env python3
"""Generate fixed-budget PSO matrices and optionally execute them.

Baseline records one serial/OpenMP reference per function, strong scaling covers
both MPI implementations, and weak scaling treats particles and max evaluations
as per-rank quantities that grow with the rank count. Generated solver commands
always disable early stopping so every run consumes its planned evaluation budget.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import time
from typing import Iterable, Sequence


FUNCTIONS = ("sphere", "rosenbrock", "rastrigin", "griewank", "ackley")
BOUNDARIES = ("reflect", "clamp", "reinit")
DYNAMICS = ("constriction", "linear-inertia")
IMPLEMENTATIONS = ("serial", "mpi-ms", "mpi-island")
INHERITED_ENVIRONMENT_BLOCKLIST = (
    "OMP_THREAD_LIMIT",
    "OMP_PLACES",
    "GOMP_CPU_AFFINITY",
    "OMP_WAIT_POLICY",
    "OMP_DISPLAY_ENV",
    "OMP_DISPLAY_AFFINITY",
    "MSMPI_PROGRESS_SPIN_LIMIT",
)


@dataclasses.dataclass(frozen=True)
class MatrixConfig:
    functions: tuple[str, ...]
    particles: int
    particles_per_rank: int
    dim: int
    max_evals: int
    ranks: tuple[int, ...]
    threads: tuple[int, ...]
    boundaries: tuple[str, ...]
    dynamics: tuple[str, ...]
    migration_intervals: tuple[int, ...]
    mode: str


@dataclasses.dataclass(frozen=True)
class Executables:
    serial: Path
    mpi_ms: Path
    mpi_island: Path
    mpiexec: str


@dataclasses.dataclass(frozen=True)
class Experiment:
    scaling: str
    implementation: str
    function: str
    particles: int
    dim: int
    max_evals: int
    ranks: int
    threads: int
    boundary: str
    dynamics: str
    migration_interval: int


def parse_csv(text: str, cast=str) -> tuple:
    """Parse a non-empty, comma-separated option and reject duplicates."""
    try:
        values = tuple(cast(item.strip()) for item in text.split(",") if item.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    if not values:
        raise argparse.ArgumentTypeError("列表不能为空")
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("列表不能包含重复项")
    return values


def _validate_choices(name: str, values: Iterable[str], allowed: Sequence[str]) -> None:
    invalid = sorted(set(values) - set(allowed))
    if invalid:
        raise ValueError(f"{name} 包含无效值: {', '.join(invalid)}")


def _validate_positive_integer(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} 必须为正的 Python 整数")


def _base_experiments(config: MatrixConfig, scaling: str) -> list[Experiment]:
    """Build non-ablation protocols without overlapping implementations.

    Baseline contains one serial/OpenMP reference per function at the first
    requested thread count. Strong contains both MPI implementations over the
    complete rank/thread matrix. Weak has the same MPI matrix, with particles
    and max evaluations interpreted per rank and scaled by the rank count.
    """
    boundary = config.boundaries[0]
    dynamics = config.dynamics[0]
    migration = config.migration_intervals[0]
    output: list[Experiment] = []
    for function in config.functions:
        if scaling == "baseline":
            output.append(Experiment(
                scaling, "serial", function, config.particles, config.dim,
                config.max_evals, 1, config.threads[0], boundary, dynamics, 0))
            continue
        for threads in config.threads:
            for ranks in config.ranks:
                weak = scaling == "weak"
                particles = config.particles_per_rank * ranks if weak else config.particles
                max_evals = config.max_evals * ranks if weak else config.max_evals
                for implementation in ("mpi-ms", "mpi-island"):
                    output.append(Experiment(
                        scaling, implementation, function, particles, config.dim,
                        max_evals, ranks, threads, boundary, dynamics,
                        migration if implementation == "mpi-island" else 0))
    return output


def build_experiment_matrix(config: MatrixConfig) -> list[Experiment]:
    """Build a deterministic matrix; ``all`` is the union of all protocols."""
    for name in ("particles", "particles_per_rank", "dim", "max_evals"):
        _validate_positive_integer(name, getattr(config, name))
    for name in ("ranks", "threads", "migration_intervals"):
        for value in getattr(config, name):
            _validate_positive_integer(name, value)
    _validate_choices("functions", config.functions, FUNCTIONS)
    _validate_choices("boundaries", config.boundaries, BOUNDARIES)
    _validate_choices("dynamics", config.dynamics, DYNAMICS)
    if config.mode not in ("baseline", "strong", "weak", "ablation", "all"):
        raise ValueError(f"无效 mode: {config.mode}")

    modes = ("baseline", "strong", "weak", "ablation") if config.mode == "all" else (config.mode,)
    experiments: list[Experiment] = []
    for mode in modes:
        if mode != "ablation":
            experiments.extend(_base_experiments(config, mode))
            continue
        for function in config.functions:
            for ranks in config.ranks:
                for threads in config.threads:
                    for boundary in config.boundaries:
                        for dynamics in config.dynamics:
                            for migration in config.migration_intervals:
                                experiments.append(Experiment(
                                    "ablation", "mpi-island", function,
                                    config.particles, config.dim, config.max_evals,
                                    ranks, threads, boundary, dynamics, migration))

    unique: list[Experiment] = []
    seen: set[Experiment] = set()
    for experiment in experiments:
        if experiment not in seen:
            seen.add(experiment)
            unique.append(experiment)
    for experiment in unique:
        if experiment.particles < experiment.ranks:
            raise ValueError(
                f"粒子数 {experiment.particles} 小于 rank 数 {experiment.ranks}")
        if experiment.max_evals < experiment.particles:
            raise ValueError(
                f"max-evals {experiment.max_evals} 小于粒子数 {experiment.particles}")
    return unique


def executable_path(build_dir: Path, stem: str, windows: bool | None = None) -> Path:
    if windows is None:
        windows = os.name == "nt"
    return build_dir / (stem + (".exe" if windows else ""))


def discover_mpiexec(explicit: str | None = None, windows: bool | None = None) -> str:
    if explicit:
        return explicit
    configured = os.environ.get("MPIEXEC")
    if configured:
        return configured
    if windows is None:
        windows = os.name == "nt"
    candidates = ("mpiexec.exe", "mpiexec") if windows else ("mpiexec",)
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    if windows:
        program_files = os.environ.get("ProgramFiles", "C:/Program Files")
        ms_mpi = Path(program_files) / "Microsoft MPI" / "Bin" / "mpiexec.exe"
        if ms_mpi.is_file():
            return str(ms_mpi)
    return candidates[0]


def experiment_command(experiment: Experiment, seed: int,
                       executables: Executables,
                       history_path: str | None = None,
                       pin: bool = False) -> list[str]:
    executable = {
        "serial": executables.serial,
        "mpi-ms": executables.mpi_ms,
        "mpi-island": executables.mpi_island,
    }[experiment.implementation]
    command: list[str] = []
    if experiment.implementation != "serial":
        command.extend((executables.mpiexec, "-n", str(experiment.ranks)))
    command.extend((
        str(executable),
        "--function", experiment.function,
        "--particles", str(experiment.particles),
        "--dim", str(experiment.dim),
        "--max-evals", str(experiment.max_evals),
        "--seed", str(seed),
        "--boundary", experiment.boundary,
        "--no-early-stop",
    ))
    if history_path is not None:
        command.extend(("--history", history_path))
    if pin:
        command.append("--pin-cpus")
    if experiment.dynamics == "linear-inertia":
        command.append("--linear-inertia")
    if experiment.implementation == "mpi-island":
        command.extend(("--migration-interval", str(experiment.migration_interval)))
    return command


def command_environment(experiment: Experiment) -> dict[str, str]:
    return {
        "OMP_NUM_THREADS": str(experiment.threads),
        "OMP_DYNAMIC": "FALSE",
        "OMP_PROC_BIND": "FALSE",
        "MPIEXEC_AFFINITY": "0",
    }


def isolated_process_environment(
        experiment: Experiment,
        inherited: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if inherited is None else inherited)
    for name in INHERITED_ENVIRONMENT_BLOCKLIST:
        environment.pop(name, None)
    environment.update(command_environment(experiment))
    return environment


def log_filename(experiment: Experiment) -> str:
    implementation = experiment.implementation.replace("-", "_")
    dynamics = experiment.dynamics.replace("-", "_")
    return (
        f"{experiment.scaling}__{implementation}__fn-{experiment.function}"
        f"__p-{experiment.particles}__d-{experiment.dim}"
        f"__r-{experiment.ranks}__t-{experiment.threads}"
        f"__b-{experiment.boundary}__dyn-{dynamics}"
        f"__mi-{experiment.migration_interval}.log"
    )


def existing_nonempty_logs(output_dir: Path,
                           experiments: Iterable[Experiment]) -> list[Path]:
    """Return planned log paths that already contain data."""
    paths = {output_dir / log_filename(experiment) for experiment in experiments}
    return sorted(path for path in paths if path.is_file() and path.stat().st_size > 0)


def existing_nonempty_histories(output_dir: Path,
                                experiments: Iterable[Experiment],
                                runs: int, seed_base: int) -> list[Path]:
    """Return planned history sidecar paths that already contain data."""
    paths = {Path(f"{output_dir / log_filename(experiment)}"
                  f".seed-{seed_base + run}.history")
             for experiment in experiments for run in range(runs)}
    return sorted(path for path in paths
                  if path.is_file() and path.stat().st_size > 0)


def format_command(command: Sequence[str], windows: bool | None = None) -> str:
    if windows is None:
        windows = os.name == "nt"
    return subprocess.list2cmdline(command) if windows else shlex.join(command)


def _local_executable_metadata(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    metadata: dict[str, object] = {
        "path": str(resolved),
        "exists": resolved.is_file(),
    }
    if not metadata["exists"]:
        return metadata
    stat = resolved.stat()
    digest = hashlib.sha256()
    with resolved.open("rb") as executable:
        for chunk in iter(lambda: executable.read(1024 * 1024), b""):
            digest.update(chunk)
    metadata.update(
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256=digest.hexdigest(),
    )
    return metadata


def executables_metadata(executables: Executables) -> dict[str, dict[str, object]]:
    mpiexec_found = shutil.which(executables.mpiexec)
    if mpiexec_found:
        mpiexec_path = Path(mpiexec_found).resolve()
        mpiexec_resolved = True
    else:
        mpiexec_path = Path(executables.mpiexec).expanduser().resolve()
        mpiexec_resolved = Path(executables.mpiexec).expanduser().is_absolute()
    mpiexec_metadata: dict[str, object] = {
        "path": str(mpiexec_path),
        "resolved": mpiexec_resolved,
    }
    if mpiexec_resolved:
        try:
            version = subprocess.run(
                [str(mpiexec_path), "--version"],
                capture_output=True, text=True, timeout=30, check=False,
                env=dict(os.environ))
            version_text = (version.stdout or "").strip()[:2000]
            if version.returncode == 0 and version_text:
                mpiexec_metadata["version"] = version_text
            else:
                mpiexec_metadata["version"] = None
                mpiexec_metadata["version_returncode"] = version.returncode
        except (OSError, subprocess.SubprocessError) as error:
            mpiexec_metadata["version"] = None
            mpiexec_metadata["version_error"] = str(error)
    return {
        "serial": _local_executable_metadata(executables.serial),
        "mpi_ms": _local_executable_metadata(executables.mpi_ms),
        "mpi_island": _local_executable_metadata(executables.mpi_island),
        "mpiexec": mpiexec_metadata,
    }


def write_manifest_atomic(path: Path, manifest: dict, attempts: int = 5) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    # Windows 上杀毒/索引服务可能瞬时锁住目标文件，让 os.replace 返回
    # WinError 5；短退避重试，最后一次仍失败才向上抛出。
    for attempt in range(attempts):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=_positive, default=30)
    parser.add_argument("--functions", default="rastrigin")
    parser.add_argument(
        "--particles", type=_positive, default=2000,
        help="baseline/strong/ablation 的总粒子数")
    parser.add_argument(
        "--particles-per-rank", type=_positive, default=2000,
        help="weak 的每-rank粒子数；总粒子数随 rank 数线性扩展")
    parser.add_argument("--dim", type=_positive, default=50)
    parser.add_argument(
        "--max-evals", type=_positive, default=100000,
        help="weak 的每-rank评估预算；其他模式的总评估预算")
    parser.add_argument("--seed-base", type=int, default=20240229)
    parser.add_argument("--ranks", default="1,2,4")
    parser.add_argument("--threads", default="1")
    parser.add_argument(
        "--mode", choices=("baseline", "strong", "weak", "ablation", "all"),
        default="baseline",
        help=("baseline=每函数一份单进程 OpenMP 参考（首个 thread）；"
              "strong=两种 MPI 的全部 rank/thread；weak=两种 MPI 的全部 "
              "rank/thread，粒子数和评估预算均按 rank 线性扩展；"
              "ablation=岛模型消融"))
    parser.add_argument("--boundaries", default="reflect")
    parser.add_argument("--dynamics", default="constriction")
    parser.add_argument("--migration-intervals", "--migration-interval",
                        dest="migration_intervals", default="10")
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--mpiexec", default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true",
                        help="实际运行；省略时仅生成 dry-run manifest")
    parser.add_argument("--history", action="store_true",
                        help="为每个 run 记录 best-so-far 收敛轨迹 sidecar"
                             "（<log>.seed-<seed>.history）")
    parser.add_argument("--pin", action="store_true",
                        help="把每个求解进程绑定到固定逻辑 CPU 块"
                             "（--pin-cpus），正式采集建议开启")
    parser.add_argument("--append", action="store_true",
                        help="执行时允许追加到已有的非空日志")
    return parser


def _manifest_config(args: argparse.Namespace, config: MatrixConfig) -> dict:
    values = dataclasses.asdict(config)
    values.update({
        "runs": args.runs,
        "seed_base": args.seed_base,
        "build_dir": str(args.build_dir.resolve()),
        "output_dir": str(args.output_dir.resolve()),
        "append": args.append,
        "pin": args.pin,
    })
    return values


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.seed_base < 0:
        parser.error("--seed-base 不能为负数")
    if args.history and args.append:
        # 求解器以截断方式（"w"）写 sidecar：追加 pass 会保留旧日志行却整体
        # 覆盖同名 sidecar，日志与收敛轨迹的逐 seed 配对随之被破坏。
        parser.error(
            "--append 与 --history 不能同时使用：追加会在旧日志行之后继续"
            "记录，而 sidecar 会被新一次 pass 整体覆盖，日志与轨迹的配对"
            "随之失效；请改用新的 --output-dir")
    try:
        config = MatrixConfig(
            functions=parse_csv(args.functions),
            particles=args.particles,
            particles_per_rank=args.particles_per_rank,
            dim=args.dim,
            max_evals=args.max_evals,
            ranks=parse_csv(args.ranks, _positive),
            threads=parse_csv(args.threads, _positive),
            boundaries=parse_csv(args.boundaries),
            dynamics=parse_csv(args.dynamics),
            migration_intervals=parse_csv(args.migration_intervals, _positive),
            mode=args.mode,
        )
        experiments = build_experiment_matrix(config)
    except (ValueError, argparse.ArgumentTypeError) as error:
        parser.error(str(error))

    build_dir = args.build_dir.resolve()
    executables = Executables(
        executable_path(build_dir, "pso_serial"),
        executable_path(build_dir, "pso_mpi_ms"),
        executable_path(build_dir, "pso_mpi_island"),
        discover_mpiexec(args.mpiexec),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    conflicting_logs = existing_nonempty_logs(args.output_dir, experiments)
    if args.execute and not args.append and conflicting_logs:
        preview = "\n  ".join(str(path) for path in conflicting_logs[:10])
        suffix = (f"\n  ... 另有 {len(conflicting_logs) - 10} 个"
                  if len(conflicting_logs) > 10 else "")
        parser.error(
            "以下计划日志已存在且非空；为避免样本污染，请使用新的 "
            "--output-dir，或显式使用 --append 允许追加："
            f"\n  {preview}{suffix}")
    conflicting_histories = (
        existing_nonempty_histories(args.output_dir, experiments,
                                    args.runs, args.seed_base)
        if args.history else [])
    if args.execute and conflicting_histories:
        preview = "\n  ".join(str(path) for path in conflicting_histories[:10])
        suffix = (f"\n  ... 另有 {len(conflicting_histories) - 10} 个"
                  if len(conflicting_histories) > 10 else "")
        parser.error(
            "以下计划 history sidecar 已存在且非空；执行会覆盖已有的收敛"
            "轨迹证据，请使用新的 --output-dir："
            f"\n  {preview}{suffix}")
    manifest_path = args.output_dir / "manifest.json"
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            parser.error(
                "既有 manifest 存在但无法解析，拒绝覆盖以避免丢失未知状态: "
                f"{manifest_path} ({error})")
        if not isinstance(previous, dict):
            parser.error(f"既有 manifest 不是 JSON 对象，拒绝覆盖: {manifest_path}")
        # 只有"纯计划"manifest 允许被替换：dry-run→dry-run 换计划、
        # dry-run→--execute 升级为真实执行（docs/03 的正式流程）。
        # 已记录真实执行的 manifest 覆盖会丢失溯源证据（状态/返回码/
        # 二进制元数据），而默认 dry-run 恰恰是最容易被误跑的路径；
        # v3 的 --execute --append 追加重跑流程显式豁免。
        if (previous.get("dry_run") is not True
                and not (args.execute and args.append)):
            parser.error(
                "既有 manifest 记录真实执行；覆盖会丢失溯源证据。请使用新的 "
                "--output-dir，或显式 --execute --append 走已登记的追加重跑"
                f"流程: {manifest_path}")
    manifest = {
        "schema": "pso_experiments_v1",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dry_run": not args.execute,
        "log_policy": "append" if args.append else "fail-if-nonempty",
        "host": {
            "platform": platform.platform(),
            "python": sys.version,
        },
        "configuration": _manifest_config(args, config),
        "executables": executables_metadata(executables),
        "commands": [],
    }

    for experiment in experiments:
        for run in range(args.runs):
            seed = args.seed_base + run
            log_path = args.output_dir / log_filename(experiment)
            history_path = None
            if args.history:
                history_path = f"{log_path.resolve()}.seed-{seed}.history"
            command = experiment_command(experiment, seed, executables,
                                         history_path, pin=args.pin)
            environment = command_environment(experiment)
            manifest["commands"].append({
                "run": run,
                "seed": seed,
                "experiment": dataclasses.asdict(experiment),
                "command": command,
                "command_text": format_command(command),
                "environment": environment,
                "log": str(log_path.resolve()),
                "history": history_path,
                "status": "planned",
            })

    write_manifest_atomic(manifest_path, manifest)
    if args.execute:
        for record in manifest["commands"]:
            record["status"] = "running"
            write_manifest_atomic(manifest_path, manifest)
            process_environment = isolated_process_environment(
                Experiment(**record["experiment"]))
            try:
                with Path(record["log"]).open("a", encoding="utf-8") as log:
                    log.write(f"# command: {record['command_text']}\n")
                    log.flush()
                    completed = subprocess.run(
                        record["command"], stdout=log, stderr=subprocess.STDOUT,
                        env=process_environment, check=False)
            except OSError as error:
                record.update(status="failed", error=str(error))
                write_manifest_atomic(manifest_path, manifest)
                print(f"命令启动失败: {record['command_text']}\n{error}", file=sys.stderr)
                return 1
            record["returncode"] = completed.returncode
            record["status"] = (
                "completed" if completed.returncode == 0 else "failed")
            write_manifest_atomic(manifest_path, manifest)
            if completed.returncode != 0:
                print(
                    f"命令失败（退出码 {completed.returncode}）: "
                    f"{record['command_text']}", file=sys.stderr)
                return completed.returncode or 1
    action = "实验完成" if args.execute else "dry-run 完成"
    print(f"{action}: {len(manifest['commands'])} 条命令；manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
