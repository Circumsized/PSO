"""Unit tests for analyze_convergence (standard library unittest only)."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze_convergence as conv  # noqa: E402


def trajectory(evals_bests: list[tuple[int, float]]) -> conv.Trajectory:
    return [(i, e, b) for i, (e, b) in enumerate(evals_bests)]


class LoadHistoryTests(unittest.TestCase):
    def test_parses_and_validates_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.history"
            path.write_text(
                "iteration=0 evals=100 best=5\n"
                "# comment\n"
                "iteration=1 evals=200 best=3\n"
                "iteration=2 evals=300 best=3\n",
                encoding="utf-8")
            loaded = conv.load_history(path)
            self.assertEqual(loaded, [(0, 100, 5.0), (1, 200, 3.0), (2, 300, 3.0)])

    def test_rejects_increasing_best_and_bad_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bad_best = Path(directory) / "up.history"
            bad_best.write_text("iteration=0 evals=100 best=1\n"
                                "iteration=1 evals=200 best=2\n", encoding="utf-8")
            bad_line = Path(directory) / "junk.history"
            bad_line.write_text("hello\n", encoding="utf-8")
            bad_evals = Path(directory) / "repeat.history"
            bad_evals.write_text("iteration=0 evals=100 best=1\n"
                                 "iteration=1 evals=100 best=1\n", encoding="utf-8")
            empty = Path(directory) / "empty.history"
            empty.write_text("", encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                for path in (bad_best, bad_line, bad_evals, empty):
                    with self.assertRaises(ValueError):
                        conv.load_history(path)


class BestAtEvalsTests(unittest.TestCase):
    def test_step_function_semantics(self) -> None:
        traj = trajectory([(100, 9.0), (200, 5.0), (300, 2.0)])
        # 固定批评估：evals=150 时只完成了 100 次评估，best-so-far 仍为 9.0。
        self.assertEqual(
            conv.best_at_evals(traj, [50, 100, 150, 250, 300, 1000]),
            {50: 9.0, 100: 9.0, 150: 9.0, 250: 5.0, 300: 2.0, 1000: 2.0})


class FirstHitAndERTTests(unittest.TestCase):
    def test_first_hit_returns_first_reaching_evals(self) -> None:
        traj = trajectory([(100, 9.0), (200, 5.0), (300, 1.0)])
        self.assertEqual(conv.first_hit_evals(traj, 5.0), 200)
        self.assertEqual(conv.first_hit_evals(traj, 0.5), None)

    def test_success_summary_ert_formula(self) -> None:
        trajs = [
            trajectory([(100, 9.0), (200, 0.5)]),
            trajectory([(100, 8.0), (200, 7.0)]),
            trajectory([(100, 0.5)]),
        ]
        summary = conv.success_summary(trajs, target=1.0, evals_budget=400)
        self.assertEqual(summary["n_success"], 2)
        self.assertAlmostEqual(summary["success_rate"], 2 / 3)
        self.assertAlmostEqual(summary["ert"], 400 * 3 / 2)
        self.assertEqual(summary["median_hit_evals"], 150)

    def test_success_summary_without_success_has_none_ert(self) -> None:
        traj = trajectory([(100, 9.0)])
        summary = conv.success_summary([traj], target=0.1, evals_budget=100)
        self.assertEqual(summary["n_success"], 0)
        self.assertIsNone(summary["ert"])
        self.assertIsNone(summary["median_hit_evals"])

    def test_rejects_bad_budget(self) -> None:
        traj = trajectory([(100, 9.0)])
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                conv.success_summary([traj], target=1.0, evals_budget=0)


if __name__ == "__main__":
    unittest.main()
