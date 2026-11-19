/* test_pin.c — pso_pin 的拓扑映射与真绑定验证 */
/* glibc 的 sched_[gs]etaffinity 宏需 _GNU_SOURCE */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include "pso_pin.h"

#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#include <windows.h>
#else
#include <sched.h>
#endif

/* 亲和性集合按平台原样保存：任何"压进 64 位掩码"的中间态都会在
 * CPU_SETSIZE(1024) 或 >64 逻辑 CPU 的机器上截断/回绕。 */
#ifdef _WIN32
typedef DWORD_PTR pso_test_affinity;
#else
typedef cpu_set_t pso_test_affinity;
#endif

static int current_affinity(pso_test_affinity *set)
{
#ifdef _WIN32
    DWORD_PTR system_mask = 0;
    return GetProcessAffinityMask(GetCurrentProcess(), set, &system_mask)
               ? 0 : -1;
#else
    CPU_ZERO(set);
    return sched_getaffinity(0, sizeof(*set), set) == 0 ? 0 : -1;
#endif
}

static int affinity_count(const pso_test_affinity *set)
{
    int n = 0;
    int i;
#ifdef _WIN32
    for (i = 0; i < 64; ++i) {
        if (*set & (((DWORD_PTR)1) << i)) ++n;
    }
#else
    for (i = 0; i < CPU_SETSIZE; ++i) {
        if (CPU_ISSET(i, set)) ++n;
    }
#endif
    return n;
}

static int affinity_has(const pso_test_affinity *set, int cpu)
{
#ifdef _WIN32
    if (cpu < 0 || cpu >= 64) {
        return 0;
    }
    return (*set & (((DWORD_PTR)1) << cpu)) ? 1 : 0;
#else
    if (cpu < 0 || cpu >= CPU_SETSIZE) {
        return 0;
    }
    return CPU_ISSET(cpu, set) ? 1 : 0;
#endif
}

static int restore_affinity(const pso_test_affinity *set)
{
#ifdef _WIN32
    return SetProcessAffinityMask(GetCurrentProcess(), *set) ? 0 : -1;
#else
    return sched_setaffinity(0, sizeof(*set), set) == 0 ? 0 : -1;
#endif
}

static int expect_cpus(const int *cpus, int count)
{
    pso_test_affinity live;
    int live_count;
    int i;
    if (current_affinity(&live) != 0) {
        fprintf(stderr, "cannot query current affinity\n");
        return 1;
    }
    live_count = affinity_count(&live);
    if (live_count != count) {
        fprintf(stderr, "affinity count %d != expected %d\n",
                live_count, count);
        return 1;
    }
    for (i = 0; i < count; ++i) {
        if (!affinity_has(&live, cpus[i])) {
            fprintf(stderr, "cpu %d not pinned\n", cpus[i]);
            return 1;
        }
    }
    return 0;
}

static int test_assign_spread(void)
{
    /* 模拟混合拓扑：两个双线程核（P）+ 两个单线程核（E） */
    pso_pin_core cores[4];
    int cpus[8];

    cores[0].logicals[0] = 0; cores[0].logicals[1] = 1; cores[0].count = 2;
    cores[1].logicals[0] = 2; cores[1].logicals[1] = 3; cores[1].count = 2;
    cores[2].logicals[0] = 4; cores[2].count = 1;
    cores[3].logicals[0] = 5; cores[3].count = 1;

    /* 单线程 rank 依次占据不同物理核（宽度降序稳定排序后轮转） */
    if (pso_pin_assign_spread(0, 1, cores, 4, cpus, 8) != 1 || cpus[0] != 0) {
        fprintf(stderr, "spread r0t1 expected cpu0\n");
        return 1;
    }
    if (pso_pin_assign_spread(1, 1, cores, 4, cpus, 8) != 1 || cpus[0] != 2) {
        fprintf(stderr, "spread r1t1 expected cpu2 (second P core)\n");
        return 1;
    }
    /* i=4 已超过核数：回到 core0 的第 1 槽（SMT 兄弟） */
    if (pso_pin_assign_spread(4, 1, cores, 4, cpus, 8) != 1 || cpus[0] != 1) {
        fprintf(stderr, "spread r4t1 expected sibling slot cpu1\n");
        return 1;
    }
    /* i=6 轮转到第一个 E 核 */
    if (pso_pin_assign_spread(6, 1, cores, 4, cpus, 8) != 1 || cpus[0] != 4) {
        fprintf(stderr, "spread r6t1 expected first E core cpu4\n");
        return 1;
    }
    /* 多线程 rank 铺开到不同物理核：rank0 t2 -> {0,2} */
    if (pso_pin_assign_spread(0, 2, cores, 4, cpus, 8) != 2 ||
        cpus[0] != 0 || cpus[1] != 2) {
        fprintf(stderr, "spread r0t2 expected {0,2}\n");
        return 1;
    }
    /* 超订阅环绕：rank7 t2 -> i=14,15 -> core2(cpu4), core3(cpu5) */
    if (pso_pin_assign_spread(7, 2, cores, 4, cpus, 8) != 2 ||
        cpus[0] != 4 || cpus[1] != 5) {
        fprintf(stderr, "spread r7t2 expected {4,5}\n");
        return 1;
    }
    /* 非法参数 */
    if (pso_pin_assign_spread(-1, 1, cores, 4, cpus, 8) != -1 ||
        pso_pin_assign_spread(0, 0, cores, 4, cpus, 8) != -1 ||
        pso_pin_assign_spread(0, 2, cores, 4, cpus, 1) != -1 ||
        pso_pin_assign_spread(0, 1, cores, 0, cpus, 8) != -1) {
        fprintf(stderr, "assign_spread must reject invalid arguments\n");
        return 1;
    }
    return 0;
}

static int test_assign_flat(void)
{
    int cpus[8];
    /* 平铺（每逻辑 CPU 一核）：rank2 t2 -> 全局 i=4,5 -> {4,5} */
    if (pso_pin_assign(2, 2, 8, cpus, 8) != 2 ||
        cpus[0] != 4 || cpus[1] != 5) {
        fprintf(stderr, "flat r2t2 expected {4,5}\n");
        return 1;
    }
    /* 超订阅环绕：rank5 t4 -> 全局 i=20..23 % 8 -> {4,5,6,7} */
    if (pso_pin_assign(5, 4, 8, cpus, 8) != 4 ||
        cpus[0] != 4 || cpus[1] != 5 || cpus[2] != 6 || cpus[3] != 7) {
        fprintf(stderr, "flat r5t4 expected {4,5,6,7}\n");
        return 1;
    }
    if (pso_pin_assign(-1, 1, 8, cpus, 8) != -1 ||
        pso_pin_assign(0, 0, 8, cpus, 8) != -1 ||
        pso_pin_assign(0, 2, 8, cpus, 1) != -1 ||
        pso_pin_assign(0, 1, 0, cpus, 8) != -1) {
        fprintf(stderr, "flat assign must reject invalid arguments\n");
        return 1;
    }
    return 0;
}

static int test_real_pinning(void)
{
    pso_pin_core cores[PSO_PIN_MAX_CORES];
    int num_cores;
    pso_test_affinity saved;
    int cpus[8];
    char error[128];
    int failed = 0;

    if (current_affinity(&saved) != 0) {
        fprintf(stderr, "cannot read initial affinity\n");
        return 1;
    }
    num_cores = pso_pin_query_cores(cores, PSO_PIN_MAX_CORES);
    if (num_cores < 0) {
        fprintf(stderr, "core topology data is malformed\n");
        return 1;
    }
    if (num_cores == 0) {
#if !PSO_PIN_HAS_BACKEND
        printf("no affinity backend on this platform: real-pinning checks skipped\n");
        return 0;
#else
        fprintf(stderr, "cannot query core topology\n");
        return 1;
#endif
    }
    /* 单线程：rank0 绑到排序后第一个物理核的最低逻辑 CPU */
    if (pso_pin_current_process(0, 1, error, sizeof(error)) != PSO_PIN_OK) {
        fprintf(stderr, "pin rank0 failed: %s\n", error);
        failed = 1;
        goto restore;
    }
    if (pso_pin_assign_spread(0, 1, cores, num_cores, cpus, 8) != 1) {
        fprintf(stderr, "assign_spread rank0 failed\n");
        failed = 1;
        goto restore;
    }
    if (expect_cpus(cpus, 1) != 0) {
        failed = 1;
        goto restore;
    }
    /* 双线程：rank1 期望的两个 CPU 必须来自不同物理核 */
    if (pso_pin_current_process(1, 2, error, sizeof(error)) != PSO_PIN_OK) {
        fprintf(stderr, "pin rank1 threads2 failed: %s\n", error);
        failed = 1;
        goto restore;
    }
    if (pso_pin_assign_spread(1, 2, cores, num_cores, cpus, 8) != 2) {
        fprintf(stderr, "assign_spread rank1 t2 failed\n");
        failed = 1;
        goto restore;
    }
    if (expect_cpus(cpus, 2) != 0) {
        failed = 1;
        goto restore;
    }

restore:
    if (restore_affinity(&saved) != 0) {
        fprintf(stderr, "FATAL: cannot restore affinity mask\n");
        return 1;
    }
    return failed;
}

static int test_threads_exceed_limit(void)
{
    char error[128];
    /* threads 超过 PSO_PIN_MAX_CORES 必须返回 PSO_PIN_FAILED：参数校验
     * 位于平台守卫之前（非法实参与平台无关），故此分支跨平台确定性可达。 */
    if (pso_pin_current_process(0, PSO_PIN_MAX_CORES + 1, error, sizeof(error))
        != PSO_PIN_FAILED) {
        fprintf(stderr, "threads > PSO_PIN_MAX_CORES must fail with PSO_PIN_FAILED\n");
        return 1;
    }
    return 0;
}

int main(void)
{
    if (test_assign_spread() != 0) return 1;
    if (test_assign_flat() != 0) return 1;
    if (test_threads_exceed_limit() != 0) return 1;
    if (test_real_pinning() != 0) return 1;
    printf("pin tests passed\n");
    return 0;
}
