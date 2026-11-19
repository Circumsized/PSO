"""收敛汇总的端到端契约测试，仅使用标准库。"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import summarize_convergence as summary  # noqa: E402


def write_history(path: Path, evals_bests: list[tuple[int, float]]) -> None:
    lines = [f"iteration={i} evals={e} best={b}"
             for i, (e, b) in enumerate(evals_bests)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def log_name(suite: str, impl: str, fn: str = "rastrigin",
             particles: int = 100, dim: int = 50, ranks: int = 1,
             threads: int = 1, boundary: str = "reflect",
             dynamics: str = "constriction", migration: int = 0) -> str:
    return (f"{suite}__{impl.replace('-', '_')}__fn-{fn}__p-{particles}"
            f"__d-{dim}__r-{ranks}__t-{threads}__b-{boundary}"
            f"__dyn-{dynamics.replace('-', '_')}__mi-{migration}.log")


def add_run(root: Path, commands: list[dict], suite: str, impl: str,
            seed: int, evals_bests: list[tuple[int, float]], *,
            fn: str = "rastrigin", particles: int = 100, dim: int = 50,
            ranks: int = 1, threads: int = 1, boundary: str = "reflect",
            dynamics: str = "constriction", migration: int = 0,
            max_evals: int | None = None, max_iters: int = 1000,
            stop_on_target: int = 0, target: float = 1e-8,
            result_overrides: dict | None = None) -> None:
    """先写有效 CLI 日志；故障案例只改所测字段。"""
    root = root.resolve()
    log = root / log_name(suite, impl, fn, particles, dim, ranks, threads,
                          boundary, dynamics, migration)
    sidecar = Path(f"{log}.seed-{seed}.history")
    write_history(sidecar, evals_bests)
    if max_evals is None:
        max_evals = evals_bests[-1][0]
    argv = [str(root / "build" / f"pso_{impl.replace('-', '_')}"),
            "--function", fn, "--particles", str(particles), "--dim", str(dim),
            "--max-evals", str(max_evals), "--max-iters", str(max_iters),
            "--seed", str(seed), "--boundary", boundary, "--target", str(target),
            "--history", str(sidecar)]
    if not stop_on_target:
        argv.append("--no-early-stop")
    if dynamics == "linear-inertia":
        argv.append("--linear-inertia")
    if impl == "mpi-island":
        argv.extend(("--migration-interval", str(migration)))
    if impl != "serial":
        argv = ["mpiexec", "-n", str(ranks), *argv]
    command_text = subprocess.list2cmdline(argv)
    hit = next((e for e, best in evals_bests if best <= target), None)
    result = {
        "record": "result", "schema": "pso_v1",
        "implementation": "mpi-data-parallel" if impl == "mpi-ms" else impl,
        "function": fn, "particles": particles, "dim": dim,
        "max_evals": max_evals, "max_iters": max_iters, "seed": seed,
        "boundary": boundary, "dynamics": dynamics, "target": target,
        "lower": -5.12, "upper": 5.12, "chi": 0.7298437881283576,
        "c1": 2.05, "c2": 2.05, "vmax_ratio": 0.2,
        "inertia_start": 0.9, "inertia_end": 0.4,
        "ranks": ranks, "threads": threads, "migration_interval": migration,
        "iterations": len(evals_bests) - 1, "evals": evals_bests[-1][0],
        "best": evals_bests[-1][1], "success": int(hit is not None),
        "fes_to_target": hit if hit is not None else (max_evals or evals_bests[-1][0]),
        "stop_reason": ("target" if stop_on_target and hit is not None else
                        "evaluation_budget" if max_evals else "iteration_limit"),
        "stop_on_target": stop_on_target, "timing_scope": "solver",
        "elapsed_max_s": 0.125,
    }
    for field, value in (result_overrides or {}).items():
        if value is None:
            result.pop(field)
        else:
            result[field] = value
    with log.open("a", encoding="utf-8") as stream:
        stream.write(f"# command: {command_text}\n")
        stream.write(" ".join(f"{k}={v}" for k, v in result.items()) + "\n")
    commands.append({
        "run": seed, "seed": seed,
        "experiment": {"scaling": suite, "implementation": impl,
                       "function": fn, "particles": particles, "dim": dim,
                       "max_evals": max_evals, "ranks": ranks, "threads": threads,
                       "boundary": boundary, "dynamics": dynamics,
                       "migration_interval": migration},
        "command": argv, "command_text": command_text,
        "environment": {"OMP_NUM_THREADS": str(threads), "OMP_DYNAMIC": "FALSE"},
        "log": str(log), "history": str(sidecar),
    })


def write_manifest(root: Path, commands: list[dict], **overrides) -> dict:
    """补默认成功状态，显式故障值必须保留。"""
    manifest = {
        "schema": "pso_experiments_v1", "dry_run": False,
        "commands": [{"status": "completed", "returncode": 0, **command}
                     for command in commands],
    }
    manifest.update(overrides)
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def run_tool(root: Path, *args: str) -> tuple[int | str, str, str]:
    """捕获真实入口输出，数据失败返回中文 SystemExit 消息。"""
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = summary.main(["--manifest-root", str(root), *args])
        except SystemExit as exit_error:
            code = exit_error.code
    return code, stdout.getvalue(), stderr.getvalue()


class SummaryTestCase(unittest.TestCase):
    def assert_rejected(self, root: Path, reason: str) -> str:
        code, output, _ = run_tool(root)
        self.assertIsInstance(code, str)
        self.assertIn(reason, code)
        self.assertEqual(output, "", "校验失败不得输出半张表")
        return code

    def _fixture(self, root: Path, **overrides) -> dict:
        commands: list[dict] = []
        add_run(root, commands, "baseline", "serial", 0,
                [(100, 9.0), (200, 0.5)])
        return write_manifest(root, commands, **overrides)


class GroupingTests(SummaryTestCase):
    def test_particles_and_dim_are_part_of_group_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            for particles, dim, best in ((100, 50, 1.0), (100, 100, 2.0),
                                          (200, 50, 3.0)):
                for seed in range(2):
                    add_run(root, commands, "baseline", "serial", seed,
                            [(particles, 9.0), (2 * particles, best)],
                            particles=particles, dim=dim)
            write_manifest(root, commands)
            code, output, _ = run_tool(root)
            self.assertEqual(code, 0)
            median_section = output.split("== fixed-budget")[0]
            rows = [line.split() for line in median_section.splitlines()
                    if line.startswith("baseline/serial/rastrigin/")]
            self.assertEqual({row[0].split("/")[3] for row in rows},
                             {"p100d50", "p100d100", "p200d50"})
            self.assertEqual(len(rows), 3)
            self.assertEqual({row[1] for row in rows}, {"1", "2", "3"})

    def test_duplicate_log_seed_aborts_instead_of_silent_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = self._fixture(root)["commands"][0]
            second = Path(command["history"] + ".pass2")
            write_history(second, [(100, 9.0), (200, 0.5)])
            write_manifest(root, [command, {**command, "history": str(second)}])
            self.assert_rejected(root, "seed 0 重复")

    def test_duplicate_log_seed_detected_across_path_spellings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = self._fixture(root)["commands"][0]
            second = Path(command["history"] + ".pass2")
            write_history(second, [(100, 9.0), (200, 0.5)])
            alias = root / ".." / root.name / Path(command["log"]).name
            write_manifest(root, [command, {**command, "log": str(alias),
                                           "history": str(second)}])
            self.assert_rejected(root, "seed 0 重复")

    def test_unmatched_log_name_aborts_instead_of_silent_skip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = self._fixture(root)["commands"][0]
            stray = root / "smoke_serial__fn-sphere.log"
            Path(command["log"]).rename(stray)
            command["log"] = str(stray)
            write_manifest(root, [command])
            message = self.assert_rejected(root, "拒绝静默跳过")
            self.assertIn(stray.name, message)

    def test_shared_history_across_seeds_or_logs_is_rejected(self) -> None:
        for second_dim in (50, 100):
            with self.subTest(dim=second_dim), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commands: list[dict] = []
                add_run(root, commands, "baseline", "serial", 0,
                        [(100, 9.0), (200, 0.5)])
                add_run(root, commands, "baseline", "serial", 1,
                        [(100, 9.0), (200, 0.5)], dim=second_dim)
                sidecar = Path(commands[0]["history"])
                commands[1]["history"] = str(root / ".." / root.name / sidecar.name)
                write_manifest(root, commands)
                self.assert_rejected(root, "history 共用")

    def test_same_log_in_different_manifests_is_rejected_even_with_distinct_seeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            for seed in (0, 1):
                add_run(root, commands, "baseline", "serial", seed,
                        [(100, 9.0), (200, 0.5)])
            write_manifest(root, commands[:1])
            batch = root / "batch"
            batch.mkdir()
            write_manifest(batch, commands[1:])
            self.assert_rejected(root, "不同 manifest 重复引用")


class ErtTests(SummaryTestCase):
    def manifest_root(self, directory: str) -> Path:
        root = Path(directory)
        commands: list[dict] = []
        for seed in range(3):
            add_run(root, commands, "baseline", "serial", seed,
                    [(100, 9.0), (200, 0.5)])
        # ablation 的不同预算不参与 ERT；每代仍须推进 particles 次。
        add_run(root, commands, "ablation", "mpi-island", 0,
                [(100, 9.0), (200, 1.0)], ranks=4, migration=10)
        add_run(root, commands, "ablation", "mpi-island", 1,
                [(100, 9.0), (200, 2.0), (300, 1.0)], ranks=4, migration=10)
        write_manifest(root, commands)
        return root

    def test_ert_row_uses_group_budget_and_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.manifest_root(directory)
            code, output, _ = run_tool(root)
            self.assertEqual(code, 0)
            ert_section = output.split("== fixed-budget")[1]
            rows = [line for line in ert_section.splitlines()
                    if line.startswith("baseline/")]
            self.assertEqual(len(rows), 1)
            self.assertIn("p100d50", rows[0])
            self.assertEqual(rows[0].split()[1:], ["3/200", "3/200", "3/200"])
            self.assertNotIn("ablation", ert_section)

    def test_partial_success_uses_full_budget_not_first_hit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            for seed, history in enumerate(([(100, 0.5), (200, 0.5)],
                                             [(100, 9.0), (200, 0.5)],
                                             [(100, 9.0), (200, 2.0)])):
                add_run(root, commands, "baseline", "serial", seed, history)
            write_manifest(root, commands)
            code, output, _ = run_tool(root, "--targets", "1,0")
            self.assertEqual(code, 0)
            row = next(line for line in output.split("== fixed-budget")[1].splitlines()
                       if line.startswith("baseline/"))
            # B=200、n=3、s=2，手算 ERT=300；零命中为 na。
            self.assertEqual(row.split()[1:], ["2/300", "0/na"])

    def test_inconsistent_group_budget_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            add_run(root, commands, "baseline", "serial", 0,
                    [(100, 9.0), (200, 0.5)])
            add_run(root, commands, "baseline", "serial", 1,
                    [(100, 9.0), (200, 3.0), (300, 1.0), (400, 0.5)])
            write_manifest(root, commands)
            message = self.assert_rejected(root, "预算")
            self.assertIn("p100d50", message)

    def test_group_budget_guard_spans_distinct_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, history in enumerate(([(100, 9.0), (200, 0.5)],
                                             [(100, 9.0), (200, 2.0), (300, 0.5)])):
                batch = root / str(index)
                batch.mkdir()
                commands: list[dict] = []
                add_run(batch, commands, "strong", "mpi-ms", 0, history, ranks=2)
                write_manifest(batch, commands)
            self.assert_rejected(root, "预算")

    def test_fixed_budget_rejects_early_stopping_mode(self) -> None:
        for suite in ("baseline", "strong"):
            with self.subTest(suite=suite), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commands: list[dict] = []
                add_run(root, commands, suite, "serial", 0,
                        [(100, 9.0), (200, 0.5)], stop_on_target=1, target=1.0)
                write_manifest(root, commands)
                self.assert_rejected(root, "stop_on_target=0")

    def test_fixed_budget_rejects_partial_or_overrun_trajectory(self) -> None:
        for suite in ("baseline", "strong"):
            for budget in ({"max_evals": 300}, {"max_evals": 150},
                           {"max_evals": 0, "max_iters": 2}):
                with self.subTest(suite=suite, budget=budget), \
                        tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    commands: list[dict] = []
                    add_run(root, commands, suite, "serial", 0,
                            [(100, 9.0), (200, 0.5)], **budget)
                    write_manifest(root, commands)
                    self.assert_rejected(root, "固定预算")

    def test_fixed_budget_rounding_and_iteration_mode_are_accepted(self) -> None:
        cases = ((250, 1, [(100, 9.0), (200, 0.5)], "1/200"),
                 (399, 1, [(100, 9.0), (200, 2.0), (300, 0.5)], "1/300"),
                 (100, 1, [(100, 0.5)], "1/100"),
                 (199, 1, [(100, 0.5)], "1/100"),
                 (0, 2, [(100, 9.0), (200, 2.0), (300, 0.5)], "1/300"))
        for max_evals, max_iters, history, expected in cases:
            with self.subTest(max_evals=max_evals), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commands: list[dict] = []
                add_run(root, commands, "baseline", "serial", 0, history,
                        max_evals=max_evals, max_iters=max_iters)
                write_manifest(root, commands)
                code, output, _ = run_tool(root, "--targets", "1")
                self.assertEqual(code, 0)
                row = next(line for line in output.split("== fixed-budget")[1].splitlines()
                           if line.startswith("baseline/"))
                self.assertEqual(row.split()[1:], [expected])

    def test_weak_and_ablation_early_stop_have_no_ert_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            for suite in ("weak", "ablation"):
                add_run(root, commands, suite, "mpi-island", 0,
                        [(100, 9.0), (200, 0.5)], ranks=4, migration=10,
                        max_evals=500, stop_on_target=1, target=1.0)
            write_manifest(root, commands)
            code, output, _ = run_tool(root)
            self.assertEqual(code, 0)
            medians, ert = output.split("== fixed-budget")
            # 组标签已统一为 pso_v1 结果行的 implementation 字段
            # （文件名 token mpi_island 不再出现在输出里）。
            self.assertIn("weak/mpi-island/", medians)
            self.assertIn("ablation/mpi-island/", medians)
            self.assertNotIn("mpi_island", medians)
            self.assertNotIn("weak/", ert)
            self.assertNotIn("ablation/", ert)


class ManifestValidationTests(SummaryTestCase):
    def test_valid_manifest_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            code, output, _ = run_tool(root)
            self.assertEqual(code, 0)
            self.assertIn("baseline/serial/rastrigin/p100d50", output)

    def test_missing_root_or_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assert_rejected(root, "manifest.json")
            self.assert_rejected(root / "missing", "目录")
            file_root = root / "file"
            file_root.write_text("file", encoding="utf-8")
            self.assert_rejected(file_root, "目录")

    def test_dry_run_manifest_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root, dry_run=True)
            self.assert_rejected(root, "dry-run")

    def test_wrong_schema_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root, schema="something_else")
            self.assert_rejected(root, "schema")

    def test_invalid_manifest_structure_rejected(self) -> None:
        cases = (([], "JSON 对象"),
                 ({"schema": "pso_experiments_v1", "dry_run": False,
                   "commands": []}, "非空列表"),
                 ({"schema": "pso_experiments_v1", "dry_run": False,
                   "commands": [None]}, "必须是对象"))
        for manifest, reason in cases:
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                self.assert_rejected(root, reason)

    def test_incomplete_or_failed_command_rejected_for_precise_reason(self) -> None:
        cases = (({"status": "planned"}, "未完成"),
                 ({"status": "running"}, "未完成"),
                 ({"status": "failed"}, "未完成"),
                 ({"status": None}, "未完成"),
                 ({"returncode": 1}, "返回码"),
                 ({"returncode": None}, "返回码"),
                 ({"returncode": False}, "返回码"))
        for override, reason in cases:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commands = self._fixture(root)["commands"]
                commands[0].update(override)
                write_manifest(root, commands)
                self.assert_rejected(root, reason)

    def test_unreadable_manifest_rejected(self) -> None:
        for content in (b"{not json", b"\xff"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "manifest.json").write_bytes(content)
                self.assert_rejected(root, "不可读")

    def test_seed_requires_nonnegative_integer(self) -> None:
        for value in (-1, True, 0.5, "0", None):
            with self.subTest(seed=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commands = self._fixture(root)["commands"]
                commands[0]["seed"] = value
                write_manifest(root, commands)
                self.assert_rejected(root, "非负整数 seed")

    def test_command_text_is_required(self) -> None:
        # command_text 是防篡改核验的锚，缺失必须拒绝而非静默跳过核验。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = self._fixture(root)["commands"]
            del commands[0]["command_text"]
            write_manifest(root, commands)
            self.assert_rejected(root, "防篡改核验需要")

    def test_missing_history_or_log_field_never_drops_command(self) -> None:
        for field in ("history", "log"):
            for value in (None, "", " ", 5, []):
                with self.subTest(field=field, value=value), \
                        tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    commands: list[dict] = []
                    for seed in (0, 1):
                        add_run(root, commands, "baseline", "serial", seed,
                                [(100, 9.0), (200, 0.5)])
                    if value is None:
                        commands[1].pop(field)
                    else:
                        commands[1][field] = value
                    write_manifest(root, commands)
                    self.assert_rejected(root, field)

    def test_paths_must_be_absolute_and_within_root(self) -> None:
        for field in ("log", "history"):
            for relative in (True, False):
                with self.subTest(field=field, relative=relative), \
                        tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    commands = self._fixture(root)["commands"]
                    name = Path(commands[0][field]).name
                    commands[0][field] = name if relative else str(root.parent / name)
                    write_manifest(root, commands)
                    message = self.assert_rejected(root, "绝对路径" if relative else "root")
                    self.assertIn(field, message)

    def test_missing_sidecar_reports_clean_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = self._fixture(root)["commands"]
            Path(commands[0]["history"]).unlink()
            self.assert_rejected(root, "sidecar 不可读")

    def test_empty_or_corrupt_sidecar_reports_clean_message(self) -> None:
        for content, reason in ((b"", "empty history"), (b"\xff", "sidecar 不可读")):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                command = self._fixture(root)["commands"][0]
                Path(command["history"]).write_bytes(content)
                message = self.assert_rejected(root, reason)
                self.assertIn("解析失败", message)


class LogValidationTests(SummaryTestCase):
    def test_missing_empty_or_corrupt_log_is_rejected(self) -> None:
        for content in (None, b"", b"\xff"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                log = Path(self._fixture(root)["commands"][0]["log"])
                if content is None:
                    log.unlink()
                else:
                    log.write_bytes(content)
                self.assert_rejected(root, "日志解析失败")

    def test_strict_parser_rejects_schema_progress_nonfinite_and_missing_result(self) -> None:
        cases = (("schema=pso_v1", "schema=invalid", "schema"),
                 ("record=result", "record=progress", "progress"),
                 ("best=0.5", "best=nan", "finite"),
                 ("elapsed_max_s=0.125", "elapsed_max_s=inf", "finite"),
                 ("record=result", "record=other", "command/result"))
        for old, new, reason in cases:
            with self.subTest(new=new), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                log = Path(self._fixture(root)["commands"][0]["log"])
                log.write_text(log.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
                message = self.assert_rejected(root, reason)
                self.assertIn("日志解析失败", message)

    def test_missing_command_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = Path(self._fixture(root)["commands"][0]["log"])
            result = log.read_text(encoding="utf-8").splitlines()[1]
            log.write_text(result + "\n", encoding="utf-8")
            self.assert_rejected(root, "command/result")

    def test_duplicate_result_seed_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            add_run(root, commands, "baseline", "serial", 0,
                    [(100, 9.0), (200, 0.5)])
            # 同一 (command,result) 对写两遍：命令文本一致，绕过防篡改
            # 配对层，由解析器自身的重复 seed 守卫命中。
            log = Path(commands[0]["log"])
            text = log.read_text(encoding="utf-8")
            log.write_text(text + text, encoding="utf-8")
            write_manifest(root, commands)
            self.assert_rejected(root, "seed 0 重复")

    def test_result_seed_set_must_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            add_run(root, commands, "baseline", "serial", 0,
                    [(100, 9.0), (200, 0.5)], result_overrides={"seed": 1})
            write_manifest(root, commands)
            # 结果行 seed 与 manifest 不一致时，防篡改配对层先行拒绝
            # （命令文本按 manifest seed 登记，查不到该 seed 的期望命令）。
            self.assert_rejected(root, "seed 或命令不匹配")

    def test_extra_or_missing_result_seed_is_rejected(self) -> None:
        for extra in (True, False):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commands: list[dict] = []
                for seed in (0, 1):
                    add_run(root, commands, "baseline", "serial", seed,
                            [(100, 9.0), (200, 0.5)])
                if extra:
                    write_manifest(root, commands[:1])
                    # 多出的结果行查不到期望命令，由防篡改配对层拒绝。
                    self.assert_rejected(root, "seed 或命令不匹配")
                else:
                    write_manifest(root, commands)
                    log = Path(commands[0]["log"])
                    lines = log.read_text(encoding="utf-8").splitlines()
                    log.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
                    # 缺失的结果行则由 manifest/result 计数守卫命中。
                    self.assert_rejected(root, "manifest/result")

    def test_result_integer_fields_are_required_and_valid(self) -> None:
        cases = (("seed", "1.5"), ("seed", "-1"), ("seed", None),
                 ("particles", "0"), ("particles", "nan"),
                 ("evals", "200.0"), ("evals", "-1"), ("evals", None),
                 ("iterations", "-1"), ("iterations", None),
                 ("max_evals", "nan"), ("max_evals", "-1"), ("max_evals", None),
                 ("max_iters", "0"), ("max_iters", "1.5"), ("max_iters", None),
                 ("stop_on_target", None))
        for field, value in cases:
            with self.subTest(field=field, value=value), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commands: list[dict] = []
                add_run(root, commands, "baseline", "serial", 0,
                        [(100, 9.0), (200, 0.5)], result_overrides={field: value})
                write_manifest(root, commands)
                self.assert_rejected(root, field)

    def test_each_log_is_read_once_and_results_join_by_seed_not_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            for seed, best in ((0, 0.5), (1, 2.0)):
                add_run(root, commands, "baseline", "serial", seed,
                        [(100, 9.0), (200, best)])
            write_manifest(root, list(reversed(commands)))
            log = Path(commands[0]["log"])
            original_read = Path.read_text
            reads: list[Path] = []

            def read_once(path, *args, **kwargs):
                if path == log:
                    reads.append(path)
                return original_read(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", read_once):
                code, output, _ = run_tool(root, "--targets", "1")
            self.assertEqual(code, 0)
            self.assertEqual(reads, [log])
            self.assertIn("1/400", output.split("== fixed-budget")[1])


class IdentityTests(SummaryTestCase):
    def test_filename_configuration_matches_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = self._fixture(root)["commands"]
            old = Path(commands[0]["log"])
            renamed = root / old.name.replace("fn-rastrigin", "fn-sphere")
            old.rename(renamed)
            commands[0]["log"] = str(renamed)
            write_manifest(root, commands)
            self.assert_rejected(root, "命名配置")

    def test_manifest_plan_budget_cannot_be_replaced_by_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = self._fixture(root)["commands"]
            commands[0]["experiment"]["max_evals"] = 20000
            write_manifest(root, commands)
            self.assert_rejected(root, "max_evals")

    def test_result_configuration_matches_manifest(self) -> None:
        for field, value in (("function", "sphere"), ("dim", 3),
                             ("implementation", "mpi-island"), ("threads", 2)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commands: list[dict] = []
                add_run(root, commands, "baseline", "serial", 0,
                        [(100, 9.0), (200, 0.5)], result_overrides={field: value})
                write_manifest(root, commands)
                self.assert_rejected(root, field)

    def test_swapping_history_references_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            add_run(root, commands, "baseline", "serial", 7,
                    [(100, 9.0), (200, 0.5), (300, 0.0)])
            add_run(root, commands, "strong", "mpi-ms", 8,
                    [(100, 8.0), (200, 4.0), (300, 0.0)], ranks=2)
            commands[0]["history"], commands[1]["history"] = (
                commands[1]["history"], commands[0]["history"])
            write_manifest(root, commands)
            self.assert_rejected(root, "history")

    def test_history_first_hit_must_match_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            add_run(root, commands, "baseline", "serial", 0,
                    [(100, 9.0), (200, 0.5), (300, 0.0)], target=1.0)
            write_manifest(root, commands)
            Path(commands[0]["history"]).write_text(
                "iteration=0 evals=100 best=9\niteration=1 evals=200 best=4\n"
                "iteration=2 evals=300 best=0\n", encoding="utf-8")
            self.assert_rejected(root, "fes_to_target")


class TrajectoryValidationTests(SummaryTestCase):
    def test_terminal_values_must_match_result(self) -> None:
        cases = (({"evals": 300}, "evals"),
                 ({"best": 0.25}, "best"),
                 ({"iterations": 2}, "iteration"))
        for override, field in cases:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commands: list[dict] = []
                add_run(root, commands, "baseline", "serial", 0,
                        [(100, 9.0), (200, 0.5)], result_overrides=override)
                write_manifest(root, commands)
                message = self.assert_rejected(root, "末行")
                self.assertIn(field, message)

    def test_initial_evals_must_equal_particles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            add_run(root, commands, "baseline", "serial", 0,
                    [(50, 9.0), (200, 0.5)])
            write_manifest(root, commands)
            self.assert_rejected(root, "初始 evals")

    def test_evals_must_advance_by_particles_each_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            add_run(root, commands, "ablation", "serial", 0,
                    [(100, 9.0), (250, 2.0), (300, 0.5)])
            write_manifest(root, commands)
            self.assert_rejected(root, "逐代")

    def test_existing_monotonicity_and_iteration_guards_remain(self) -> None:
        cases = (("iteration=0 evals=100 best=9\niteration=2 evals=200 best=1\n", "iteration"),
                 ("iteration=0 evals=100 best=9\niteration=1 evals=100 best=1\n", "evals"),
                 ("iteration=0 evals=100 best=9\niteration=1 evals=200 best=10\n", "best-so-far"),
                 ("iteration=0 evals=100 best=nan\n", "finite"))
        for content, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                command = self._fixture(root)["commands"][0]
                Path(command["history"]).write_text(content, encoding="utf-8")
                self.assert_rejected(root, reason)

    def test_invalid_later_log_does_not_print_partial_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands: list[dict] = []
            add_run(root, commands, "baseline", "serial", 0,
                    [(100, 9.0), (200, 0.5)])
            add_run(root, commands, "strong", "mpi-ms", 0,
                    [(100, 9.0), (200, 0.5)], ranks=2,
                    result_overrides={"best": 0.25})
            write_manifest(root, commands)
            self.assert_rejected(root, "末行 best")


class CliTests(SummaryTestCase):
    def test_invalid_targets_and_checkpoints_fail_at_entry(self) -> None:
        cases = (("--targets", ("nan", "inf", "-inf", "-1", "oops", "", "1,,2", "1,")),
                 ("--checkpoints", ("nan", "inf", "1.5", "0", "-1", "", ",", "1,,2")))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # 没有 manifest：参数错误必须先于数据读取报出。
            for option, values in cases:
                for value in values:
                    with self.subTest(option=option, value=value):
                        code, output, error = run_tool(root, f"{option}={value}")
                        self.assertEqual(code, 2)
                        self.assertEqual(output, "")
                        self.assertIn(option, error)

    def test_checkpoints_keep_step_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._fixture(root)
            code, output, _ = run_tool(root, "--targets=0", "--checkpoints=50,150,200")
            self.assertEqual(code, 0)
            row = next(line for line in output.split("== fixed-budget")[0].splitlines()
                       if line.startswith("baseline/"))
            self.assertEqual(row.split()[1:], ["9", "9", "0.5"])

    def test_real_cli_missing_log_exits_nonzero_without_table_or_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = self._fixture(root)["commands"][0]
            Path(command["log"]).unlink()
            process = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", str(Path(summary.__file__).resolve()),
                 "--manifest-root", str(root)], capture_output=True, text=True,
                encoding="utf-8", check=False, timeout=20)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(process.stdout, "")
            self.assertIn("日志解析失败", process.stderr)
            self.assertNotIn("Traceback", process.stderr)


if __name__ == "__main__":
    unittest.main()
