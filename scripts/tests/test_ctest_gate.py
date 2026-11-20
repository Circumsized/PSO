"""ctest_gate.py 的契约测试（标准库 unittest）。"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

GATE = Path(__file__).resolve().parent / "ctest_gate.py"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ctest_gate  # noqa: E402


def run_gate(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GATE), "--", *arguments],
                          capture_output=True, text=True)


class CtestGateTests(unittest.TestCase):
    def test_success_passes_output_through(self) -> None:
        proc = run_gate(sys.executable, "-c",
                        "print('record=result best=1')")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("record=result best=1", proc.stdout)
        self.assertNotIn("PsoTestGate:", proc.stderr)

    def test_nonzero_exit_is_mirrored_with_sentinel(self) -> None:
        proc = run_gate(sys.executable, "-c",
                        "print('record=result best=1'); raise SystemExit(7)")
        self.assertEqual(proc.returncode, 7)
        self.assertIn("record=result best=1", proc.stdout)
        self.assertIn("PsoTestGate:", proc.stderr)

    def test_recovered_diagnostic_passes_through_for_fail_regex(self) -> None:
        # UBSan 恢复诊断走 stderr；包装器不吞它，CMake 的 FAIL 正则才有牙。
        proc = run_gate(sys.executable, "-c",
                        "import sys; print('runtime error: synthetic', "
                        "file=sys.stderr)")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("runtime error", proc.stderr)

    def test_missing_command_is_gate_error(self) -> None:
        proc = subprocess.run([sys.executable, str(GATE)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("PsoTestGate:", proc.stderr)

    def test_unknown_executable_is_gate_error(self) -> None:
        proc = run_gate("definitely-not-a-real-executable-xyz")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("PsoTestGate: 无法启动子进程", proc.stderr)


class ExitCodeNormalizationTests(unittest.TestCase):
    """退出码归一化与超时路径（mock 子进程，跨平台确定）。

    Windows 进程可以带任意 32 位退出码结束（如 exit(700)），POSIX 上被
    信号杀死时 returncode 为负；CTest 只接受 0..255，原样透传既会误判
    通过也会把信号退出显示成失败码，包装器必须统一收敛为 1。真实
    SystemExit(700) 在 POSIX 会被截断成 188，无法稳定复现，故用 mock
    直接钉住归一化契约。
    """

    @staticmethod
    def run_gate_with(result=None, side_effect=None) -> int:
        argv = ["ctest_gate.py", "--", "child"]
        with mock.patch.object(ctest_gate.subprocess, "run",
                               return_value=result,
                               side_effect=side_effect), \
                mock.patch.object(sys, "argv", argv):
            return ctest_gate.main()

    def test_exit_code_at_least_256_collapses_to_one(self) -> None:
        code = self.run_gate_with(
            result=subprocess.CompletedProcess(["child"], 700))
        self.assertEqual(code, 1)

    def test_signal_exit_code_collapses_to_one(self) -> None:
        code = self.run_gate_with(
            result=subprocess.CompletedProcess(["child"], -15))
        self.assertEqual(code, 1)

    def test_timeout_is_gate_failure(self) -> None:
        code = self.run_gate_with(side_effect=subprocess.TimeoutExpired(
            ["child"], ctest_gate.CHILD_TIMEOUT_S))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
