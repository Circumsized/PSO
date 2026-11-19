"""check_rank1_equivalence.py 的 mpiexec 命令组装与参数解析（标准库 unittest）。"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_rank1_equivalence as crq


class ParseFlagListTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(crq.parse_flag_list(""), [])

    def test_single_token(self) -> None:
        self.assertEqual(crq.parse_flag_list("--oversubscribe"),
                         ["--oversubscribe"])

    def test_semicolon_list(self) -> None:
        self.assertEqual(crq.parse_flag_list("-a;-b c"), ["-a", "-b c"])

    def test_empty_segments_dropped(self) -> None:
        self.assertEqual(crq.parse_flag_list(";;--x;;"), ["--x"])


class BuildMpiCommandTests(unittest.TestCase):
    def test_no_flags_matches_legacy_three_token_shape(self) -> None:
        cmd = crq.build_mpi_command(["mpiexec", "-n", "1"], [], [],
                                    "prog", ["--seed", "7"])
        self.assertEqual(cmd, ["mpiexec", "-n", "1", "prog", "--seed", "7"])

    def test_preflags_sit_between_count_and_executable(self) -> None:
        cmd = crq.build_mpi_command(["mpiexec", "-n", "1"],
                                    ["--oversubscribe"], [], "prog", [])
        self.assertEqual(cmd, ["mpiexec", "-n", "1", "--oversubscribe", "prog"])

    def test_postflags_sit_after_executable_before_solver_args(self) -> None:
        cmd = crq.build_mpi_command(["mpiexec", "-n", "1"], [],
                                    ["--mca", "x"], "prog", ["--seed", "7"])
        self.assertEqual(cmd, ["mpiexec", "-n", "1", "prog",
                               "--mca", "x", "--seed", "7"])


class MainFlagsPassthroughTests(unittest.TestCase):
    """非空 flags 必须真实到达 MPI 启动行：main() 里解析漏传在这立刻失败。"""

    ROW = {"record": "result", "schema": "pso_v1",
           "timing_scope": "solver", "best": "1", "iterations": "3",
           "evals": "40", "implementation": "x", "ranks": "1",
           "threads": "1", "elapsed_max_s": "0.001"}

    @classmethod
    def run_main_capturing(cls, argv: list[str]) -> tuple[int, list[list[str]]]:
        """跑 main()，返回退出码与每次 run() 收到的 argv；stdout 静音。"""
        captured: list[list[str]] = []

        def fake_run(argv: list[str]) -> dict[str, str]:
            captured.append(argv)
            return cls.ROW

        with mock.patch.object(crq, "run", side_effect=fake_run), \
                contextlib.redirect_stdout(io.StringIO()):
            code = crq.main(argv)
        return code, captured

    def test_preflags_and_postflags_reach_mpi_launch(self) -> None:
        code, captured = self.run_main_capturing([
            "--serial", "serial.exe", "--mpi", "mpi.exe",
            "--mpiexec", "mpiexec", "-n", "1",
            "--mpiexec-pre", "--oversubscribe",
            "--mpiexec-post", "--mca;x",
            "--", "--function", "sphere", "--seed", "7"])
        self.assertEqual(code, 0)
        self.assertEqual(captured[0],
                         ["serial.exe", "--function", "sphere", "--seed", "7"])
        self.assertEqual(captured[1],
                         ["mpiexec", "-n", "1", "--oversubscribe", "mpi.exe",
                          "--mca", "x", "--function", "sphere", "--seed", "7"])

    def test_omitted_flags_keep_legacy_shape(self) -> None:
        code, captured = self.run_main_capturing([
            "--serial", "serial.exe", "--mpi", "mpi.exe",
            "--mpiexec", "mpiexec", "-n", "1",
            "--", "--seed", "7"])
        self.assertEqual(code, 0)
        self.assertEqual(captured[1],
                         ["mpiexec", "-n", "1", "mpi.exe", "--seed", "7"])


if __name__ == "__main__":
    unittest.main()
