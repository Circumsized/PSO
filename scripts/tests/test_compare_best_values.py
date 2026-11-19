"""Unit tests for compare_best_values (standard library unittest only)."""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compare_best_values as compare  # noqa: E402

LOG = ("baseline__serial__fn-sphere__p-100__d-50__r-1__t-1"
       "__b-reflect__dyn-constriction__mi-0.log")


def result_line(seed: int, best: str) -> str:
    return (f"record=result schema=pso_v1 implementation=serial "
            f"function=sphere particles=100 dim=50 seed={seed} best={best} "
            f"ranks=1 threads=1 elapsed_max_s=0.01\n")


class CollectBestTests(unittest.TestCase):
    def test_collects_per_seed_best_and_rejects_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / LOG
            log.write_text(
                result_line(1, "0.5") + result_line(2, "0.25"),
                encoding="utf-8")
            values = compare.collect_best(root)
            self.assertEqual(values[(LOG, 1)], 0.5)
            self.assertEqual(values[(LOG, 2)], 0.25)

            log.write_text(
                result_line(1, "0.5") + result_line(1, "0.25"),
                encoding="utf-8")
            with self.assertRaises(SystemExit):
                compare.collect_best(root)

    def test_rejects_unmatched_log_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stray.log").write_text(result_line(1, "0.5"),
                                            encoding="utf-8")
            with self.assertRaises(SystemExit):
                compare.collect_best(root)


class CompareTests(unittest.TestCase):
    @staticmethod
    def write_collection(root: Path, bests: list[str]) -> None:
        (root / LOG).write_text(
            "".join(result_line(seed, best)
                    for seed, best in enumerate(bests, 1)),
            encoding="utf-8")

    def run_compare(self, root_a: Path, root_b: Path, tol: float = 0.0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            code = compare.main(["--a", str(root_a), "--b", str(root_b),
                                 "--tol", repr(tol)])
        return code, stdout.getvalue()

    def test_exact_match_passes(self) -> None:
        with tempfile.TemporaryDirectory() as a_dir, \
                tempfile.TemporaryDirectory() as b_dir:
            self.write_collection(Path(a_dir), ["0.5", "0.25"])
            self.write_collection(Path(b_dir), ["0.5", "0.25"])
            code, output = self.run_compare(Path(a_dir), Path(b_dir))
            self.assertEqual(code, 0)
            self.assertIn("COMPARISON OK", output)

    def test_mismatch_and_tolerance(self) -> None:
        with tempfile.TemporaryDirectory() as a_dir, \
                tempfile.TemporaryDirectory() as b_dir:
            self.write_collection(Path(a_dir), ["0.5", "0.25"])
            self.write_collection(Path(b_dir), ["0.5000000001", "0.25"])
            code, _ = self.run_compare(Path(a_dir), Path(b_dir))
            self.assertEqual(code, 1)
            code, output = self.run_compare(Path(a_dir), Path(b_dir),
                                            tol=1e-9)
            self.assertEqual(code, 0)
            self.assertIn("COMPARISON OK", output)

    def test_one_sided_logs_fail(self) -> None:
        with tempfile.TemporaryDirectory() as a_dir, \
                tempfile.TemporaryDirectory() as b_dir:
            self.write_collection(Path(a_dir), ["0.5"])
            other = Path(b_dir) / (
                "baseline__serial__fn-rastrigin__p-100__d-50__r-1__t-1"
                "__b-reflect__dyn-constriction__mi-0.log")
            other.write_text(result_line(1, "200.0"), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    compare.main(["--a", str(a_dir), "--b", str(b_dir)])
            self.assertIn("没有共享", str(raised.exception))

    def test_allow_subset_downgrades_a_only_entries(self) -> None:
        with tempfile.TemporaryDirectory() as a_dir, \
                tempfile.TemporaryDirectory() as b_dir:
            # A 有 seed 1、2 两条，B 真的只含 seed 1——A 多出的 seed 2
            # 只有在 --allow-subset 下才降级为行信息。不这样构造的话
            # only_a 恒为空，该分支从未被真正执行。
            log = Path(a_dir) / LOG
            log.write_text(result_line(1, "0.5") + result_line(2, "0.25"),
                           encoding="utf-8")
            (Path(b_dir) / LOG).write_text(result_line(1, "0.5"),
                                           encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = compare.main(["--a", str(a_dir), "--b", str(b_dir)])
            # 不带 --allow-subset 时单边条目必须失败。
            self.assertEqual(code, 1)
            self.assertIn("only in A", stdout.getvalue())

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = compare.main(["--a", str(a_dir), "--b", str(b_dir),
                                     "--allow-subset"])
            self.assertEqual(code, 0)
            self.assertIn("COMPARISON OK", stdout.getvalue())
            self.assertIn("only in A", stdout.getvalue())
            # 反向：B 多出仍是失败
            (Path(b_dir) / LOG).write_text(
                result_line(1, "0.5") + result_line(3, "9.0"),
                encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = compare.main(["--a", str(a_dir), "--b", str(b_dir),
                                     "--allow-subset"])
            self.assertEqual(code, 1)

    def test_non_finite_best_is_rejected(self) -> None:
        # NaN 会让 abs(nan-nan) > tol 恒为 False，从而在 --tol 0（逐位一致，
        # 最强比较模式）下静默通过；必须与缺失 seed 同等硬失败。
        for value in ("NaN", "inf", "-inf", "Infinity"):
            with self.subTest(value=value), \
                    tempfile.TemporaryDirectory() as a_dir, \
                    tempfile.TemporaryDirectory() as b_dir:
                (Path(a_dir) / LOG).write_text(result_line(1, value),
                                               encoding="utf-8")
                (Path(b_dir) / LOG).write_text(result_line(1, value),
                                               encoding="utf-8")
                with self.assertRaises(SystemExit) as raised:
                    compare.main(["--a", str(a_dir), "--b", str(b_dir),
                                  "--tol", "0"])
                self.assertIn("非有限", str(raised.exception.code))


    def test_empty_collections_fail(self) -> None:
        with tempfile.TemporaryDirectory() as a_dir, \
                tempfile.TemporaryDirectory() as b_dir:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    compare.main(["--a", str(a_dir), "--b", str(b_dir)])
            self.assertIn("没有共享", str(raised.exception.code))

    def test_stray_line_and_directory_log_are_rejected(self) -> None:
        # 尾部截断碎片（非 command/非结果行）此前被静默丢弃、配对集合悄悄
        # 缩小；名为 *.log 的目录则此前会以裸 OSError 崩。两者都须带定位
        # 硬失败。
        with tempfile.TemporaryDirectory() as a_dir:
            self.write_collection(Path(a_dir), ["0.5", "0.25"])
            # 截断碎片行
            with tempfile.TemporaryDirectory() as b_dir, \
                    self.assertRaises(SystemExit) as raised:
                (Path(b_dir) / LOG).write_text(
                    result_line(1, "0.5") + "truncated garbage no prefix\n",
                    encoding="utf-8")
                compare.main(["--a", str(a_dir), "--b", str(b_dir)])
            self.assertIn("非法行", str(raised.exception.code))
            # 目录冒充日志
            with tempfile.TemporaryDirectory() as b_dir, \
                    self.assertRaises(SystemExit) as raised:
                (Path(b_dir) / LOG).mkdir()
                compare.main(["--a", str(a_dir), "--b", str(b_dir)])
            self.assertIn("不是可读文件", str(raised.exception.code))

    def test_recordless_log_is_rejected(self) -> None:
        # 空文件/截断日志若零记录，配对集合会静默缩小仍可能全绿；
        # 必须与缺日志同口径硬失败。
        with tempfile.TemporaryDirectory() as a_dir, \
                tempfile.TemporaryDirectory() as b_dir:
            self.write_collection(Path(a_dir), ["0.5", "0.25"])
            (Path(b_dir) / LOG).write_text("", encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                compare.main(["--a", str(a_dir), "--b", str(b_dir)])
            self.assertIn("不含任何 pso_v1 记录", str(raised.exception.code))

    def test_negative_tol_is_rejected(self) -> None:
        # 负容差会让 delta > tol 恒真，逐位相同也报 FAILED，必须拒绝。
        with tempfile.TemporaryDirectory() as a_dir, \
                tempfile.TemporaryDirectory() as b_dir:
            self.write_collection(Path(a_dir), ["0.5", "0.25"])
            self.write_collection(Path(b_dir), ["0.5", "0.25"])
            with contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                compare.main(["--a", str(a_dir), "--b", str(b_dir),
                              "--tol", "-1"])
            self.assertEqual(str(raised.exception.code), "2")

    def test_corrupt_bytes_and_non_numeric_best_are_rejected(self) -> None:
        # 坏字节不得被洗白：实测 best 末字节损坏（errors="replace" 时期）
        # 会经 \b 回溯把 0.500000<坏字节> 静默截断成 0.500000，产生假 OK。
        # 非数值 best 此前则是裸 ValueError traceback。
        cases = (
            (b"record=result schema=pso_v1 implementation=serial "
             b"function=sphere particles=100 dim=50 seed=1 "
             b"best=0.500000\xff elapsed_max_s=0.1\n", "编码损坏"),
            (b"record=result schema=pso_v1 implementation=serial "
             b"function=sphere particles=100 dim=50 seed=1 best=abc "
             b"elapsed_max_s=0.1\n", "best 不是数值"),
        )
        for content, expected in cases:
            with self.subTest(expected=expected), \
                    tempfile.TemporaryDirectory() as a_dir, \
                    tempfile.TemporaryDirectory() as b_dir:
                self.write_collection(Path(a_dir), ["0.5", "0.25"])
                (Path(b_dir) / LOG).write_bytes(content)
                with self.assertRaises(SystemExit) as raised:
                    compare.main(["--a", str(a_dir), "--b", str(b_dir)])
                self.assertIn(expected, str(raised.exception.code))


class StrictTokenTests(unittest.TestCase):
    def _collection(self, directory: str, line: str) -> Path:
        root = Path(directory)
        (root / LOG).write_text(line, encoding="utf-8")
        return root

    def test_partial_numeric_tokens_are_rejected(self) -> None:
        cases = (
            ("record=result schema=pso_v1 seed=1.9 best=0.5 "
             "elapsed_max_s=0.1\n", "seed 不是非负整数"),
            ("record=result schema=pso_v1 seed=1 best=0.5+ "
             "elapsed_max_s=0.1\n", "best 不是数值"),
            ("record=result schema=pso_v1 seed=1 best=0.5, "
             "elapsed_max_s=0.1\n", "best 不是数值"),
        )
        for line, expected in cases:
            with self.subTest(expected=expected), \
                    tempfile.TemporaryDirectory() as directory:
                root = self._collection(directory, line)
                with self.assertRaises(SystemExit) as raised:
                    compare.collect_best(root)
                self.assertIn(expected, str(raised.exception))

    def test_repeated_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._collection(
                directory,
                "record=result schema=pso_v1 seed=1 seed=2 best=0.5 "
                "elapsed_max_s=0.1\n")
            with self.assertRaises(SystemExit) as raised:
                compare.collect_best(root)
            self.assertIn("恰好各含一个", str(raised.exception))

    def test_missing_collection_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "nope"
            with self.assertRaises(SystemExit) as raised:
                compare.collect_best(missing)
            self.assertIn("不是目录", str(raised.exception))

    def test_empty_opposite_collection_with_allow_subset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as a_dir, \
                tempfile.TemporaryDirectory() as b_dir:
            self._collection(a_dir, "record=result schema=pso_v1 seed=1 "
                                    "best=0.5 elapsed_max_s=0.1\n")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    compare.main(["--a", a_dir, "--b", b_dir,
                                  "--allow-subset"])
            self.assertIn("没有共享", str(raised.exception))

    def test_valid_non_empty_subset_still_passes(self) -> None:
        with tempfile.TemporaryDirectory() as a_dir, \
                tempfile.TemporaryDirectory() as b_dir:
            log = Path(a_dir) / LOG
            log.write_text("record=result schema=pso_v1 seed=1 best=0.5 "
                           "elapsed_max_s=0.1\n"
                           "record=result schema=pso_v1 seed=2 best=0.25 "
                           "elapsed_max_s=0.1\n", encoding="utf-8")
            (Path(b_dir) / LOG).write_text(
                "record=result schema=pso_v1 seed=1 best=0.5 "
                "elapsed_max_s=0.1\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = compare.main(["--a", a_dir, "--b", b_dir,
                                     "--allow-subset"])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
