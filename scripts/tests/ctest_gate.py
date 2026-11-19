#!/usr/bin/env python3
"""CTest 包装器：直通子进程输出，并把退出码变成真实的失败判据。

CMake 的 PASS_REGULAR_EXPRESSION 会忽略进程退出码（官方文档明确），
"输出匹配但进程非零退出"的测试会被判 Passed——求解器失败、UBSan 可恢复
诊断、包装脚本自身错误都会因此静默通过。本包装器：

* 原样继承 stdin/stdout/stderr，字节级直通（供既有 PASS 正则继续匹配）；
* 镜像子进程退出码；非零时额外打印 ``PsoTestGate:`` 哨兵，供 CMake 侧
  FAIL_REGULAR_EXPRESSION 命中（双保险；与 PASS 冲突时 FAIL 恒胜）。

用法（CMake 侧由 pso_add_gate_test 生成）：
  python ctest_gate.py -- <求解器命令...>
"""
from __future__ import annotations

import subprocess
import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        print("PsoTestGate: 缺少子进程命令", file=sys.stderr)
        return 2
    try:
        child = subprocess.run(argv)
    except OSError as error:
        print(f"PsoTestGate: 无法启动子进程: {error}", file=sys.stderr)
        return 2
    if child.returncode != 0:
        print(f"PsoTestGate: 子进程退出码 {child.returncode}: {argv[0]}",
              file=sys.stderr)
        return child.returncode if 0 < child.returncode < 256 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
