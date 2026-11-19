"""ctest_gate.py 的契约测试（标准库 unittest）。"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

GATE = Path(__file__).resolve().parent / "ctest_gate.py"


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


if __name__ == "__main__":
    unittest.main()
