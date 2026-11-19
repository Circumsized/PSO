"""Unit tests for scripts/analyze_stats.py (bootstrap CI + Mann-Whitney U)."""
from __future__ import annotations

import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import analyze_stats as stats  # noqa: E402


class BootstrapRatioTests(unittest.TestCase):
    def test_ratio_of_identical_samples_is_one_with_tight_ci(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        point, low, high = stats.bootstrap_ratio_ci(values, list(values))
        self.assertAlmostEqual(point, 1.0, places=12)
        self.assertAlmostEqual(low, 1.0, places=12)
        self.assertAlmostEqual(high, 1.0, places=12)

    def test_ratio_halved_when_candidate_is_double(self) -> None:
        base = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        cand = [2.0 * v for v in base]
        point, low, high = stats.bootstrap_ratio_ci(base, cand, iterations=2000)
        self.assertAlmostEqual(point, 0.5, places=12)
        self.assertLessEqual(low, point + 1e-9)  # CI brackets point
        self.assertGreaterEqual(high, point - 1e-9)

    def test_deterministic_for_fixed_seed(self) -> None:
        base = [3.1, 4.2, 5.3, 6.4, 7.5, 8.6, 9.7, 1.2, 2.3, 4.5]
        cand = [2.9, 4.0, 5.1, 6.9, 7.1, 8.0, 9.1, 1.5, 2.8, 4.9]
        first = stats.bootstrap_ratio_ci(base, cand, iterations=500)
        second = stats.bootstrap_ratio_ci(base, cand, iterations=500)
        self.assertEqual(first, second)

    def test_mismatched_lengths_rejected(self) -> None:
        with self.assertRaises(ValueError):
            stats.bootstrap_ratio_ci([1.0, 2.0], [1.0])

    def test_small_resample_counts_and_singletons(self) -> None:
        for iterations in (1, 2, 3):
            with self.subTest(iterations=iterations):
                self.assertEqual(stats.bootstrap_ratio_ci(
                    [1.0], [2.0], iterations=iterations), (0.5, 0.5, 0.5))

    def test_linear_percentiles_for_narrow_confidence(self) -> None:
        # seed=1 的五次配对重采样比值排序为 [0.5, 0.5, 0.6, 0.6, 0.6]。
        point, low, high = stats.bootstrap_ratio_ci(
            [1.0, 3.0, 20.0], [2.0, 5.0, 7.0],
            iterations=5, confidence=0.01, seed=1)
        self.assertEqual(point, 0.6)
        self.assertAlmostEqual(low, 0.598, places=14)
        self.assertEqual(high, 0.6)

    def test_invalid_iteration_counts(self) -> None:
        for count in (0, -1, True, 1.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                stats.bootstrap_ratio_ci([1.0], [2.0], iterations=count)

    def test_ratio_requires_positive_finite_samples(self) -> None:
        for bad in (0.0, -1.0, math.nan, math.inf, -math.inf):
            for baseline, candidate in (([bad, 1.0], [2.0, 3.0]),
                                        ([2.0, 3.0], [bad, 1.0])):
                with self.subTest(bad=bad, baseline=baseline), self.assertRaises(ValueError):
                    stats.bootstrap_ratio_ci(baseline, candidate, iterations=10)

    def test_unrepresentable_ratio_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            stats.bootstrap_ratio_ci([1e308], [1e-308], iterations=10)

    def test_large_finite_median_is_not_overflowed(self) -> None:
        self.assertEqual(stats.bootstrap_ratio_ci(
            [1e308, 1e308], [1e308, 1e308], iterations=10), (1.0, 1.0, 1.0))

    def test_ci_brackets_point_estimate_on_real_like_data(self) -> None:
        # 20 paired samples with noise: CI must contain the point estimate
        base = [10.0 + 0.5 * ((i * 7) % 11) for i in range(20)]
        cand = [b * 0.6 + 0.1 * ((i * 13) % 9) for i, b in enumerate(base)]
        point, low, high = stats.bootstrap_ratio_ci(base, cand, iterations=2000)
        self.assertLessEqual(low, point + 1e-9)
        self.assertGreaterEqual(high, point - 1e-9)
        self.assertGreater(point, 1.4)  # candidate ~0.6x of baseline


class MannWhitneyTests(unittest.TestCase):
    def test_completely_separated_distributions(self) -> None:
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [6.0, 7.0, 8.0, 9.0, 10.0]
        u, z, p = stats.mann_whitney_u(a, b)
        self.assertAlmostEqual(u, 0.0)
        self.assertLess(z, -1.5)
        self.assertLess(p, 0.05)

    def test_identical_samples_not_significant(self) -> None:
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        u, z, p = stats.mann_whitney_u(a, list(a))
        self.assertAlmostEqual(p, 1.0, places=6)
        self.assertAlmostEqual(z, 0.0, places=6)

    def test_ties_get_average_ranks(self) -> None:
        # heavy ties: variance correction must keep sigma > 0 and p defined
        a = [1.0, 1.0, 1.0, 2.0, 2.0]
        b = [1.0, 1.0, 2.0, 2.0, 2.0]
        u, z, p = stats.mann_whitney_u(a, b)
        self.assertEqual(u, 10.0)
        self.assertAlmostEqual(z, -0.48, places=12)
        self.assertAlmostEqual(p, 0.6312273930324452, places=12)

    def test_symmetry_of_u(self) -> None:
        a = [1.0, 3.0, 5.0, 7.0, 9.0]
        b = [2.0, 4.0, 6.0, 8.0, 10.0]
        u1, _, p1 = stats.mann_whitney_u(a, b)
        u2, _, p2 = stats.mann_whitney_u(b, a)
        self.assertEqual(u1, u2)
        self.assertAlmostEqual(p1, p2)

    def test_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            stats.mann_whitney_u([], [1.0])

    def test_nonfinite_samples_rejected(self) -> None:
        for expression in (
            "stats.mann_whitney_u([1.0, bad], [2.0, 3.0])",
            "stats.mann_whitney_u([2.0, 3.0], [1.0, bad])",
            "stats.wilcoxon_signed_rank([1.0, bad])",
        ):
            code = (
                "import math, analyze_stats as stats\n"
                "for bad in (math.nan, math.inf, -math.inf):\n"
                "    try:\n"
                f"        {expression}\n"
                "    except ValueError:\n"
                "        continue\n"
                "    raise SystemExit('nonfinite sample accepted')\n"
            )
            with self.subTest(expression=expression):
                try:
                    proc = subprocess.run(
                        [sys.executable, "-c", code],
                        cwd=Path(stats.__file__).parent,
                        capture_output=True, text=True, timeout=5)
                except subprocess.TimeoutExpired:
                    self.fail("nonfinite sample hung the rank calculation")
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class WilcoxonSignedRankTests(unittest.TestCase):
    def test_all_differences_same_direction(self) -> None:
        # 此处五个差值没有并列秩，不能推广为所有小样本的 p 值下界。
        t, z, p = stats.wilcoxon_signed_rank([-1.0, -2.0, -3.0, -4.0, -5.0])
        self.assertAlmostEqual(t, 0.0)
        self.assertLess(z, 0.0)
        self.assertGreater(p, 0.05)  # documented floor: n = 5 is p = 0.0591
        t8, z8, p8 = stats.wilcoxon_signed_rank(
            [-float(v) for v in range(1, 9)])
        self.assertAlmostEqual(t8, 0.0)
        self.assertLess(z8, 0.0)
        self.assertLess(p8, 0.05)

    def test_known_reference_values(self) -> None:
        # ranks 1..4 over |d|; w+ = 1+2+3 = 6, w- = 4 -> T = 4
        # mean = 4*5/4 = 5, sigma = sqrt(4*5*9/24) = sqrt(7.5)
        # continuity-corrected z = (4 - 5 + 0.5) / sqrt(7.5)
        t, z, p = stats.wilcoxon_signed_rank([0.1, 0.2, 0.3, -0.4])
        self.assertAlmostEqual(t, 4.0)
        expected_z = -0.5 / math.sqrt(7.5)
        self.assertAlmostEqual(z, expected_z, places=10)
        self.assertAlmostEqual(p, math.erfc(abs(expected_z) / math.sqrt(2.0)),
                               places=12)

    def test_zero_differences_are_dropped(self) -> None:
        # nz = [+1, -1] share one tied rank, so T sits exactly on its
        # expectation: z must be 0 and p exactly 1
        t, z, p = stats.wilcoxon_signed_rank([0.0, 1.0, -1.0, 0.0])
        self.assertAlmostEqual(t, 1.5)
        self.assertAlmostEqual(z, 0.0, places=9)
        self.assertAlmostEqual(p, 1.0, places=9)

    def test_sign_flip_leaves_statistic_unchanged(self) -> None:
        diffs = [0.5, -1.5, 2.5, -3.5, 4.5]
        t1, _, p1 = stats.wilcoxon_signed_rank(diffs)
        t2, _, p2 = stats.wilcoxon_signed_rank([-d for d in diffs])
        self.assertAlmostEqual(t1, t2)
        self.assertAlmostEqual(p1, p2)

    def test_all_zero_rejected(self) -> None:
        with self.assertRaises(ValueError):
            stats.wilcoxon_signed_rank([0.0, 0.0, 0.0])

    def test_five_tied_differences_pin_normal_approximation(self) -> None:
        t, z, p = stats.wilcoxon_signed_rank([1.0] * 5)
        self.assertEqual(t, 0.0)
        self.assertAlmostEqual(z, -2.0869967789998034, places=12)
        self.assertAlmostEqual(p, 0.03688842570704988, places=12)

    def test_paired_design_detects_shift_rank_sum_misses(self) -> None:
        # A uniform improvement riding on a wide between-seed spread is
        # invisible to the independent-samples rank-sum but obvious to the
        # paired test -- exactly why the formal matrix needs this one.
        base = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]
        cand = [value - 1.0 for value in base]
        _, _, p_ranksum = stats.mann_whitney_u(base, cand)
        _, _, p_paired = stats.wilcoxon_signed_rank(
            [c - b for b, c in zip(base, cand)])
        self.assertGreater(p_ranksum, 0.05)
        self.assertLess(p_paired, 0.05)


class PairingTests(unittest.TestCase):
    def test_paired_by_seed_orders_by_seed(self) -> None:
        baseline = [(7, 0.5), (8, 0.6), (9, 0.7)]
        candidate = [(9, 1.5), (7, 1.4), (8, 1.6)]
        pairs = stats.paired_by_seed(baseline, candidate)
        self.assertEqual([(b, c) for b, c in pairs],
                         [(0.5, 1.4), (0.6, 1.6), (0.7, 1.5)])

    def test_seed_mismatch_rejected(self) -> None:
        with self.assertRaises(ValueError):
            stats.paired_by_seed([(1, 1.0)], [(2, 1.0)])


class LoaderGuardTests(unittest.TestCase):
    """重复 seed 必须在共享 loader 处硬失败：dict 后写覆盖会把被 --append
    污染的日志悄悄折叠成 3 行，产出貌似可信的配对统计。"""

    LOG = (
        "record=result schema=pso_v1 implementation=serial function=sphere "
        "particles=100 dim=8 seed=1 best=1.0 ranks=1 threads=1 elapsed_max_s=1.0\n"
        "record=result schema=pso_v1 implementation=serial function=sphere "
        "particles=100 dim=8 seed=2 best=1.0 ranks=1 threads=1 elapsed_max_s=2.0\n"
        "record=result schema=pso_v1 implementation=serial function=sphere "
        "particles=100 dim=8 seed=1 best=0.5 ranks=1 threads=1 elapsed_max_s=3.0\n"
    )

    def test_load_run_times_rejects_duplicate_seeds(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dup.log"
            path.write_text(self.LOG, encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                stats.load_run_times(path)
            self.assertIn("duplicate seeds", str(raised.exception))

    def test_load_run_bests_rejects_duplicate_seeds(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dup.log"
            path.write_text(self.LOG, encoding="utf-8")
            with self.assertRaises(ValueError):
                stats.load_run_bests(path)

    def test_unique_seeds_pass_through(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ok.log"
            path.write_text(self.LOG.replace(
                "seed=1 best=0.5 ranks=1 threads=1 elapsed_max_s=3.0",
                "seed=3 best=0.5 ranks=1 threads=1 elapsed_max_s=3.0"),
                encoding="utf-8")
            rows = stats.load_run_times(path)
            self.assertEqual([seed for seed, _ in rows], [1, 2, 3])


class StatsCliTests(unittest.TestCase):
    def test_incomplete_command_log_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.log"
            path.write_text(
                "# command: pso_serial --seed 1\n"
                "record=result schema=pso_v1 seed=1 best=1 elapsed_max_s=1\n"
                "# command: pso_serial --seed 2\nfailed to initialize\n",
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "command/result"):
                stats.load_run_times(path)
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", stats.__file__, str(path), str(path)],
                capture_output=True, text=True, encoding="utf-8", timeout=5)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "")
            self.assertIn("command/result", proc.stderr)

    def test_command_seed_must_match_following_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong-seed.log"
            path.write_text(
                '# command: "C:\\folder with spaces\\pso.exe" --seed 1\n'
                "record=result schema=pso_v1 seed=2 best=1 elapsed_max_s=1\n",
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "seed"):
                stats.load_run_times(path)

    def test_result_without_command_in_marked_log_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "order.log"
            path.write_text(
                "record=result schema=pso_v1 seed=1 best=1 elapsed_max_s=1\n"
                "# command: pso_serial --seed 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "command/result"):
                stats.load_run_times(path)

    def test_nonfinite_log_rejected_with_location(self) -> None:
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                a, b = root / "a.log", root / "b.log"
                a.write_text(f"record=result schema=pso_v1 seed=1 best={value} elapsed_max_s=1\n",
                             encoding="utf-8")
                b.write_text("record=result schema=pso_v1 seed=1 best=1 elapsed_max_s=1\n",
                             encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "a.log:1"):
                    stats.load_run_bests(a)
                try:
                    proc = subprocess.run(
                        [sys.executable, "-X", "utf8", str(Path(stats.__file__)), str(a), str(b),
                         "--metric", "best"], capture_output=True, text=True,
                        encoding="utf-8", timeout=5)
                except subprocess.TimeoutExpired:
                    self.fail("NaN log hung the CLI")
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, "")
                self.assertIn("a.log:1", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)

    def test_zero_times_rejected_without_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a.log", Path(directory) / "b.log"
            for path, value in ((a, "1"), (b, "0")):
                path.write_text(f"record=result schema=pso_v1 seed=1 best=1 elapsed_max_s={value}\n",
                                encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", str(Path(stats.__file__)), str(a), str(b)],
                capture_output=True, text=True, encoding="utf-8", timeout=5)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "")
            self.assertIn("elapsed_max_s", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    def test_all_zero_differences_do_not_print_partial_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.log"
            path.write_text("record=result schema=pso_v1 seed=1 best=1 elapsed_max_s=1\n",
                            encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", str(Path(stats.__file__)), str(path), str(path),
                 "--metric", "best"], capture_output=True, text=True,
                encoding="utf-8", timeout=5)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "")
            self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
