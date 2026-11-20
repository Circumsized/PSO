cmake_minimum_required(VERSION 3.20)
project(pso_refactor C)

option(PSO_ENABLE_MPI "Build MPI executables" ON)
option(PSO_ENABLE_OPENMP "Enable OpenMP in the shared core" ON)
option(PSO_ENABLE_SANITIZERS "Enable address and undefined-behavior sanitizers" OFF)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

# 项目声明的告警标准必须覆盖每一个 target。此前它挂在 pso_core 上且是
# PRIVATE，于是三个入口与 tests/*.c 从未被 -Wall -Wextra -Wpedantic
# 审视过。
if(MSVC)
    add_compile_options(/W4)
else()
    add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(Threads REQUIRED)
if(PSO_ENABLE_OPENMP)
    find_package(OpenMP)
endif()
if(PSO_ENABLE_MPI)
    find_package(MPI COMPONENTS C)
    # 用项目私有 pso_mpi 接口库隔离 FindMPI：MinGW + MS-MPI 没有 mpicc wrapper，
    # FindMPI 即使失败也可能生成一个不可用的 MPI::MPI_C 占位 target。
    add_library(pso_mpi INTERFACE)
    if(MPI_C_FOUND)
        target_link_libraries(pso_mpi INTERFACE MPI::MPI_C)
        # 与下方回退分支同理：第三方 mpi.h 不经受本项目的 -Wpedantic 审视
        # （MPI::MPI_C 携带的是普通 include，跨编译器如 MSVC /W4 时尤为重要）。
        if(MPI_C_INCLUDE_DIRS)
            set_property(TARGET pso_mpi APPEND PROPERTY
                         INTERFACE_SYSTEM_INCLUDE_DIRECTORIES "${MPI_C_INCLUDE_DIRS}")
        endif()
        if(WIN32)
            find_program(MS_MPIEXEC_EXECUTABLE NAMES mpiexec.exe mpiexec
                PATHS "C:/Program Files/Microsoft MPI/Bin" NO_DEFAULT_PATH)
            if(MS_MPIEXEC_EXECUTABLE)
                set(MPIEXEC_EXECUTABLE "${MS_MPIEXEC_EXECUTABLE}")
                set(MPIEXEC_NUMPROC_FLAG "-n")
            endif()
        endif()
    elseif(WIN32)
        find_path(MS_MPI_INCLUDE_DIR mpi.h
            PATHS
                "$ENV{MSMPI_INC}"
                "C:/Program Files (x86)/Microsoft SDKs/MPI/Include"
                "C:/Program Files/Microsoft SDKs/MPI/Include")
        find_library(MS_MPI_LIBRARY NAMES msmpi
            PATHS
                "$ENV{MSMPI_LIB64}"
                "C:/Program Files (x86)/Microsoft SDKs/MPI/Lib/x64"
                "C:/Program Files/Microsoft SDKs/MPI/Lib/x64")
        if(MS_MPI_INCLUDE_DIR AND MS_MPI_LIBRARY)
            target_include_directories(pso_mpi INTERFACE "${MS_MPI_INCLUDE_DIR}")
            # 第三方头不该被本项目的 -Wpedantic 审视：MS-MPI 的 mpi.h 用了
            # __int64 等非 ISO 写法。target_include_directories(... SYSTEM)
            # 需要 CMake 3.25，本仓下限是 3.20，故用属性写法。
            set_property(TARGET pso_mpi APPEND PROPERTY
                         INTERFACE_SYSTEM_INCLUDE_DIRECTORIES "${MS_MPI_INCLUDE_DIR}")
            target_link_libraries(pso_mpi INTERFACE "${MS_MPI_LIBRARY}")
            find_program(MS_MPIEXEC_EXECUTABLE NAMES mpiexec.exe mpiexec
                PATHS "C:/Program Files/Microsoft MPI/Bin" NO_DEFAULT_PATH)
            if(MS_MPIEXEC_EXECUTABLE)
                set(MPIEXEC_EXECUTABLE "${MS_MPIEXEC_EXECUTABLE}")
                set(MPIEXEC_NUMPROC_FLAG "-n")
            endif()
            set(MPI_C_FOUND TRUE)
            message(STATUS "Using MS-MPI SDK fallback: ${MS_MPI_INCLUDE_DIR}")
        endif()
    endif()
    if(NOT MPI_C_FOUND)
        message(WARNING "MPI C compiler not found; MPI targets will be skipped")
        set(PSO_ENABLE_MPI OFF)
    endif()
endif()

# OpenMPI 在槽位不足的 runner 上会拒绝 -n 4（CI runner 常见 2 物理核 ×2
# 线程，而本项目有 4-rank 金标准与环迁移测试）。只对 Open MPI 追加
# --oversubscribe：MPICH/MS-MPI 不认这个旗标，乱加会直接报错。
if(PSO_ENABLE_MPI AND MPIEXEC_EXECUTABLE)
    execute_process(COMMAND "${MPIEXEC_EXECUTABLE}" --version
        OUTPUT_VARIABLE PSO_MPIEXEC_VERSION
        ERROR_QUIET OUTPUT_STRIP_TRAILING_WHITESPACE)
    # Open MPI 的 mpiexec --version 输出形如 "mpiexec (OpenRTE) 4.1.x"，
    # 不含字面 "Open MPI"；两种写法都要命中。
    if(PSO_MPIEXEC_VERSION MATCHES "Open MPI|OpenRTE")
        set(MPIEXEC_PREFLAGS "--oversubscribe")
    endif()
    message(STATUS "mpiexec preflags: '${MPIEXEC_PREFLAGS}'")
endif()

# Sanitizer 同样是全项目标准：挂在 pso_core 上时 pso_env_probe（只链
# pso_mpi）与 test_rng（不链 pso_core）会整对目标逃过 ASan/UBSan。
# 探测必须在任何 target 创建之前完成，否则 add_compile_options 不生效。
if(PSO_ENABLE_SANITIZERS AND NOT MSVC)
    # MinGW 的精简发行版可能没有 libasan/libubsan；先探测运行库，避免把
    # 一个必然链接失败的 sanitizer 配置写入构建图。
    #
    # 两个坑：MinGW-w64 的导入库实名为 libasan.dll.a / libubsan.dll.a；
    # 且 gcc -print-file-name=X 在找不到时原样回显 X，此时若用 EXISTS 按
    # 相对路径判定，源码目录下的同名文件会造成误命中。因此对每个候选名
    # 都要求回显结果与输入不同（即真的解析到了绝对路径）。
    function(pso_find_sanitizer_library out_var stem)
        foreach(candidate "${stem}.dll.a" "${stem}.a")
            execute_process(
                COMMAND "${CMAKE_C_COMPILER}" -print-file-name=${candidate}
                OUTPUT_VARIABLE resolved
                OUTPUT_STRIP_TRAILING_WHITESPACE
                ERROR_QUIET)
            if(resolved AND NOT resolved STREQUAL candidate
               AND EXISTS "${resolved}")
                set(${out_var} "${resolved}" PARENT_SCOPE)
                return()
            endif()
        endforeach()
        set(${out_var} "" PARENT_SCOPE)
    endfunction()

    pso_find_sanitizer_library(PSO_ASAN_LIBRARY libasan)
    pso_find_sanitizer_library(PSO_UBSAN_LIBRARY libubsan)
    if(PSO_ASAN_LIBRARY AND PSO_UBSAN_LIBRARY)
        message(STATUS "sanitizers enabled (${PSO_ASAN_LIBRARY})")
        # GCC 的 -fsanitize=undefined 默认允许恢复（UBSan 运行库默认
        # halt_on_error=0），恢复时"退出码可能指示成功"；加 -fno-sanitize-recover
        # 让首个检出即非零退出，再配合 ctest_gate.py 的退出码镜像成为硬失败。
        add_compile_options(-fsanitize=address,undefined
                            -fno-sanitize-recover=undefined
                            -fno-omit-frame-pointer)
        add_link_options(-fsanitize=address,undefined)
    else()
        message(WARNING "PSO_ENABLE_SANITIZERS requested, but this compiler has no libasan/libubsan; sanitizers disabled")
    endif()
endif()

add_library(pso_core STATIC
    src/pso_bench.c
    src/pso_config.c
    src/pso_cli.c
    src/pso_core.c
    src/pso_pin.c
    src/pso_report.c
)
target_include_directories(pso_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(pso_core PUBLIC Threads::Threads)
if(NOT WIN32)
    target_link_libraries(pso_core PUBLIC m)
endif()
if(OpenMP_C_FOUND AND PSO_ENABLE_OPENMP)
    target_link_libraries(pso_core PUBLIC OpenMP::OpenMP_C)
    target_compile_definitions(pso_core PUBLIC PSO_OPENMP_ENABLED=1)
endif()

add_executable(pso_serial src/main_serial.c)
target_link_libraries(pso_serial PRIVATE pso_core)

# 环境自检 target 必须在 MPI 块之外定义：无 MPI 配置下 CTest 的
# environment_probe 有串行注册分支，若 target 只在 MPI 块内创建，
# 该分支会引用一个不存在的 target 并让整份测试清单无法运行。
add_executable(pso_env_probe tests/env_probe.c)
# 探针要报告"生效的 OpenMP 配置"，必须与求解器一致地携带 OpenMP 依赖；
# 只链 pso_mpi 时 -fopenmp 不会出现在命令行，探针恒报 threads=1。
target_link_libraries(pso_env_probe PRIVATE pso_core)
if(PSO_ENABLE_MPI)
    add_executable(pso_mpi_ms src/main_mpi_ms.c)
    add_executable(pso_mpi_island src/main_mpi_island.c)
    foreach(target pso_mpi_ms pso_mpi_island)
        target_link_libraries(${target} PRIVATE pso_core pso_mpi)
        target_compile_definitions(${target} PRIVATE PSO_WITH_MPI=1)
    endforeach()
    target_link_libraries(pso_env_probe PRIVATE pso_mpi)
    target_compile_definitions(pso_env_probe PRIVATE PSO_WITH_MPI=1)
endif()

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 COMPONENTS Interpreter)
    # PASS_REGULAR_EXPRESSION 忽略进程退出码（CMake 官方文档），"输出匹配
    # 但求解器非零退出"会被判 Passed；恢复型 UBSan 诊断同样可能 exit 0。
    # 所有以 PASS 正则判定的测试统一经 ctest_gate.py 包装：直通输出 +
    # 镜像退出码，非零时打印 PsoTestGate: 哨兵，由 FAIL 正则接住；
    # "runtime error" 同时作为 UBSan 恢复诊断的 fail 正则（与
    # -fno-sanitize-recover 双保险）。无 Python 时退化为普通 add_test
    # （退出码本就被 CTest 检查，只是失去哨兵层）。
    function(pso_add_gate_test name)
        set(command ${ARGN})
        list(POP_FRONT command keyword)   # CMake >= 3.15；本仓下限 3.20
        if(NOT keyword STREQUAL "COMMAND")
            message(FATAL_ERROR
                    "pso_add_gate_test(${name}) 需要 COMMAND 关键字")
        endif()
        if(Python3_Interpreter_FOUND)
            add_test(NAME ${name} COMMAND ${Python3_EXECUTABLE}
                     ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/ctest_gate.py
                     -- ${command})
            set_tests_properties(${name} PROPERTIES
                FAIL_REGULAR_EXPRESSION "PsoTestGate:;runtime error")
        else()
            add_test(NAME ${name} COMMAND ${command})
        endif()
    endfunction()
    if(Python3_Interpreter_FOUND)
        # 下列 Python 套件逐套显式注册进 CTest：未注册的套件等于游离在
        # 门禁之外。套件数与项数这类随提交漂移的计数不写在活代码注释里
        # （历史批次的计数以 CHANGELOG 为准），注册后 Linux 与 Windows
        # 两个 job 都覆盖。
        add_test(NAME experiment_runner_unit
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/test_run_experiments.py)
        add_test(NAME statistics_unit
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/test_analyze_stats.py)
        add_test(NAME convergence_analysis_unit
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/test_analyze_convergence.py)
        add_test(NAME convergence_summary_unit
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/test_summarize_convergence.py)
        add_test(NAME best_value_comparison_unit
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/test_compare_best_values.py)
        add_test(NAME speedup_analysis_unit
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/test_analyze_speedups.py)
        # 注入当前构建目录：哨兵必须测本次产物而不是回退到旧构建树
        # （审计 R7）；显式指定时缺二进制即失败，不再静默 skip。
        add_test(NAME rank1_equivalence_unit
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/test_rank1_equivalence.py)
        # 写失败路径的端到端哨兵：仅 Linux 的 /dev/full 能构造中途写失败，
        # Windows 上该组 skip（绿灯由 CI 的 Linux job 提供）；同文件内的
        # CLI 诊断钉子（CliDiagnosticsTests）跨平台实跑。
        add_test(NAME io_failure_paths
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/test_io_failures.py)
        add_test(NAME ctest_gate_unit
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/test_ctest_gate.py)
        # 注入当前构建目录：当前树优先且不可回退（审计 R7）。
        set_tests_properties(io_failure_paths PROPERTIES
            ENVIRONMENT "PSO_TEST_BUILD_DIR=${CMAKE_CURRENT_BINARY_DIR}")
    endif()

    add_executable(test_bench tests/test_bench.c)
    target_link_libraries(test_bench PRIVATE pso_core)
    add_test(NAME bench_known_optima COMMAND test_bench)

    add_executable(test_rng tests/test_rng.c)
    target_include_directories(test_rng PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    add_test(NAME rng_reproducibility COMMAND test_rng)

    add_executable(test_core tests/test_core.c)
    target_link_libraries(test_core PRIVATE pso_core)
    add_test(NAME core_invariants COMMAND test_core)

    add_executable(test_cli tests/test_cli.c)
    target_link_libraries(test_cli PRIVATE pso_core)
    add_test(NAME cli_validation COMMAND test_cli)

    add_executable(test_pin tests/test_pin.c)
    target_link_libraries(test_pin PRIVATE pso_core)
    add_test(NAME pin_affinity COMMAND test_pin)

    # 构建环境自检：target 定义在上方 MPI 块之外（无 MPI 配置下串行
    # 注册分支也要引用它）。CI 冒烟验证生效配置。
    if(PSO_ENABLE_MPI)
        pso_add_gate_test(environment_probe COMMAND ${MPIEXEC_EXECUTABLE}
                 ${MPIEXEC_NUMPROC_FLAG} 2 ${MPIEXEC_PREFLAGS}
                 $<TARGET_FILE:pso_env_probe> ${MPIEXEC_POSTFLAGS})
    else()
        pso_add_gate_test(environment_probe COMMAND $<TARGET_FILE:pso_env_probe>)
    endif()
    set_tests_properties(environment_probe PROPERTIES
        PASS_REGULAR_EXPRESSION "(mpi rank 0/[0-9]+, thread_level=[0-9]+|serial), omp threads=[0-9]+")

    pso_add_gate_test(serial_initial_budget COMMAND $<TARGET_FILE:pso_serial>
             --function sphere --particles 16 --dim 3 --max-evals 16 --seed 7)
    set_tests_properties(serial_initial_budget PROPERTIES
        PASS_REGULAR_EXPRESSION "iterations=0 evals=16")

    if(OpenMP_C_FOUND AND PSO_ENABLE_OPENMP)
        set(PSO_TEST_THREADS 2)
    else()
        set(PSO_TEST_THREADS 1)
    endif()
    pso_add_gate_test(serial_no_early_stop COMMAND $<TARGET_FILE:pso_serial>
             --function sphere --particles 8 --dim 2 --max-evals 32
             --target 1e300 --seed 7 --no-early-stop)
    set_tests_properties(serial_no_early_stop PROPERTIES
        PASS_REGULAR_EXPRESSION "threads=${PSO_TEST_THREADS} .*iterations=3 evals=32 .*best=4\\.1687829711287634 success=1 fes_to_target=8 stop_reason=evaluation_budget stop_on_target=0 timing_scope=solver elapsed_max_s=[0-9]"
        ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE")
    pso_add_gate_test(serial_target_stop COMMAND $<TARGET_FILE:pso_serial>
             --function sphere --particles 8 --dim 2 --max-evals 32
             --target 1e300 --seed 7)
    set_tests_properties(serial_target_stop PROPERTIES
        PASS_REGULAR_EXPRESSION "iterations=0 evals=8 .*success=1 fes_to_target=8 stop_reason=target stop_on_target=1")
    pso_add_gate_test(serial_iteration_limit COMMAND $<TARGET_FILE:pso_serial>
             --function sphere --particles 8 --dim 2 --max-evals 0 --max-iters 3
             --target 1e300 --seed 7 --no-early-stop)
    set_tests_properties(serial_iteration_limit PROPERTIES
        PASS_REGULAR_EXPRESSION "iterations=3 evals=32 .*success=1 fes_to_target=8 stop_reason=iteration_limit stop_on_target=0")
    pso_add_gate_test(serial_failure_fes_budget COMMAND $<TARGET_FILE:pso_serial>
             --function sphere --particles 8 --dim 2 --max-evals 33
             --target 0 --seed 7 --no-early-stop)
    set_tests_properties(serial_failure_fes_budget PROPERTIES
        PASS_REGULAR_EXPRESSION "iterations=3 evals=32 .*success=0 fes_to_target=33 stop_reason=evaluation_budget stop_on_target=0")

    # 数值金标准：可复现不变量的回归锚点。此前全库唯一的 best 钉值在
    # mpi_data_4rank_nondiv 上，串行与消融开关在 CI 中没有任何数值断言，
    # 回归不可见。以下数值均实测两次逐位一致；serial 与数据并行共享
    # namespace 0，best=10.117392270511301 与 4-rank 金标准同值互证。
    pso_add_gate_test(serial_golden_best COMMAND $<TARGET_FILE:pso_serial>
             --function sphere --particles 10 --dim 2 --max-evals 40
             --target 1e300 --seed 7 --no-early-stop)
    set_tests_properties(serial_golden_best PROPERTIES
        PASS_REGULAR_EXPRESSION "implementation=serial function=sphere particles=10 dim=2 max_evals=40 .*iterations=3 evals=40 best=10\\.117392270511301 success=1 .*timing_scope=solver elapsed_max_s=[0-9]"
        ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE")
    pso_add_gate_test(serial_linear_inertia COMMAND $<TARGET_FILE:pso_serial>
             --function sphere --particles 10 --dim 2 --max-evals 40
             --target 1e300 --seed 7 --no-early-stop --linear-inertia)
    set_tests_properties(serial_linear_inertia PROPERTIES
        PASS_REGULAR_EXPRESSION "dynamics=linear-inertia .*iterations=3 evals=40 best=9\\.047716595022596 success=1 .*timing_scope=solver elapsed_max_s=[0-9]"
        ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE")
    # 旧配置（seed 7/p10/d2）下三种边界模式逐位同值，该测试在 --boundary
    # 被静默忽略为 no-op 时照样通过，只钉住了解析与回显。现配置实测
    # clamp/reflect/reinit 两两分化（0.01404…/0.02616…/0.01384…），
    # 钉住 clamp 行为本身。
    pso_add_gate_test(serial_boundary_clamp COMMAND $<TARGET_FILE:pso_serial>
             --function sphere --particles 40 --dim 5 --max-evals 2000
             --target 1e300 --seed 42 --no-early-stop --boundary clamp)
    set_tests_properties(serial_boundary_clamp PROPERTIES
        PASS_REGULAR_EXPRESSION "boundary=clamp dynamics=constriction .*iterations=49 evals=2000 best=0\\.014040675490865866 success=1 .*timing_scope=solver elapsed_max_s=[0-9]"
        ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE")

    pso_add_gate_test(serial_history_smoke COMMAND $<TARGET_FILE:pso_serial>
             --function sphere --particles 8 --dim 2 --max-evals 32
             --target 0 --seed 7 --no-early-stop --history history_serial_smoke.log)
    set_tests_properties(serial_history_smoke PROPERTIES
        PASS_REGULAR_EXPRESSION "iterations=3 evals=32 .*success=0")
    if(Python3_Interpreter_FOUND)
        add_test(NAME result_parser_statistics
                 COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/test_plot_results.py)
        add_test(NAME serial_history_check
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/check_history.py
                         history_serial_smoke.log 3 8 4.1687829711287634)
        set_tests_properties(serial_history_check PROPERTIES
            DEPENDS serial_history_smoke)
        add_test(NAME serial_invalid_fitness
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/check_numeric_failure.py
                         -- $<TARGET_FILE:pso_serial> --function sphere
                         --particles 8 --dim 2 --max-evals 16
                         --lower 1e200 --upper 2e200 --seed 3)
        set_tests_properties(serial_invalid_fitness PROPERTIES
            TIMEOUT 20 ENVIRONMENT "OMP_NUM_THREADS=1;OMP_DYNAMIC=FALSE")
    endif()

    if(PSO_ENABLE_MPI)
        add_executable(test_mpi_reduce tests/test_mpi_reduce.c)
        target_link_libraries(test_mpi_reduce PRIVATE pso_core pso_mpi)
        target_compile_definitions(test_mpi_reduce PRIVATE PSO_WITH_MPI=1)
        add_executable(test_mpi_workflow tests/test_mpi_workflow.c)
        target_link_libraries(test_mpi_workflow PRIVATE pso_core pso_mpi)
        target_compile_definitions(test_mpi_workflow PRIVATE PSO_WITH_MPI=1)
        add_test(NAME mpi_reduce_smoke COMMAND ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 2
                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:test_mpi_reduce> ${MPIEXEC_POSTFLAGS})
        add_test(NAME mpi_workflow_4rank COMMAND ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 4
                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:test_mpi_workflow> ${MPIEXEC_POSTFLAGS})
        pso_add_gate_test(mpi_data_initial_budget COMMAND ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 2
                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:pso_mpi_ms>
                 --function sphere --particles 16 --dim 3 --max-evals 16 --seed 7
                 ${MPIEXEC_POSTFLAGS})
        set_tests_properties(mpi_data_initial_budget PROPERTIES
            PASS_REGULAR_EXPRESSION "iterations=0 evals=16")
        pso_add_gate_test(mpi_island_initial_budget COMMAND ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 2
                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:pso_mpi_island>
                 --function sphere --particles 16 --dim 3 --max-evals 16 --seed 7
                 ${MPIEXEC_POSTFLAGS})
        set_tests_properties(mpi_island_initial_budget PROPERTIES
            PASS_REGULAR_EXPRESSION "iterations=0 evals=16")

        pso_add_gate_test(mpi_data_no_early_stop COMMAND ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 2
                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:pso_mpi_ms>
                 --function sphere --particles 8 --dim 2 --max-evals 32
                 --target 1e300 --seed 7 --no-early-stop
                 --history history_mpi_ms_smoke.log ${MPIEXEC_POSTFLAGS})
        set_tests_properties(mpi_data_no_early_stop PROPERTIES
            PASS_REGULAR_EXPRESSION "threads=${PSO_TEST_THREADS} .*iterations=3 evals=32 .*best=4\\.1687829711287634 success=1 fes_to_target=8 stop_reason=evaluation_budget stop_on_target=0"
            TIMEOUT 20 ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE" PROCESSORS 4)
        pso_add_gate_test(mpi_island_no_early_stop COMMAND ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 2
                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:pso_mpi_island>
                 --function sphere --particles 8 --dim 2 --max-evals 32
                 --target 1e300 --seed 7 --no-early-stop
                 --history history_mpi_island_smoke.log ${MPIEXEC_POSTFLAGS})
        set_tests_properties(mpi_island_no_early_stop PROPERTIES
            PASS_REGULAR_EXPRESSION "threads=${PSO_TEST_THREADS} .*iterations=3 evals=32 .*best=99\\.193493785620191 success=1 fes_to_target=8 stop_reason=evaluation_budget stop_on_target=0"
            TIMEOUT 20 ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE" PROCESSORS 4)
        if(Python3_Interpreter_FOUND)
            foreach(solver pso_mpi_ms pso_mpi_island)
                add_test(NAME ${solver}_invalid_fitness
                         COMMAND ${Python3_EXECUTABLE}
                                 ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/check_numeric_failure.py
                                 -- ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 2
                                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:${solver}>
                                 ${MPIEXEC_POSTFLAGS} --function sphere
                                 --particles 8 --dim 2 --max-evals 16
                                 --lower 1e200 --upper 2e200 --seed 3)
                set_tests_properties(${solver}_invalid_fitness PROPERTIES
                    TIMEOUT 20 ENVIRONMENT "OMP_NUM_THREADS=1;OMP_DYNAMIC=FALSE")
            endforeach()
            add_test(NAME mpi_data_history_check
                     COMMAND ${Python3_EXECUTABLE}
                             ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/check_history.py
                             history_mpi_ms_smoke.log 3 8 4.1687829711287634)
            set_tests_properties(mpi_data_history_check PROPERTIES
                DEPENDS mpi_data_no_early_stop)
            add_test(NAME mpi_island_history_check
                     COMMAND ${Python3_EXECUTABLE}
                             ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/check_history.py
                             history_mpi_island_smoke.log 3 8 99.193493785620191)
            set_tests_properties(mpi_island_history_check PROPERTIES
                DEPENDS mpi_island_no_early_stop)

            # 1-rank 数据并行必须与串行逐字段一致（除实现名/ranks/threads/计时）：
            # namespace 0 + 偏移 0 是"结果不随 rank 数变化"这一不变量最便宜的
            # 可执行证明，此前任何 rank 数下都只有 4-rank 一个金标准。
            add_test(NAME rank1_data_matches_serial
                     COMMAND ${Python3_EXECUTABLE}
                             ${CMAKE_CURRENT_SOURCE_DIR}/scripts/tests/check_rank1_equivalence.py
                             --serial $<TARGET_FILE:pso_serial>
                             --mpi $<TARGET_FILE:pso_mpi_ms>
                             --mpiexec ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 1
                             --mpiexec-pre "${MPIEXEC_PREFLAGS}"
                             --mpiexec-post "${MPIEXEC_POSTFLAGS}"
                             -- --function sphere --particles 10 --dim 2 --max-evals 40
                             --target 1e300 --seed 7 --no-early-stop)
            set_tests_properties(rank1_data_matches_serial PROPERTIES
                TIMEOUT 30 ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE" PROCESSORS 4)
        endif()

        pso_add_gate_test(mpi_data_4rank_nondiv COMMAND ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 4
                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:pso_mpi_ms>
                 --function sphere --particles 10 --dim 2 --max-evals 40
                 --target 0 --seed 7 ${MPIEXEC_POSTFLAGS})
        set_tests_properties(mpi_data_4rank_nondiv PROPERTIES
            PASS_REGULAR_EXPRESSION "record=result schema=pso_v1 implementation=mpi-data-parallel .*ranks=4 .*iterations=3 .*evals=40 .*best=10\\.117392270511301"
            TIMEOUT 20 ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE" PROCESSORS 8)
        pso_add_gate_test(mpi_island_4rank_ring COMMAND ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 4
                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:pso_mpi_island>
                 --function sphere --particles 10 --dim 2 --max-evals 40
                 --target 0 --seed 7 --migration-interval 1 ${MPIEXEC_POSTFLAGS})
        set_tests_properties(mpi_island_4rank_ring PROPERTIES
            PASS_REGULAR_EXPRESSION "record=result schema=pso_v1 implementation=mpi-island function=sphere particles=10 dim=2 max_evals=40 max_iters=1000 seed=7 boundary=reflect dynamics=constriction target=0 lower=-100 upper=100 chi=0.72984000000000004 c1=1.4961800000000001 c2=1.4961800000000001 vmax_ratio=0.20000000000000001 inertia_start=0.90000000000000002 inertia_end=0.40000000000000002 ranks=4 threads=${PSO_TEST_THREADS} migration_interval=1 iterations=3 evals=40 best=63\\.342831656962169 success=0 fes_to_target=40 stop_reason=evaluation_budget stop_on_target=1 timing_scope=solver elapsed_max_s=[0-9]"
            TIMEOUT 20 ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE" PROCESSORS 8)

        # 2-rank 环形迁移金标准：size=2 时左邻与右邻是同一 rank，是环形
        # 实现最脆弱的特例，此前在全部测试中从未被实际执行过。
        pso_add_gate_test(mpi_island_2rank_migration COMMAND ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 2
                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:pso_mpi_island>
                 --function sphere --particles 10 --dim 2 --max-evals 20
                 --target 0 --seed 7 --migration-interval 1 ${MPIEXEC_POSTFLAGS})
        set_tests_properties(mpi_island_2rank_migration PROPERTIES
            PASS_REGULAR_EXPRESSION "implementation=mpi-island function=sphere particles=10 dim=2 max_evals=20 .*migration_interval=1 iterations=1 evals=20 best=205\\.84515577792911 success=0 fes_to_target=20 stop_reason=evaluation_budget stop_on_target=1 timing_scope=solver elapsed_max_s=[0-9]"
            TIMEOUT 20 ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE" PROCESSORS 4)

        # 4-rank 金标的 seed-8 变体：该 seed 下 rank 0 并不持有全局最优
        # （seed 7 的全部岛金标恰好 rank0 本地最优 == 全局最优，对"上报误查
        # 本岛最优"这类变异零判别力——变异评审实测 0/44 检出、全部漏网，
        # 本用例经拷贝变异实测变异后 best 变为 49.078714682892375，可检出）。
        pso_add_gate_test(mpi_island_4rank_global_best COMMAND ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 4
                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:pso_mpi_island>
                 --function sphere --particles 10 --dim 2 --max-evals 40
                 --target 0 --seed 8 --migration-interval 1 ${MPIEXEC_POSTFLAGS})
        set_tests_properties(mpi_island_4rank_global_best PROPERTIES
            PASS_REGULAR_EXPRESSION "record=result schema=pso_v1 implementation=mpi-island function=sphere particles=10 dim=2 max_evals=40 max_iters=1000 seed=8 boundary=reflect dynamics=constriction target=0 lower=-100 upper=100 chi=0.72984000000000004 c1=1.4961800000000001 c2=1.4961800000000001 vmax_ratio=0.20000000000000001 inertia_start=0.90000000000000002 inertia_end=0.40000000000000002 ranks=4 threads=${PSO_TEST_THREADS} migration_interval=1 iterations=3 evals=40 best=39\\.288371356974544 success=0 fes_to_target=40 stop_reason=evaluation_budget stop_on_target=1 timing_scope=solver elapsed_max_s=[0-9]"
            TIMEOUT 20 ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE" PROCESSORS 8)

        # 半非有限岛金标：±2e154 域下部分初始粒子平方溢出（归一为 +inf），
        # 实测 4 rank 中 3 rank 有死亡粒子（10 粒子死 6 个）且全局 best 有限
        # ——三个 *_invalid_fitness 全 rank 同非有限、在 init 即退出，对
        # "带死亡粒子进主循环 + 迁移"零判别力，
        # 本用例补上该路径（seed 3，与 invalid_fitness 簇同 seed 不同域）。
        pso_add_gate_test(mpi_island_4rank_partial_nonfinite COMMAND ${MPIEXEC_EXECUTABLE} ${MPIEXEC_NUMPROC_FLAG} 4
                 ${MPIEXEC_PREFLAGS} $<TARGET_FILE:pso_mpi_island>
                 --function sphere --particles 10 --dim 2 --max-evals 40
                 --target 0 --seed 3 --lower -2e154 --upper 2e154
                 --migration-interval 1 ${MPIEXEC_POSTFLAGS})
        set_tests_properties(mpi_island_4rank_partial_nonfinite PROPERTIES
            PASS_REGULAR_EXPRESSION "record=result schema=pso_v1 implementation=mpi-island function=sphere particles=10 dim=2 max_evals=40 max_iters=1000 seed=3 boundary=reflect dynamics=constriction target=0 lower=-2\\.0000000000000001e\\+154 upper=2\\.0000000000000001e\\+154 chi=0.72984000000000004 c1=1.4961800000000001 c2=1.4961800000000001 vmax_ratio=0.20000000000000001 inertia_start=0.90000000000000002 inertia_end=0.40000000000000002 ranks=4 threads=${PSO_TEST_THREADS} migration_interval=1 iterations=3 evals=40 best=2\\.4444982668108198e\\+305 success=0 fes_to_target=40 stop_reason=evaluation_budget stop_on_target=1 timing_scope=solver elapsed_max_s=[0-9]"
            TIMEOUT 20 ENVIRONMENT "OMP_NUM_THREADS=2;OMP_DYNAMIC=FALSE" PROCESSORS 8)

        pso_add_gate_test(mpi_help_mode COMMAND $<TARGET_FILE:pso_mpi_ms> --help)
        set_tests_properties(mpi_help_mode PROPERTIES
            PASS_REGULAR_EXPRESSION "MPI mode: total particles are divided across ranks")
        pso_add_gate_test(island_help_mode COMMAND $<TARGET_FILE:pso_mpi_island> --help)
        set_tests_properties(island_help_mode PROPERTIES
            PASS_REGULAR_EXPRESSION "Island mode: best-worst migration on a unidirectional ring")
    endif()
endif()

message(STATUS "PSO_ENABLE_MPI=${PSO_ENABLE_MPI}")
message(STATUS "OpenMP_C_FOUND=${OpenMP_C_FOUND}")
