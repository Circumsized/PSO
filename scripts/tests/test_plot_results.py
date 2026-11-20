from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import plot_results  # noqa: E402


class PlotResultsTests(unittest.TestCase):
    @staticmethod
    def _full_result(best: str = "0.5", elapsed: str = "0.25",
                     seed: str = "1", **overrides: str) -> str:
        """字段与顺序对齐 src/pso_report.c 的真实 pso_v1 输出行。"""
        fields = {
            "record": "result", "schema": "pso_v1",
            "implementation": "serial", "function": "sphere",
            "particles": "8", "dim": "2", "max_evals": "100000",
            "max_iters": "1000", "seed": seed, "boundary": "reflect",
            "dynamics": "constriction", "target": "1e-8", "lower": "-100",
            "upper": "100", "chi": "1", "c1": "2", "c2": "2",
            "vmax_ratio": "1", "inertia_start": "0.9", "inertia_end": "0.4",
            "ranks": "1", "threads": "1", "migration_interval": "10",
            "iterations": "2", "evals": "16", "best": best, "success": "1",
            "fes_to_target": "16", "stop_reason": "target",
            "stop_on_target": "1", "timing_scope": "solver",
            "elapsed_max_s": elapsed,
        }
        fields.update(overrides)
        return " ".join(f"{key}={value}" for key, value in fields.items()) + "\n"

    @staticmethod
    def _result(best: str = "0.5", elapsed: str = "0.25",
                seed: str = "1") -> str:
        return PlotResultsTests._full_result(best=best, elapsed=elapsed,
                                             seed=seed)

    @staticmethod
    def _write_manifest(root: Path, logs: list[Path], *, dry_run: bool = False,
                        status: str = "completed", returncode: int = 0,
                        seeds: list[int] | None = None,
                        command_texts: list[str] | None = None) -> Path:
        manifest = {
            "schema": "pso_experiments_v1",
            "dry_run": dry_run,
            "commands": [
                {
                    "status": status,
                    "returncode": returncode,
                    "log": str(log.resolve()),
                    "seed": (seeds[i] if seeds else 1),
                    "command_text": (command_texts[i] if command_texts
                                     else "solver"),
                }
                for i, log in enumerate(logs)
            ],
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_parser_accepts_v1_elapsed_and_legacy_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.log"
            path.write_text(
                "record=result schema=pso_v1 implementation=serial function=sphere "
                "particles=8 dim=2 seed=1 best=0.5 success=1 "
                "fes_to_target=16 elapsed_max_s=0.25\n"
                "progress implementation=serial step=1 best=0.25 target=1e-8\n"
                "seed=2 best=1.5 target=2 time=0.75\n",
                encoding="utf-8")
            rows = plot_results.parse_log(path, "fallback")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["implementation"], "serial")
        self.assertEqual(rows[0]["time"], "0.25")
        self.assertEqual(rows[0]["elapsed_max_s"], "0.25")
        self.assertEqual(rows[1]["implementation"], "fallback")
        self.assertEqual(rows[1]["time"], "0.75")
        self.assertEqual(plot_results.summary([rows[1]])[0]["success_rate"], "1")

    def test_complete_manifest_loads_two_deduplicated_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = root / "batch"
            batch.mkdir()
            first = batch / "first.log"
            second = batch / "second.log"
            first.write_text(
                "# command: solver --seed 1\n" + self._result("1", "0.1", seed="1")
                + "# command: solver --seed 2\n" + self._result("2", "0.2", seed="2"),
                encoding="utf-8")
            second.write_text(
                "# command: solver --seed 3\n" + self._result("3", "0.3", seed="3"),
                encoding="utf-8")
            self._write_manifest(
                batch, [first, first, second], seeds=[1, 2, 3],
                command_texts=["solver --seed 1", "solver --seed 2",
                               "solver --seed 3"])

            rows = plot_results.load_manifest_rows(root)

        self.assertEqual(len(rows), 3)
        self.assertEqual([row["best"] for row in rows], ["1", "2", "3"])

    def test_manifest_log_with_duplicate_seed_is_rejected(self) -> None:
        # --append 复跑会在同一日志留下两个同 seed 结果；plot 的 manifest
        # 路径曾静默把两轮数据一起收进统计（analyze_stats 早已硬失败），
        # 必须同口径拒绝。manifest 只登记一次该 seed，让解析层守卫命中。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "run.log"
            log.write_text(
                "# command: solver --seed 1\n" + self._result("0.5", seed="1")
                + "# command: solver --seed 1\n" + self._result("0.9", seed="1"),
                encoding="utf-8")
            self._write_manifest(
                root, [log], seeds=[1], command_texts=["solver --seed 1"])
            with self.assertRaisesRegex(ValueError, "duplicate seeds"):
                plot_results.load_manifest_rows(root)

    def test_manifest_missing_tamper_fields_is_rejected(self) -> None:
        # seed/command_text 是防篡改核验的锚，缺任一字段都必须拒绝而非
        # 静默跳过核验。
        for field in ("seed", "command_text"):
            with self.subTest(field=field), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                log = root / "run.log"
                log.write_text(
                    "# command: solver --seed 1\n" + self._result(seed="1"),
                    encoding="utf-8")
                command = {"status": "completed", "returncode": 0,
                            "log": str(log.resolve()), "seed": 1,
                            "command_text": "solver --seed 1"}
                del command[field]
                (root / "manifest.json").write_text(json.dumps({
                    "schema": "pso_experiments_v1", "dry_run": False,
                    "commands": [command]}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "防篡改核验需要"):
                    plot_results.load_manifest_rows(root)

    def test_manifest_log_with_tampered_command_is_rejected(self) -> None:
        # 防篡改核验：日志里的 # command: 行被改（如把 --seed 100 改成
        # --seed 999）时，manifest 记录的 command_text 必须对不上并拒绝；
        # 该缺口曾让 seed 撒谎也能通过全部正式消费者。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "run.log"
            log.write_text(
                "# command: solver --seed 999\n" + self._result("0.5", seed="1"),
                encoding="utf-8")
            self._write_manifest(
                root, [log], seeds=[1], command_texts=["solver --seed 1"])
            with self.assertRaisesRegex(ValueError, "seed 或命令不匹配"):
                plot_results.load_manifest_rows(root)

    def test_dry_run_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "run.log"
            log.write_text("# command: solver\n" + self._result(), encoding="utf-8")
            self._write_manifest(root, [log], dry_run=True)
            with self.assertRaisesRegex(ValueError, "dry-run"):
                plot_results.load_manifest_rows(root)

    def test_incomplete_command_records_are_rejected(self) -> None:
        for status, returncode in (("running", 0), ("failed", 1), ("completed", 1)):
            with self.subTest(status=status, returncode=returncode), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                log = root / "run.log"
                log.write_text(
                    "# command: solver\n" + self._result(), encoding="utf-8")
                self._write_manifest(
                    root, [log], status=status, returncode=returncode)
                with self.assertRaises(ValueError):
                    plot_results.load_manifest_rows(root)

    def test_trailing_command_without_result_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "run.log"
            log.write_text(
                "# command: solver --seed 1\n" + self._result(seed="1")
                + "# command: solver --seed 2\n",
                encoding="utf-8")
            self._write_manifest(
                root, [log, log], seeds=[1, 2],
                command_texts=["solver --seed 1", "solver --seed 2"])
            with self.assertRaisesRegex(ValueError, "command/result 缺少结果"):
                plot_results.load_manifest_rows(root)

    def test_strict_parser_binds_each_result_to_its_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.log"
            path.write_text("# command: solver --seed 2\n" + self._result(),
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "command/result"):
                plot_results.parse_log_strict(
                    path, 1, expected_commands={1: "solver --seed 1"})
            path.write_text("# command: solver --seed 1\n" + self._result(),
                            encoding="utf-8")
            rows = plot_results.parse_log_strict(
                path, 1, expected_commands={1: "solver --seed 1"})
            self.assertEqual(rows[0]["best"], "0.5")

    def test_log_outside_manifest_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "root"
            root.mkdir()
            log = parent / "outside.log"
            log.write_text("# command: solver\n" + self._result(), encoding="utf-8")
            self._write_manifest(root, [log])
            with self.assertRaisesRegex(ValueError, "outside manifest root"):
                plot_results.load_manifest_rows(root)

    def test_non_finite_numbers_are_rejected(self) -> None:
        cases = (
            self._result("NaN", "0.1"),
            self._result("1", "Inf"),
            self._result().replace("fes_to_target=16", "fes_to_target=-Inf"),
        )
        for content in cases:
            with self.subTest(content=content), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                log = root / "run.log"
                log.write_text(
                    "# command: solver\n" + content, encoding="utf-8")
                self._write_manifest(root, [log])
                with self.assertRaisesRegex(ValueError, "must be finite"):
                    plot_results.load_manifest_rows(root)

    def test_progress_and_non_v1_results_are_rejected(self) -> None:
        cases = (
            self._result() + "record=progress schema=pso_v1 best=0.4\n",
            self._result().replace("schema=pso_v1", "schema=other"),
        )
        for content in cases:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                log = root / "run.log"
                log.write_text("# command: solver\n" + content, encoding="utf-8")
                self._write_manifest(root, [log])
                with self.assertRaises(ValueError):
                    plot_results.load_manifest_rows(root)

    def test_corrupted_encoding_hard_fails_instead_of_replacing(self) -> None:
        """编码损坏必须就地失败。parse_log 曾用 errors="replace"，\\xff 会
        变成替换字符静默混入解析；钉住 strict 语义，防该行为回退。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.log"
            path.write_bytes(
                b"record=result schema=pso_v1 implementation=serial best=\xff\n")
            with self.assertRaises(UnicodeDecodeError):
                plot_results.parse_log(path, "serial")

    def test_input_and_manifest_root_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit):
            plot_results.main(["--input", "results", "--manifest-root", "formal"])

    def test_manifest_root_cli_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = root / "batch"
            batch.mkdir()
            log = batch / "run.log"
            log.write_text("# command: solver\n" + self._result(), encoding="utf-8")
            self._write_manifest(batch, [log])
            output = root / "summary.csv"

            with contextlib.redirect_stdout(io.StringIO()):
                result = plot_results.main([
                    "--manifest-root", str(root), "--output", str(output)])

            self.assertEqual(result, 0)
            self.assertTrue(output.is_file())
            self.assertIn("best_mean", output.read_text(encoding="utf-8"))

    def test_strict_manifest_path_rejects_missing_pinned_field(self) -> None:
        # 缺钉住字段会在 summary 里静默产生空单元格并独立成组（审阅确认
        # 项 #6），--manifest-root 严格路径必须硬失败。
        for field in ("timing_scope", "stop_on_target", "max_evals"):
            with self.subTest(field=field), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                log = root / "run.log"
                log.write_text(
                    "# command: solver --seed 1\n"
                    + self._result().replace(f" {field}=", " removed="),
                    encoding="utf-8")
                self._write_manifest(
                    root, [log], command_texts=["solver --seed 1"])
                with self.assertRaisesRegex(ValueError, "钉住字段"):
                    plot_results.load_manifest_rows(root)

    def test_strict_manifest_path_rejects_max_evals_tampering(self) -> None:
        # 结果 max_evals 与绑定命令 --max-evals 不一致必须硬失败（审阅确认
        # 项 #6；兄弟工具 summarize_convergence 早已同口径）。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "run.log"
            log.write_text(
                "# command: solver --max-evals 100000 --seed 1\n"
                + self._full_result(max_evals="99999"),
                encoding="utf-8")
            self._write_manifest(
                root, [log],
                command_texts=["solver --max-evals 100000 --seed 1"])
            with self.assertRaisesRegex(ValueError, "max-evals"):
                plot_results.load_manifest_rows(root)

    def test_strict_manifest_path_rejects_fixed_budget_mismatch(self) -> None:
        # stop_on_target=0 的固定预算必须结束于完整一代边界
        # （--max-evals 100000 // particles 8 = 100000），evals=200 不可收。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "run.log"
            log.write_text(
                "# command: solver --max-evals 100000 --seed 1\n"
                + self._full_result(evals="200", stop_on_target="0",
                                    success="0", fes_to_target="200",
                                    stop_reason="evals"),
                encoding="utf-8")
            self._write_manifest(
                root, [log],
                command_texts=["solver --max-evals 100000 --seed 1"])
            with self.assertRaisesRegex(ValueError, "固定预算"):
                plot_results.load_manifest_rows(root)

    def test_strict_manifest_path_accepts_legal_early_stop(self) -> None:
        # stop_on_target=1 时 evals 可以小于完整预算（真实早停），必须放行。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "run.log"
            log.write_text(
                "# command: solver --max-evals 100000 --seed 1\n"
                + self._full_result(evals="16"),
                encoding="utf-8")
            self._write_manifest(
                root, [log],
                command_texts=["solver --max-evals 100000 --seed 1"])
            rows = plot_results.load_manifest_rows(root)
        self.assertEqual(len(rows), 1)

    def test_summary_groups_by_complete_configuration(self) -> None:
        common = {
            "implementation": "serial", "schema": "pso_v1",
            "function": "sphere", "particles": "8", "dim": "2",
            "max_evals": "40", "ranks": "1", "threads": "2",
            "target": "1e-8", "stop_on_target": "0",
        }
        rows = []
        for seed, best, elapsed, success, fes in (
                (1, 1.0, 1.0, 1, 8), (2, 2.0, 2.0, 1, 16),
                (3, 3.0, 3.0, 0, 40), (4, 4.0, 4.0, 0, 40)):
            rows.append(common | {
                "seed": str(seed), "best": str(best), "time": str(elapsed),
                "success": str(success), "fes_to_target": str(fes),
                "lower": "-100", "upper": "100",
            })
        rows.append(common | {
            "seed": "5", "best": "9", "time": "5", "success": "0",
            "fes_to_target": "40", "lower": "-5", "upper": "5",
        })

        records = plot_results.summary(rows)

        self.assertEqual(len(records), 2)
        main = next(record for record in records if record["lower"] == "-100")
        self.assertEqual(main["runs"], "4")
        self.assertEqual(main["best_mean"], "2.5")
        self.assertEqual(main["best_median"], "2.5")
        self.assertAlmostEqual(float(main["best_stdev"]), 1.2909944487358056)
        self.assertEqual(main["best_q1"], "1.75")
        self.assertEqual(main["best_q3"], "3.25")
        self.assertEqual(main["time_mean"], "2.5")
        self.assertAlmostEqual(float(main["time_stdev"]), 1.29099445)
        self.assertEqual(main["success_rate"], "0.5")
        self.assertEqual(main["fes_to_target_mean"], "26")
        self.assertAlmostEqual(float(main["fes_to_target_stdev"]),
                               16.492422502471)
        self.assertEqual(main["fes_to_target_median"], "28")
        self.assertEqual(main["fes_to_target_q1"], "14")
        self.assertEqual(main["fes_to_target_q3"], "40")

    def test_single_sample_stdev_and_quartiles_are_defined(self) -> None:
        record = plot_results.summary([{
            "implementation": "serial", "best": "2", "time": "0.1",
            "success": "1", "fes_to_target": "8",
        }])[0]
        self.assertEqual(record["best_stdev"], "0.0")
        self.assertEqual(record["best_q1"], "2")
        self.assertEqual(record["best_q3"], "2")
        self.assertEqual(record["time_stdev"], "0.0")
        self.assertEqual(record["fes_to_target_stdev"], "0.0")


if __name__ == "__main__":
    unittest.main()
