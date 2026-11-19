#!/usr/bin/env python3
"""验证真实求解器在初始种群无有限适应度时明确失败。"""
from __future__ import annotations

import subprocess
import sys


def main() -> int:
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("缺少求解器命令")
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"求解器未正常完成数值失败处理: {error}") from error
    if result.returncode == 0:
        raise SystemExit("无有限适应度时仍返回成功")
    if "initial swarm has no finite fitness" not in result.stderr:
        raise SystemExit(f"缺少数值失败诊断: {result.stderr}")
    if "record=result" in result.stdout:
        raise SystemExit("失败运行仍发布了结果行")
    print("无有限初始适应度：非零退出且未发布结果")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
