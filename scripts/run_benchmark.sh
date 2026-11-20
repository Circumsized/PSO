#!/usr/bin/env bash
set -euo pipefail

# 快速冒烟链路：固定 FEs、固定单线程 + 拓扑绑核，输出机器可解析的一行结果。
# 单次采集、无 manifest、无法保证采集期机器空闲，因此其 elapsed 列不构成
# 任何可引用的计时结论——性能数字一律走 scripts/run_experiments.py。
# 用法：./scripts/run_benchmark.sh [runs] [particles] [dim] [max_evals]
RUNS="${1:-30}"
PARTICLES="${2:-2000}"
DIM="${3:-50}"
MAX_EVALS="${4:-100000}"
FUNCTION="${FUNCTION:-rastrigin}"
SEED_BASE="${SEED_BASE:-20240229}"
MPIEXEC="${MPIEXEC:-mpiexec}"
PYTHON="${PYTHON:-python}"
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* || "$(uname -s)" == CYGWIN* ]]; then
    export MSYS2_ARG_CONV_EXCL='*'
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXE_SUFFIX=""
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* || "$(uname -s)" == CYGWIN* ]]; then
    EXE_SUFFIX=".exe"
fi
SERIAL="$ROOT/build/pso_serial${EXE_SUFFIX}"
MS="$ROOT/build/pso_mpi_ms${EXE_SUFFIX}"
ISLAND="$ROOT/build/pso_mpi_island${EXE_SUFFIX}"
if [[ -n "$EXE_SUFFIX" ]] && command -v cygpath >/dev/null 2>&1; then
    SERIAL="$(cygpath -w "$SERIAL")"
    MS="$(cygpath -w "$MS")"
    ISLAND="$(cygpath -w "$ISLAND")"
fi
OUT="$ROOT/results"
mkdir -p "$OUT"

# 冒烟链路只认 build/ 产物：缺任一可执行文件立刻失败，别把错误推迟成
# 半份空日志（rm 已经先清掉了旧的 raw_*.log）。
[[ -x "$SERIAL" ]] || { echo "缺少 $SERIAL；请先构建到 build/（cmake --build build --parallel）" >&2; exit 1; }
[[ -x "$MS" ]] || { echo "缺少 $MS；请先构建到 build/（cmake --build build --parallel）" >&2; exit 1; }
[[ -x "$ISLAND" ]] || { echo "缺少 $ISLAND；请先构建到 build/（cmake --build build --parallel）" >&2; exit 1; }

rm -f "$OUT/raw_serial.log" "$OUT/raw_mpi_ms.log" "$OUT/raw_mpi_island.log"
for ((run=0; run<RUNS; run++)); do
    seed=$((SEED_BASE + run))
    OMP_NUM_THREADS=1 "$SERIAL" --function "$FUNCTION" --particles "$PARTICLES" --dim "$DIM" \
        --max-evals "$MAX_EVALS" --seed "$seed" --no-early-stop --pin-cpus >> "$OUT/raw_serial.log"
    OMP_NUM_THREADS=1 "$MPIEXEC" -n 2 "$MS" --function "$FUNCTION" --particles "$PARTICLES" --dim "$DIM" \
        --max-evals "$MAX_EVALS" --seed "$seed" --no-early-stop --pin-cpus >> "$OUT/raw_mpi_ms.log"
    OMP_NUM_THREADS=1 "$MPIEXEC" -n 2 "$ISLAND" --function "$FUNCTION" --particles "$PARTICLES" --dim "$DIM" \
        --max-evals "$MAX_EVALS" --seed "$seed" --no-early-stop --pin-cpus >> "$OUT/raw_mpi_island.log"
done

PLOT_SCRIPT="$ROOT/scripts/plot_results.py"
if [[ -n "$EXE_SUFFIX" ]] && command -v cygpath >/dev/null 2>&1; then
    PLOT_SCRIPT="$(cygpath -w "$PLOT_SCRIPT")"
    OUT_FOR_PYTHON="$(cygpath -w "$OUT")"
else
    OUT_FOR_PYTHON="$OUT"
fi
"$PYTHON" "$PLOT_SCRIPT" --input "$OUT_FOR_PYTHON" --output "$OUT_FOR_PYTHON/smoke_summary.csv"
printf 'Benchmark logs and summary written under %s\n' "$OUT"
