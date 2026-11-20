"""Unit tests for analyze_speedups (standard library unittest only)."""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze_stats  # noqa: E402
import analyze_speedups  # noqa: E402
import run_experiments as runner  # noqa: E402


BASE_CONFIG = {
    "mode": "all",
    "functions": ["sphere"],
    "particles": 100,
    "dim": 8,
    "max_evals": 1000,
    "ranks": [1, 2],
    "threads": [1],
    "boundaries": ["reflect"],
    "dynamics": ["constriction"],
    "migration_intervals": [5],
}

# (scaling, implementation, ranks, threads, migration) → 每 seed 的耗时。
# baseline 每 run 1.0s；dp r2 每 run 0.5s → 中位比 x2.00；island r1 0.8s →
# x1.25；island r2 0.4s → x2.50（与旧夹具的断言值一一对应）。
TIMES = {
    ("baseline", "serial", 1, 1, 0): {1: 1.0, 2: 1.0, 3: 1.0},
    ("strong", "mpi-ms", 1, 1, 0): {1: 1.0, 2: 1.0, 3: 1.0},
    ("strong", "mpi-ms", 2, 1, 0): {1: 0.5, 2: 0.5, 3: 0.5},
    ("strong", "mpi-island", 1, 1, 5): {1: 0.8, 2: 0.8, 3: 0.8},
    ("strong", "mpi-island", 2, 1, 5): {1: 0.4, 2: 0.4, 3: 0.4},
}


def _experiment(scaling: str, implementation: str, ranks: int,
                threads: int, migration: int) -> runner.Experiment:
    return runner.Experiment(
        scaling=scaling, implementation=implementation, function="sphere",
        particles=100, dim=8, max_evals=1000, ranks=ranks, threads=threads,
        boundary="reflect", dynamics="constriction",
        migration_interval=migration)


def write_fixture(root: Path) -> None:
    """写一份自洽的 manifest + 日志：commands 与日志 # command: 行逐字一致。

    manifest 与日志互证是 R4 修复的核心契约，夹具必须两份都真写出来
    （旧夹具只有 commands=[{status,returncode}] 且日志无 # command: 行，
    在新校验下必然失败）。
    """
    root = root.resolve()
    executables = runner.Executables(
        root / "pso_serial", root / "pso_mpi_ms", root / "pso_mpi_island",
        "mpiexec")
    commands: list[dict] = []
    lines_by_log: dict[Path, list[str]] = {}
    for key, per_seed in TIMES.items():
        experiment = _experiment(*key)
        log = root / runner.log_filename(experiment)
        for seed, elapsed in per_seed.items():
            argv = runner.experiment_command(experiment, seed, executables)
            command_text = runner.format_command(argv)
            commands.append({
                "status": "completed", "returncode": 0, "seed": seed,
                "experiment": dataclasses.asdict(experiment),
                "command": argv, "command_text": command_text,
                "log": str(log),
            })
            lines_by_log.setdefault(log, []).append(
                f"# command: {command_text}\n"
                f"record=result schema=pso_v1 implementation="
                f"{experiment.implementation} function=sphere particles=100 "
                f"dim=8 seed={seed} best=1.0 ranks={experiment.ranks} "
                f"threads=1 elapsed_max_s={elapsed:.6f}\n")
    manifest = {"schema": "pso_experiments_v1", "dry_run": False,
                "commands": commands, "configuration": BASE_CONFIG}
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for log, lines in lines_by_log.items():
        log.write_text("".join(lines), encoding="utf-8")


class BoundLoaderTests(unittest.TestCase):
    def test_requires_matching_command_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "x.log"
            log.write_text(
                "# command: pso_serial.exe --seed 1\n"
                "record=result schema=pso_v1 seed=1 best=1.0 "
                "elapsed_max_s=1.0\n", encoding="utf-8")
            self.assertEqual(
                analyze_stats.load_run_times_bound(
                    log, {1: "pso_serial.exe --seed 1"}),
                [(1, 1.0)])
            with self.assertRaises(ValueError):
                analyze_stats.load_run_times_bound(
                    log, {1: "pso_serial.exe --seed 2"})

    def test_rejects_non_positive_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "x.log"
            log.write_text(
                "# command: c\n"
                "record=result schema=pso_v1 seed=1 best=1.0 "
                "elapsed_max_s=0.000000000\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                analyze_stats.load_run_times_bound(log, {1: "c"})


class AnalyzeSpeedupsTests(unittest.TestCase):
    def test_table_pairs_baseline_by_seed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = analyze_speedups.main([
                    "--manifest-root", str(root), "--iterations", "200"])
            self.assertEqual(code, 0)
            output = stdout.getvalue()
            self.assertIn("x2.00", output)
            self.assertIn("x2.50", output)
            self.assertIn("mpi-data-parallel", output)
            # 表头由 print(header) 输出：列标签必须真的出现，防止未来
            # 又把它改回"算而不发"。
            self.assertIn("threads", output)
            self.assertIn("r1", output)
            self.assertNotIn("—", output)

    def test_missing_candidate_log_fails_loud(self) -> None:
        # 场景一：日志文件被删 → loader 的 log-missing 分支拒绝。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            (root / ("strong__mpi_island__fn-sphere__p-100__d-8__r-2__t-1"
                     "__b-reflect__dyn-constriction__mi-5.log")).unlink()
            with self.assertRaises(SystemExit) as raised:
                analyze_speedups.main(["--manifest-root", str(root),
                                       "--iterations", "200"])
            message = str(raised.exception)
            self.assertIn("strong__mpi_island__fn-sphere__p-100__d-8__r-2",
                          message)
            self.assertNotIn("x2.50", message)
        # 场景二（复审 M-4）：manifest 缺整条 strong command 而日志仍在 →
        # 必须走"先收集全部缺失再统一 SystemExit"的聚合分支，不得出半表。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            manifest = json.loads(
                (root / "manifest.json").read_text(encoding="utf-8"))
            manifest["commands"] = [
                c for c in manifest["commands"]
                if not (c["experiment"]["scaling"] == "strong"
                        and c["experiment"]["implementation"] == "mpi-island"
                        and c["experiment"]["ranks"] == 2)]
            (root / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                analyze_speedups.main(["--manifest-root", str(root),
                                       "--iterations", "200"])
            message = str(raised.exception)
            self.assertIn("缺少 strong 日志，拒绝以占位符静默降级", message)
            self.assertIn("strong__mpi_island__fn-sphere__p-100__d-8__r-2",
                          message)
            self.assertNotIn("x2.50", message)

    def test_duplicate_seed_log_fails_loud(self) -> None:
        # --append 复跑会在同一日志里留下重复 seed；dict 后写覆盖曾把它
        # 静默折叠，产出貌似可信的速度比。契约下沉到共享 loader 后必须硬失败。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            baseline = (root / ("baseline__serial__fn-sphere__p-100__d-8__r-1__t-1"
                                "__b-reflect__dyn-constriction__mi-0.log"))
            text = baseline.read_text(encoding="utf-8")
            first_command = text.splitlines()[0]
            baseline.write_text(
                text + first_command + "\n"
                + "record=result schema=pso_v1 implementation=serial "
                  "function=sphere particles=100 dim=8 seed=1 best=1.0 "
                  "ranks=1 threads=1 elapsed_max_s=2.000000\n",
                encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                analyze_speedups.main(["--manifest-root", str(root),
                                       "--iterations", "200"])
            message = str(raised.exception)
            self.assertIn("duplicate seeds", message)
            self.assertNotIn("x2.50", message)

    def test_malformed_manifest_is_rejected(self) -> None:
        # 坏 schema / dry-run 空壳 / 未完成命令 / 缺配置键必须硬失败，
        # 消息与 plot/summarize 同风格。
        import copy
        for key, value in (("schema", "bogus"), ("dry_run", True),
                           ("commands", [{"status": "failed"}]),
                           ("configuration", {"mode": "all"})):
            with self.subTest(key=key), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_fixture(root)
                manifest = copy.deepcopy(
                    json.loads((root / "manifest.json").read_text(encoding="utf-8")))
                manifest[key] = value
                (root / "manifest.json").write_text(
                    json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(SystemExit) as raised:
                    analyze_speedups.main([
                        "--manifest-root", str(root), "--iterations", "200"])
                expected = {"schema": "schema", "dry_run": "未真实采集",
                            "commands": "is not completed",
                            "configuration": "缺少键"}[key]
                self.assertIn(expected, str(raised.exception))

    def test_nonzero_returncode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            manifest = json.loads(
                (root / "manifest.json").read_text(encoding="utf-8"))
            manifest["commands"][0]["returncode"] = 7
            (root / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                analyze_speedups.main(["--manifest-root", str(root),
                                       "--iterations", "200"])
            self.assertIn("returncode", str(raised.exception))

    def test_missing_trace_fields_are_rejected(self) -> None:
        for field in ("log", "seed", "command_text", "experiment"):
            with self.subTest(field=field), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_fixture(root)
                manifest = json.loads(
                    (root / "manifest.json").read_text(encoding="utf-8"))
                manifest["commands"][0].pop(field)
                (root / "manifest.json").write_text(
                    json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    analyze_speedups.main(["--manifest-root", str(root),
                                           "--iterations", "200"])

    def test_tampered_log_command_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            baseline = next(root.glob("baseline__serial__fn-sphere*.log"))
            baseline.write_text(
                baseline.read_text(encoding="utf-8").replace(
                    "--seed 1", "--seed 99", 1), encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                analyze_speedups.main(["--manifest-root", str(root),
                                       "--iterations", "200"])
            self.assertIn("命令不匹配", str(raised.exception))

    def test_invalid_cli_arguments_fail_at_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            for arguments in (["--iterations", "0"], ["--impls", ""]):
                with self.subTest(arguments=arguments), \
                        self.assertRaises(SystemExit) as raised:
                    analyze_speedups.main(
                        ["--manifest-root", str(root), *arguments])
                self.assertEqual(raised.exception.code, 2)

    def test_partial_mode_manifest_is_rejected(self) -> None:
        # 速度比表需要 baseline 与 strong 两套日志同处一个 root；run_experiments
        # 的 mode 只能单选（all 才是并集），baseline/strong 套件不会同时存在，
        # 故根 manifest 必须是 mode=all（见 build_experiment_matrix）。
        for mode in ("baseline", "strong"):
            with self.subTest(mode=mode), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_fixture(root)
                manifest = json.loads(
                    (root / "manifest.json").read_text(encoding="utf-8"))
                manifest["configuration"]["mode"] = mode
                (root / "manifest.json").write_text(
                    json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(SystemExit) as raised:
                    analyze_speedups.main(["--manifest-root", str(root),
                                           "--iterations", "200"])
                self.assertIn("mode=all", str(raised.exception))

    def test_unparsable_manifest_is_system_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text("{broken", encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                analyze_speedups.main(["--manifest-root", str(root)])
            self.assertIn("不可读或损坏", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
