from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import run_experiments as runner  # noqa: E402


class RunExperimentsTests(unittest.TestCase):
    def config(self, **overrides) -> runner.MatrixConfig:
        values = {
            "functions": ("sphere", "ackley"),
            "particles": 120,
            "particles_per_rank": 25,
            "dim": 8,
            "max_evals": 1000,
            "ranks": (2, 4),
            "threads": (2, 3),
            "boundaries": ("reflect",),
            "dynamics": ("constriction",),
            "migration_intervals": (5,),
            "mode": "strong",
        }
        values.update(overrides)
        return runner.MatrixConfig(**values)

    def test_strong_has_only_full_mpi_matrix(self) -> None:
        experiments = runner.build_experiment_matrix(self.config())
        self.assertEqual(len(experiments), 16)
        self.assertNotIn("serial", {item.implementation for item in experiments})
        self.assertEqual(
            {item.implementation for item in experiments},
            {"mpi-ms", "mpi-island"})
        self.assertEqual({item.ranks for item in experiments}, {2, 4})
        self.assertEqual({item.threads for item in experiments}, {2, 3})

    def test_baseline_has_one_serial_per_function_and_no_mpi(self) -> None:
        experiments = runner.build_experiment_matrix(self.config(mode="baseline"))
        self.assertEqual(len(experiments), 2)
        self.assertEqual(
            {item.implementation for item in experiments}, {"serial"})
        self.assertEqual({item.function for item in experiments}, {"sphere", "ackley"})
        self.assertEqual({(item.ranks, item.threads) for item in experiments}, {(1, 2)})

    def test_weak_scaling_derives_total_particles_and_budget_per_rank(self) -> None:
        experiments = runner.build_experiment_matrix(
            self.config(mode="weak", functions=("sphere",), threads=(1,)))
        self.assertEqual(len(experiments), 4)
        totals = {(item.ranks, item.particles, item.max_evals) for item in experiments}
        self.assertEqual(totals, {(2, 50, 2000), (4, 100, 4000)})
        self.assertNotIn("serial", {item.implementation for item in experiments})

    def test_matrix_rejects_non_positive_or_non_python_integers(self) -> None:
        invalid_configs = (
            self.config(particles=0),
            self.config(max_evals=1000.0),
            self.config(ranks=(True,)),
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                with self.assertRaisesRegex(ValueError, "Python 整数"):
                    runner.build_experiment_matrix(config)

    def test_weak_budget_still_must_cover_scaled_particles(self) -> None:
        with self.assertRaisesRegex(ValueError, "max-evals 40 小于粒子数 50"):
            runner.build_experiment_matrix(self.config(
                mode="weak", functions=("sphere",), threads=(1,),
                particles_per_rank=25, max_evals=20))

    def test_all_implementation_commands_disable_early_stop(self) -> None:
        executables = runner.Executables(
            Path("build/pso_serial"), Path("build/pso_mpi_ms"),
            Path("build/pso_mpi_island"), "mpiexec")
        experiments = [
            runner.Experiment(
                "baseline", "serial", "ackley", 120, 8, 1000,
                1, 3, "reinit", "linear-inertia", 0),
            runner.Experiment(
                "strong", "mpi-ms", "ackley", 120, 8, 1000,
                4, 3, "reinit", "linear-inertia", 0),
            runner.Experiment(
                "ablation", "mpi-island", "ackley", 120, 8, 1000,
                4, 3, "reinit", "linear-inertia", 7),
        ]
        commands = [
            runner.experiment_command(experiment, 42, executables)
            for experiment in experiments
        ]
        for command in commands:
            self.assertIn("--no-early-stop", command)
            self.assertIn("--linear-inertia", command)
        self.assertEqual(
            commands[2][:4],
            ["mpiexec", "-n", "4", str(Path("build/pso_mpi_island"))])
        self.assertEqual(commands[2][-2:], ["--migration-interval", "7"])
        for experiment in experiments:
            self.assertEqual(
                runner.command_environment(experiment),
                {
                    "OMP_NUM_THREADS": "3",
                    "OMP_DYNAMIC": "FALSE",
                    "OMP_PROC_BIND": "FALSE",
                    "MPIEXEC_AFFINITY": "0",
                })

    def test_process_environment_removes_inherited_tuning(self) -> None:
        experiment = runner.Experiment(
            "strong", "mpi-ms", "sphere", 120, 8, 1000,
            4, 3, "reflect", "constriction", 0)
        inherited = {
            name: "polluted" for name in runner.INHERITED_ENVIRONMENT_BLOCKLIST
        }
        inherited.update({
            "PATH": "kept",
            "OMP_NUM_THREADS": "99",
            "OMP_DYNAMIC": "TRUE",
            "OMP_PROC_BIND": "TRUE",
            "MPIEXEC_AFFINITY": "1",
        })
        environment = runner.isolated_process_environment(experiment, inherited)
        self.assertEqual(environment["PATH"], "kept")
        for name in runner.INHERITED_ENVIRONMENT_BLOCKLIST:
            self.assertNotIn(name, environment)
        self.assertEqual(environment["OMP_NUM_THREADS"], "3")
        self.assertEqual(environment["OMP_DYNAMIC"], "FALSE")
        self.assertEqual(environment["OMP_PROC_BIND"], "FALSE")
        self.assertEqual(environment["MPIEXEC_AFFINITY"], "0")

    def test_executable_metadata_hashes_local_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            serial = root / "pso_serial.exe"
            mpi_ms = root / "pso_mpi_ms.exe"
            mpi_island = root / "pso_mpi_island.exe"
            payloads = (b"serial", b"master-worker", b"island")
            for path, payload in zip((serial, mpi_ms, mpi_island), payloads):
                path.write_bytes(payload)
            metadata = runner.executables_metadata(runner.Executables(
                serial, mpi_ms, mpi_island, "missing-mpiexec-command"))
            for name, path, payload in zip(
                    ("serial", "mpi_ms", "mpi_island"),
                    (serial, mpi_ms, mpi_island), payloads):
                entry = metadata[name]
                self.assertEqual(entry["path"], str(path.resolve()))
                self.assertEqual(entry["size"], len(payload))
                self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest())
                self.assertIn("mtime_ns", entry)
            self.assertFalse(metadata["mpiexec"]["resolved"])
            self.assertNotIn("sha256", metadata["mpiexec"])
            self.assertTrue(Path(metadata["mpiexec"]["path"]).is_absolute())

    def test_log_name_and_windows_executable_suffix(self) -> None:
        experiment = runner.Experiment(
            "strong", "mpi-ms", "sphere", 120, 8, 1000,
            4, 3, "reflect", "constriction", 0)
        self.assertEqual(
            runner.log_filename(experiment),
            "strong__mpi_ms__fn-sphere__p-120__d-8__r-4__t-3"
            "__b-reflect__dyn-constriction__mi-0.log")
        self.assertEqual(
            runner.executable_path(Path("build"), "pso_serial", windows=True),
            Path("build/pso_serial.exe"))
        self.assertEqual(
            runner.executable_path(Path("build"), "pso_serial", windows=False),
            Path("build/pso_serial"))

    def test_history_flag_wires_per_run_sidecar(self) -> None:
        experiment = runner.Experiment(
            "baseline", "serial", "sphere", 120, 8, 1000,
            1, 1, "reflect", "constriction", 0)
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            arguments = [
                "--output-dir", str(output_dir), "--runs", "2",
                "--functions", "sphere", "--particles", "120",
                "--dim", "8", "--max-evals", "1000",
                "--threads", "1", "--mode", "baseline",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(arguments), 0)
            plain = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(all(
                "--history" not in command["command"]
                for command in plain["commands"]))
            self.assertTrue(all(
                command.get("history") is None
                for command in plain["commands"]))

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    runner.main([*arguments, "--history"]), 0)
            traced = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8"))
            for command in traced["commands"]:
                expected_sidecar = (
                    f"{command['log']}.seed-{command['seed']}.history")
                self.assertEqual(command["history"], expected_sidecar)
                self.assertIn("--history", command["command"])
                self.assertEqual(
                    command["command"][command["command"].index("--history") + 1],
                    expected_sidecar)

    def test_append_with_history_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arguments = [
                "--output-dir", str(directory), "--execute", "--append",
                "--history", "--runs", "1",
                "--functions", "sphere", "--particles", "120",
                "--dim", "8", "--max-evals", "1000",
                "--threads", "1", "--mode", "baseline",
            ]
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as raised:
                    runner.main(arguments)
            self.assertEqual(raised.exception.code, 2)
            message = stderr.getvalue()
            self.assertIn("--history", message)
            self.assertIn("--append", message)

    def test_existing_sidecar_blocks_execution_without_append(self) -> None:
        experiment = runner.Experiment(
            "baseline", "serial", "sphere", 120, 8, 1000,
            1, 1, "reflect", "constriction", 0)
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            sidecar = Path(f"{output_dir / runner.log_filename(experiment)}"
                           f".seed-0.history")
            sidecar.write_text("iteration=0 evals=120 best=9\n",
                               encoding="utf-8")
            arguments = [
                "--output-dir", str(output_dir), "--execute", "--history",
                "--seed-base", "0", "--runs", "1",
                "--functions", "sphere", "--particles", "120",
                "--dim", "8", "--max-evals", "1000",
                "--threads", "1", "--mode", "baseline",
            ]
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as raised:
                    runner.main(arguments)
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("sidecar", stderr.getvalue())
            self.assertIn(sidecar.name, stderr.getvalue())
            # dry-run 不受已有 sidecar 影响
            dry_arguments = [a for a in arguments if a != "--execute"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(dry_arguments), 0)

    def test_pin_flag_wires_pin_cpus_and_manifest(self) -> None:
        experiment = runner.Experiment(
            "strong", "mpi-ms", "sphere", 120, 8, 1000,
            4, 3, "reflect", "constriction", 0)
        executables = runner.Executables(
            Path("build/pso_serial"), Path("build/pso_mpi_ms"),
            Path("build/pso_mpi_island"), "mpiexec")
        plain = runner.experiment_command(experiment, 1, executables)
        pinned = runner.experiment_command(experiment, 1, executables, pin=True)
        self.assertNotIn("--pin-cpus", plain)
        self.assertIn("--pin-cpus", pinned)
        self.assertNotIn("--pin-cpus", pinned[:pinned.index("--seed")])

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            arguments = [
                "--output-dir", str(output_dir), "--runs", "1",
                "--functions", "sphere", "--particles", "120",
                "--dim", "8", "--max-evals", "1000",
                "--threads", "1", "--mode", "baseline",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main([*arguments, "--pin"]), 0)
            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["configuration"]["pin"])
            self.assertIn("--pin-cpus", manifest["commands"][0]["command"])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(arguments), 0)
            plain_manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(plain_manifest["configuration"]["pin"])
            self.assertNotIn("--pin-cpus", plain_manifest["commands"][0]["command"])

    def test_nonempty_log_only_blocks_execution_without_append(self) -> None:
        experiment = runner.Experiment(
            "strong", "mpi-ms", "sphere", 120, 8, 1000,
            4, 3, "reflect", "constriction", 0)
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            log_path = output_dir / runner.log_filename(experiment)
            log_path.write_text("old sample\n", encoding="utf-8")
            arguments = [
                "--output-dir", str(output_dir), "--runs", "1",
                "--functions", "sphere", "--particles", "120",
                "--dim", "8", "--max-evals", "1000",
                "--ranks", "4", "--threads", "3", "--mode", "strong",
            ]

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(arguments), 0)
            dry_manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(dry_manifest["dry_run"])
            self.assertFalse(dry_manifest["configuration"]["append"])
            self.assertEqual(
                set(dry_manifest["executables"]),
                {"serial", "mpi_ms", "mpi_island", "mpiexec"})
            self.assertTrue(all(
                command["status"] == "planned"
                for command in dry_manifest["commands"]))

            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as raised:
                    runner.main([*arguments, "--execute"])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("--append", stderr.getvalue())

            completed = subprocess.CompletedProcess([], 0)
            snapshots: list[list[str]] = []
            original_write = runner.write_manifest_atomic

            def capture_write(path, manifest):
                snapshots.append([
                    record["status"]
                    for record in copy.deepcopy(manifest["commands"])
                ])
                original_write(path, manifest)

            polluted = {
                name: "polluted" for name in runner.INHERITED_ENVIRONMENT_BLOCKLIST
            }
            with mock.patch.dict(runner.os.environ, polluted, clear=False):
                with mock.patch.object(
                        runner, "write_manifest_atomic",
                        side_effect=capture_write):
                    with mock.patch.object(
                            runner.subprocess, "run",
                            return_value=completed) as run:
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(runner.main(
                                [*arguments, "--execute", "--append"]), 0)
            executed_manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(executed_manifest["dry_run"])
            self.assertTrue(executed_manifest["configuration"]["append"])
            self.assertEqual(executed_manifest["log_policy"], "append")
            self.assertEqual(snapshots[0], ["planned", "planned"])
            self.assertIn(["running", "planned"], snapshots)
            self.assertEqual(snapshots[-1], ["completed", "completed"])
            self.assertTrue(all(
                record["returncode"] == 0
                for record in executed_manifest["commands"]))
            for call in run.call_args_list:
                command = call.args[0] if call.args else []
                if "--version" in command:
                    continue  # mpiexec 版本探测是诊断调用，不执行求解器
                environment = call.kwargs["env"]
                for name in runner.INHERITED_ENVIRONMENT_BLOCKLIST:
                    self.assertNotIn(name, environment)
                self.assertEqual(environment["OMP_NUM_THREADS"], "3")
                self.assertEqual(environment["OMP_DYNAMIC"], "FALSE")
                self.assertEqual(environment["OMP_PROC_BIND"], "FALSE")
                self.assertEqual(environment["MPIEXEC_AFFINITY"], "0")

    def test_manifest_write_retries_transient_windows_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            original_replace = Path.replace
            calls = {"n": 0}

            def flaky_replace(self, target):
                calls["n"] += 1
                if calls["n"] <= 2:
                    raise PermissionError(5, "denied")
                return original_replace(self, target)

            with mock.patch.object(Path, "replace", autospec=True,
                                   side_effect=flaky_replace):
                with mock.patch.object(runner.time, "sleep") as slept:
                    runner.write_manifest_atomic(path, {"schema": "x"})
            self.assertEqual(calls["n"], 3)
            self.assertEqual(slept.call_count, 2)
            self.assertTrue(path.is_file())

    def test_manifest_write_raises_after_exhausted_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            denied = PermissionError(5, "denied")
            with mock.patch.object(Path, "replace", side_effect=denied):
                with mock.patch.object(runner.time, "sleep"):
                    with self.assertRaises(PermissionError):
                        runner.write_manifest_atomic(path, {"schema": "x"})

    def test_failed_mocked_execution_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            arguments = [
                "--output-dir", str(output_dir), "--execute", "--runs", "1",
                "--functions", "sphere", "--particles", "120",
                "--dim", "8", "--max-evals", "1000",
                "--threads", "3", "--mode", "baseline",
            ]
            completed = subprocess.CompletedProcess([], 7)
            with mock.patch.object(
                    runner.subprocess, "run", return_value=completed):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(runner.main(arguments), 7)
            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["commands"]), 1)
            self.assertEqual(manifest["commands"][0]["status"], "failed")
            self.assertEqual(manifest["commands"][0]["returncode"], 7)


    def _baseline_args(self, output_dir: Path) -> list[str]:
        return [
            "--output-dir", str(output_dir), "--runs", "1",
            "--functions", "sphere", "--particles", "120",
            "--dim", "8", "--max-evals", "1000",
            "--threads", "1", "--mode", "baseline",
        ]

    @staticmethod
    def _executed_manifest() -> dict:
        return {
            "schema": "pso_experiments_v1",
            "dry_run": False,
            "commands": [{"status": "completed", "returncode": 0,
                          "seed": 1, "log": "x.log"}],
        }

    def test_dry_run_refuses_to_overwrite_executed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            manifest_path = output_dir / "manifest.json"
            executed = self._executed_manifest()
            manifest_path.write_text(json.dumps(executed), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as raised:
                    runner.main(self._baseline_args(output_dir))
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("真实执行", stderr.getvalue())
            # 字节级不变：既有证据一个字节都不能动。
            self.assertEqual(
                json.loads(manifest_path.read_text(encoding="utf-8")), executed)

    def test_execute_without_append_rejects_executed_manifest_without_logs(self) -> None:
        # 日志被手工删除后，日志 guard 会漏网；manifest guard 必须接住。
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            (output_dir / "manifest.json").write_text(
                json.dumps(self._executed_manifest()), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as raised:
                    runner.main([*self._baseline_args(output_dir), "--execute"])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("真实执行", stderr.getvalue())

    def test_execute_append_may_replace_executed_manifest(self) -> None:
        # v3 的 --execute --append 追加重跑是已登记流程，必须豁免。
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            (output_dir / "manifest.json").write_text(
                json.dumps(self._executed_manifest()), encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0)
            with mock.patch.object(runner.subprocess, "run",
                                   return_value=completed):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        runner.main([*self._baseline_args(output_dir),
                                     "--execute", "--append"]), 0)
            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["dry_run"])

    def test_unparsable_manifest_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            manifest_path = output_dir / "manifest.json"
            manifest_path.write_text("{not json", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as raised:
                    runner.main(self._baseline_args(output_dir))
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("无法解析", stderr.getvalue())
            self.assertEqual(manifest_path.read_text(encoding="utf-8"),
                             "{not json")


    def test_manifest_survives_platform_probe_failure(self) -> None:
        # platform.platform() 在部分环境（GH runner 的 Python 3.12）会因
        # platform.processor() 的子进程返回空而抛 AttributeError；manifest
        # 的主机信息不能因此中断整个实验计划。
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with mock.patch.object(runner.platform, "platform",
                                   side_effect=AttributeError("no uname")):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        runner.main(self._baseline_args(output_dir)), 0)
            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["host"]["platform"], sys.platform)


if __name__ == "__main__":
    unittest.main()
