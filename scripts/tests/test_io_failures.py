"""写失败路径的端到端验证（仅 Linux 生效）与 CLI 诊断钉子（跨平台）。

本批修复主张"history 与结果行写失败必须非零退出，不得静默截断"。跨平台
构造中途写失败并不现实（Windows 没有等价设备），故这组哨兵只在 Linux
生效：/dev/full 的 open 成功而 write 返回 ENOSPC，正好命中磁盘满场景。
Windows 本地跑这组会 skip，绿灯由 CI 的 Linux job 提供；pso_serial 走
pso_report.c 的公共检查（fprintf 返回值 + 显式 fflush，CERT FIO34-C），
串行路径的端到端证明即覆盖该公共实现。文件内另有跨平台的
CliDiagnosticsTests（不受 /dev/full 守卫约束，Windows 也实跑）。
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEV_FULL = Path("/dev/full")


def built(target: str) -> Path | None:
    """返回已构建的 target；PSO_TEST_BUILD_DIR 存在时以其为唯一来源。

    CTest 通过 ENVIRONMENT 注入当前构建目录：显式指定构建目录时"文件不在"
    必须失败而不是回退到别的旧构建或静默跳过（审计 R7）。未注入时
    保留原开发机约定：依次搜 build-warn/ 与 build/，找不到返回 None
    由调用方 skip——CI 的 python-tools 作业不构建 C 目标，依赖这一语义。
    """
    override = os.environ.get("PSO_TEST_BUILD_DIR")
    if override:
        for name in (target, target + ".exe"):
            candidate = Path(override) / name
            if candidate.is_file():
                return candidate
        raise AssertionError(
            f"PSO_TEST_BUILD_DIR={override} 中缺少 {target}；"
            "构建目录被显式指定时不得回退或跳过")
    for directory in ("build-warn", "build"):
        for name in (target, target + ".exe"):
            candidate = ROOT / directory / name
            if candidate.is_file():
                return candidate
    return None

ARGS = ["--function", "sphere", "--particles", "8", "--dim", "2",
        "--max-evals", "32", "--target", "1e300", "--seed", "7",
        "--no-early-stop"]


# 不能用 DEV_FULL.exists() 当守卫：原生 Windows Python 把 "/dev/full"
# 解析成当前盘的 X:\dev\full（普通文件可写），MSYS Git Bash 又真的提供
# /dev/full 设备——两种环境下存在性都为真但哨兵都不会触发 ENOSPC，
# 且误跑会在仓库外自建 X:\dev\full 让守卫永久翻转为真。只有 Linux 上
# /dev/full 的语义才是确定的，故按平台判定。
@unittest.skipUnless(sys.platform.startswith("linux"), "需要 /dev/full（仅 Linux）")
class WriteFailureTests(unittest.TestCase):
    def test_history_write_failure_exits_nonzero(self) -> None:
        exe = built("pso_serial")
        if exe is None:
            self.skipTest("pso_serial not built yet")
        proc = subprocess.run(
            [str(exe), *ARGS, "--history", str(DEV_FULL)],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        # 写失败与关闭失败两条分支都在该文案家族里，不钉具体是哪一条。
        self.assertIn("cannot", proc.stderr)
        self.assertIn("history file", proc.stderr)
        # R1 回归哨兵：失败清理绝不允许 unlink 设备本身。
        self.assertTrue(DEV_FULL.exists(), "测试运行删除了 /dev/full")
        self.assertTrue(stat.S_ISCHR(DEV_FULL.stat().st_mode),
                        "/dev/full 不再是字符设备")

    def test_result_line_write_failure_exits_nonzero(self) -> None:
        exe = built("pso_serial")
        if exe is None:
            self.skipTest("pso_serial not built yet")
        with DEV_FULL.open("w") as sink:
            proc = subprocess.run([str(exe), *ARGS], stdout=sink,
                                  stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("cannot write result line", proc.stderr)


class CliDiagnosticsTests(unittest.TestCase):
    """docs/06 §6.1 承诺参数解析失败"打印错误信息并返回非零退出码"。

    该承诺在 2026-10-08 修复静默拒绝后成立，但诊断文本此前无任何测试
    钉住，未来重构可静默删除而不被发现——这里按 helper 分类锁死文本
    与退出码：四个解析 helper 各至少一个代表选项。
    """

    def test_parse_failures_print_named_diagnostics(self) -> None:
        exe = built("pso_serial")
        if exe is None:
            self.skipTest("pso_serial not built yet")
        for option, value in (("--seed", "-1"), ("--seed", " -1"),
                              ("--max-evals", "\t-1"),
                              ("--target", "nan"),
                              ("--boundary", "bogus"), ("--chi", "1e309"),
                              ("--particles", "abc")):
            with self.subTest(option=option, value=value):
                proc = subprocess.run(
                    [str(exe), "--function", "sphere", "--particles", "8",
                     "--dim", "2", "--max-evals", "32", option, value],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace")
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(f"invalid value for {option}: {value}",
                              proc.stderr)


class HistoryResidueTests(unittest.TestCase):
    """失败退出不得删除用户提供的 history 路径（R1 回归钉子，跨平台）。

    R1 之前：分配/全非有限/写失败/关闭失败路径会 remove() 该路径——
    Linux root 下 `--history /dev/full` 会把字符设备本身 unlink 掉。
    本用例用"初始种群全非有限"构造确定性失败退出，断言普通文件残留
    且为空（0 字节），而不是被删除。
    """

    def test_failed_run_keeps_user_history_path(self) -> None:
        exe = built("pso_serial")
        if exe is None:
            self.skipTest("pso_serial not built yet")
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "fail.history"
            proc = subprocess.run(
                [str(exe), "--function", "sphere", "--particles", "8",
                 "--dim", "2", "--max-evals", "16", "--seed", "3",
                 "--lower", "1e200", "--upper", "2e200",
                 "--history", str(sidecar)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("no finite fitness", proc.stderr)
            self.assertTrue(sidecar.exists(),
                            "失败退出删除了用户提供的 history 路径")
            self.assertEqual(sidecar.stat().st_size, 0)


class NoSidecarDeletionTests(unittest.TestCase):
    """源码级哨兵：三个入口都不得重新引入路径删除。

    该类缺陷此前修过一轮（fopen 失败误删他人文件）又复发过一轮
    （成功打开后误删设备）；行为测试无法安全覆盖设备场景（若实现
    回归，测试本身就会破坏目标），故在源码层锁死。
    """

    def test_entrypoints_do_not_delete_history_paths(self) -> None:
        for name in ("main_serial.c", "main_mpi_ms.c", "main_mpi_island.c"):
            text = (ROOT / "src" / name).read_text(encoding="utf-8")
            self.assertNotIn(
                "remove(", text,
                f"{name} 重新引入了按路径删除；R1 修复禁止删除用户路径")


if __name__ == "__main__":
    unittest.main()
