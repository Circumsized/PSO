#!/usr/bin/env bash
# 在 Linux 容器内执行的第二环境验证（由宿主机 docker run 调用）：
#   1. ASan+UBSan（Debug、MPI+OpenMP）真实 sanitizer 运行 + 全 CTest
#   2. Release 构建 + 全 CTest（Linux GCC 工具链交叉验证）
# 源码从挂载点复制到容器本地目录，构建产物不污染宿主仓库。
set -euo pipefail
cd /tmp/pso

echo "== toolchain =="
gcc --version | head -1
cmake --version | head -1

echo "== installing OpenMPI =="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libopenmpi-dev openmpi-bin > /dev/null
mpirun --version | head -1

echo "== sanitizer build (ASan+UBSan, Debug, MPI+OpenMP) =="
cmake -S . -B build-sanitize \
    -DPSO_ENABLE_MPI=ON -DPSO_ENABLE_OPENMP=ON \
    -DPSO_ENABLE_SANITIZERS=ON -DCMAKE_BUILD_TYPE=Debug \
    > configure-sanitize.log 2>&1 || { cat configure-sanitize.log; exit 1; }
# FindMPI/FindOpenMP 失败只会降级 WARNING 并跳过目标，必须断言真的生效。
# Python3 同理：缺解释器时 CTest 会静默少跑全部 Python 门禁套件。
grep -q "PSO_ENABLE_MPI=ON" configure-sanitize.log || { echo "MPI 未生效"; cat configure-sanitize.log; exit 1; }
grep -q "OpenMP_C_FOUND=TRUE" configure-sanitize.log || { echo "OpenMP 未生效"; cat configure-sanitize.log; exit 1; }
grep -q "Python3_Interpreter_FOUND=TRUE" configure-sanitize.log || { echo "Python3 未生效"; cat configure-sanitize.log; exit 1; }
cmake --build build-sanitize --parallel 4

echo "== sanitizer CTest =="
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
export ASAN_OPTIONS=detect_leaks=0
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
ctest --test-dir build-sanitize --output-on-failure --timeout 120

echo "== release build (second environment) =="
cmake -S . -B build-release \
    -DPSO_ENABLE_MPI=ON -DPSO_ENABLE_OPENMP=ON -DCMAKE_BUILD_TYPE=Release \
    > configure-release.log 2>&1 || { cat configure-release.log; exit 1; }
# FindMPI/FindOpenMP 失败只会降级 WARNING 并跳过目标，必须断言真的生效。
grep -q "PSO_ENABLE_MPI=ON" configure-release.log || { echo "MPI 未生效"; cat configure-release.log; exit 1; }
grep -q "OpenMP_C_FOUND=TRUE" configure-release.log || { echo "OpenMP 未生效"; cat configure-release.log; exit 1; }
grep -q "Python3_Interpreter_FOUND=TRUE" configure-release.log || { echo "Python3 未生效"; cat configure-release.log; exit 1; }
cmake --build build-release --parallel 4

echo "== release CTest =="
ctest --test-dir build-release --output-on-failure --timeout 120

echo "LINUX_CHECK_DONE"
