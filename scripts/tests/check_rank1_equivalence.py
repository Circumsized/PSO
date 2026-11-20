#!/usr/bin/env python3
"""Assert that 1-rank mpi-data-parallel matches the serial solver field by field.

Cheapest executable proof of architecture invariants 2/3: serial and
pso_mpi_ms share RNG namespace 0 with a global-particle-index offset that is
zero at rank 1, so their pso_v1 result lines must agree on every field except
implementation name, ranks, threads, and elapsed time (which measures the
host, not the algorithm). timing_scope must still be identical.

Usage (CTest wiring):
  check_rank1_equivalence.py --serial PATH_TO_pso_serial \
      --mpi PATH_TO_pso_mpi_ms --mpiexec EXE NUMPROC_FLAG COUNT \
      [--mpiexec-pre "FLAGS"] [--mpiexec-post "FLAGS"] \
      -- <solver arguments passed to both runs>

--mpiexec-pre/--mpiexec-post 各吃一个 CMake 分号列表字符串（空串即无
flags）：pre 落在进程数之后、可执行文件之前，post 落在可执行文件之后、
求解参数之前——与 CMakeLists 其余 MPI 测试里 ${MPIEXEC_PREFLAGS}/
${MPIEXEC_POSTFLAGS} 的位置约定一致。缺省不传时与本脚本的旧行为相同。

Exit 0 = all shared fields identical; 1 = any mismatch or missing record.
"""
from __future__ import annotations

import re
import subprocess
import sys

FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
IGNORED = {"implementation", "ranks", "threads", "elapsed_max_s"}


def fail(message: str) -> None:
    print(f"rank1-equivalence check FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_flag_list(raw: str) -> list[str]:
    """CMake 分号列表字符串 -> token 列表；空串与空段不产生 token。"""
    return [token for token in raw.split(";") if token]


def build_mpi_command(mpiexec: list[str], pre: list[str], post: list[str],
                      mpi: str, solver_args: list[str]) -> list[str]:
    return [*mpiexec, *pre, mpi, *post, *solver_args]


def run(argv: list[str]) -> dict[str, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        fail(f"{' '.join(argv)} 超过 120s 未退出（求解器卡死）")
    if proc.returncode != 0:
        fail(f"{' '.join(argv)} exited {proc.returncode}: {proc.stderr.strip()}")
    for line in proc.stdout.splitlines():
        values = dict(FIELD.findall(line))
        if values.get("record") == "result" and values.get("schema") == "pso_v1":
            return values
    fail(f"{' '.join(argv)} produced no pso_v1 result line")
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--" not in args:
        print(__doc__, file=sys.stderr)
        return 2
    split = args.index("--")
    setup, solver_args = args[:split], args[split + 1:]

    serial = mpi = None
    mpiexec: list[str] = []
    mpiexec_pre: list[str] = []
    mpiexec_post: list[str] = []
    i = 0
    while i < len(setup):
        if setup[i] == "--serial" and i + 1 < len(setup):
            serial = setup[i + 1]
            i += 2
        elif setup[i] == "--mpi" and i + 1 < len(setup):
            mpi = setup[i + 1]
            i += 2
        elif setup[i] == "--mpiexec" and i + 3 < len(setup):
            mpiexec = setup[i + 1:i + 4]
            i += 4
        elif setup[i] == "--mpiexec-pre" and i + 1 < len(setup):
            mpiexec_pre = parse_flag_list(setup[i + 1])
            i += 2
        elif setup[i] == "--mpiexec-post" and i + 1 < len(setup):
            mpiexec_post = parse_flag_list(setup[i + 1])
            i += 2
        else:
            fail(f"unknown or incomplete option at {setup[i]!r}")
    if serial is None or mpi is None or len(mpiexec) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    serial_row = run([serial, *solver_args])
    mpi_row = run(build_mpi_command(mpiexec, mpiexec_pre, mpiexec_post,
                                    mpi, solver_args))

    keys = sorted((set(serial_row) | set(mpi_row)) - IGNORED)
    problems = [f"{key}: serial={serial_row.get(key)} mpi1={mpi_row.get(key)}"
                for key in keys if serial_row.get(key) != mpi_row.get(key)]
    if problems:
        fail("field mismatch:\n  " + "\n  ".join(problems))
    if serial_row.get("timing_scope") != "solver":
        fail(f"timing_scope={serial_row.get('timing_scope')!r}, expected 'solver'")
    print(f"rank1 equivalence OK: {len(keys)} shared fields identical "
          f"({mpi_row['implementation']} == serial)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
