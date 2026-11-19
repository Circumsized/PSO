/* pso_pin.c — --pin-cpus 的平台实现 */
/* glibc 的 CPU_ZERO/CPU_SET/sched_setaffinity 需要 _GNU_SOURCE */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include "pso_pin.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

#ifdef _WIN32
#include <windows.h>
#else
#include <errno.h>
#include <unistd.h>
#ifdef __linux__
#include <sched.h>
#define PSO_PIN_HAS_SCHED 1
#endif
#endif

/* 头文件的 PSO_PIN_HAS_BACKEND 与本文件实际取到的后端必须一致，否则
 * 调用方会拿到错误的"平台不支持"诊断。 */
#if PSO_PIN_HAS_BACKEND && !defined(_WIN32) && !defined(PSO_PIN_HAS_SCHED)
#error "PSO_PIN_HAS_BACKEND disagrees with the available affinity backend"
#endif

static void fail_with(char *error, size_t error_size, const char *text)
{
    if (error != NULL && error_size > 0) {
        snprintf(error, error_size, "%s", text);
    }
}

/* 稳定排序核表：兄弟数多的核在前（P 核优先），同数按原枚举序。 */
static void sort_cores_by_width(pso_pin_core *cores, int num_cores)
{
    int i;
    for (i = 1; i < num_cores; ++i) {
        pso_pin_core key = cores[i];
        int j = i - 1;
        while (j >= 0 && cores[j].count < key.count) {
            cores[j + 1] = cores[j];
            --j;
        }
        cores[j + 1] = key;
    }
}

int pso_pin_assign_spread(int rank, int threads,
                          const pso_pin_core *cores, int num_cores,
                          int *cpus, int cpus_capacity)
{
    int i;
    pso_pin_core sorted[PSO_PIN_MAX_CORES];
    if (num_cores <= 0 || num_cores > PSO_PIN_MAX_CORES || threads <= 0 ||
        rank < 0 || cores == NULL || cpus == NULL ||
        cpus_capacity < threads) {
        return -1;
    }
    memcpy(sorted, cores, (size_t)num_cores * sizeof(*sorted));
    sort_cores_by_width(sorted, num_cores);
    for (i = 0; i < threads; ++i) {
        int global = rank * threads + i;
        pso_pin_core *core = &sorted[global % num_cores];
        if (core->count <= 0) {
            return -1;
        }
        cpus[i] = core->logicals[(global / num_cores) % core->count];
    }
    return threads;
}

int pso_pin_assign(int rank, int threads, int total_cpus,
                   int *cpus, int cpus_capacity)
{
    pso_pin_core flat[PSO_PIN_MAX_CORES];
    int i;
    if (total_cpus <= 0 || total_cpus > PSO_PIN_MAX_CORES) {
        return -1;
    }
    for (i = 0; i < total_cpus; ++i) {
        flat[i].logicals[0] = i;
        flat[i].count = 1;
    }
    return pso_pin_assign_spread(rank, threads, flat, total_cpus,
                                 cpus, cpus_capacity);
}

#ifdef _WIN32
int pso_pin_query_cores(pso_pin_core *cores, int capacity)
{
    SYSTEM_LOGICAL_PROCESSOR_INFORMATION *info = NULL;
    DWORD bytes = 0;
    DWORD count;
    DWORD i;
    int num_cores = 0;
    int bit;

    if (cores == NULL || capacity <= 0) {
        return 0;
    }
    GetLogicalProcessorInformation(NULL, &bytes);
    if (bytes == 0) {
        return 0;
    }
    info = (SYSTEM_LOGICAL_PROCESSOR_INFORMATION *)malloc(bytes);
    if (info == NULL) {
        return 0;
    }
    if (!GetLogicalProcessorInformation(info, &bytes)) {
        free(info);
        return 0;
    }
    count = bytes / sizeof(*info);
    for (i = 0; i < count; ++i) {
        if (info[i].Relationship != RelationProcessorCore) {
            continue;
        }
        if (num_cores >= capacity) {
            break;
        }
        cores[num_cores].count = 0;
        for (bit = 0; bit < 64; ++bit) {
            ULONG_PTR bit_mask = ((ULONG_PTR)1) << bit;
            if (info[i].ProcessorMask & bit_mask) {
                if (cores[num_cores].count < PSO_PIN_MAX_SIBLINGS) {
                    cores[num_cores].logicals[cores[num_cores].count++] = bit;
                }
            }
        }
        if (cores[num_cores].count > 0) {
            ++num_cores;
        }
    }
    free(info);
    return num_cores;
}
#elif defined(PSO_PIN_HAS_SCHED)
/* /sys/devices/system/cpu/cpuN/topology/thread_siblings_list，
 * 形如 "0,2" 或 "0-3"；以最小兄弟 CPU 为核的去重键。 */
static int parse_sibling_token(const char *text, int *first, int *last)
{
    char *end;
    long head;
    long tail;

    errno = 0;
    head = strtol(text, &end, 10);
    if (end == text || errno == ERANGE || head < 0 || head >= PSO_PIN_MAX_CORES) {
        return -1;
    }
    *first = (int)head;
    *last = (int)head;
    if (*end == '-') {
        const char *digits = end + 1;
        errno = 0;
        tail = strtol(digits, &end, 10);
        if (end == digits || errno == ERANGE || tail < head
            || tail >= PSO_PIN_MAX_CORES || tail - head >= PSO_PIN_MAX_SIBLINGS) {
            return -1;
        }
        *last = (int)tail;
    }
    /* 调用方按逗号切分，所以 token 末尾允许逗号、换行或串尾。 */
    if (*end != '\0' && *end != ',' && *end != '\n' && *end != '\r') {
        return -1;
    }
    return 0;
}

/* 返回 0=解析成功，1=无拓扑数据（回退为每逻辑 CPU 独立成核），
 * -1=数据存在但畸形。 */
static int sysfs_core_of_cpu(int cpu, int *core)
{
    char path[128];
    char text[128];
    FILE *file;
    int first, last, sibling;
    int min_sibling = INT_MAX;
    char *cursor;
    char *comma;

    *core = cpu;
    snprintf(path, sizeof(path),
             "/sys/devices/system/cpu/cpu%d/topology/thread_siblings_list",
             cpu);
    file = fopen(path, "r");
    if (file == NULL) {
        return 1;  /* 无 sysfs 信息（如精简容器）：每逻辑 CPU 独立成核 */
    }
    if (fgets(text, sizeof(text), file) == NULL) {
        fclose(file);
        return 1;
    }
    fclose(file);

    cursor = text;
    while (1) {
        comma = strchr(cursor, ',');
        if (parse_sibling_token(cursor, &first, &last) != 0) {
            return -1;
        }
        for (sibling = first; sibling <= last; ++sibling) {
            if (sibling < min_sibling) {
                min_sibling = sibling;
            }
        }
        if (comma == NULL) {
            break;
        }
        cursor = comma + 1;
    }
    *core = min_sibling;
    return 0;
}

static int cpu_present(int cpu)
{
    char path[128];
    snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%d", cpu);
    return access(path, F_OK) == 0;
}

int pso_pin_query_cores(pso_pin_core *cores, int capacity)
{
    int cpu;
    int num_cores = 0;
    /* 去重键与 logicals[0] 必须是两个位置：cpuset 受限时会先枚举到非
     * 最小兄弟的 CPU，键若存在 logicals[0] 里会立刻被覆盖而把同一物理核
     * 拆成多个核。 */
    int key[PSO_PIN_MAX_CORES];

    /* 扫描 cpuN 目录而非信任 sysconf 计数：cpuset/NUMA 热拔插会让
     * 编号不连续。 */
    if (cores == NULL || capacity <= 0) {
        return 0;
    }
    if (capacity > PSO_PIN_MAX_CORES) {
        capacity = PSO_PIN_MAX_CORES;
    }
    for (cpu = 0; cpu < PSO_PIN_MAX_CORES; ++cpu) {
        int core_index;
        int owner = -1;
        int i;
        if (!cpu_present(cpu)) {
            continue;
        }
        if (sysfs_core_of_cpu(cpu, &core_index) < 0) {
            return -1;
        }
        for (i = 0; i < num_cores; ++i) {
            if (key[i] == core_index) {
                owner = i;
                break;
            }
        }
        if (owner < 0) {
            if (num_cores >= capacity) {
                break;
            }
            owner = num_cores++;
            key[owner] = core_index;
            cores[owner].count = 0;
        }
        if (cores[owner].count < PSO_PIN_MAX_SIBLINGS) {
            cores[owner].logicals[cores[owner].count++] = cpu;
        }
    }
    return num_cores;
}
#else
int pso_pin_query_cores(pso_pin_core *cores, int capacity)
{
    (void)cores;
    (void)capacity;
    return 0;
}
#endif

int pso_pin_current_process(int rank, int threads,
                            char *error, size_t error_size)
{
    pso_pin_core cores[PSO_PIN_MAX_CORES];
    int num_cores;
    int assigned;
    int cpus[PSO_PIN_MAX_CORES];
    int i;

    if (error != NULL && error_size > 0) {
        error[0] = '\0';
    }
    /* 参数校验先于平台守卫：threads 超限是平台无关的非法实参，不能因
     * Windows 多组守卫在前面而变成 UNSUPPORTED（那会把"参数错了"误报成
     * "平台不支持"）。 */
    if (threads > PSO_PIN_MAX_CORES) {
        fail_with(error, error_size, "threads exceed pin limit");
        return PSO_PIN_FAILED;
    }
#ifdef _WIN32
    if (GetMaximumProcessorGroupCount() > 1 ||
        GetActiveProcessorCount(ALL_PROCESSOR_GROUPS) > 64) {
        /* ProcessorMask 的位序号只在组内等于逻辑 CPU 号，跨组时整张核表
         * 都会错位；宁可不绑，也不能误绑。 */
        fail_with(error, error_size,
                  "Windows processor-group topology not supported by --pin-cpus");
        return PSO_PIN_UNSUPPORTED;
    }
#endif
    num_cores = pso_pin_query_cores(cores, PSO_PIN_MAX_CORES);
    if (num_cores < 0) {
        fail_with(error, error_size, "malformed core topology in sysfs");
        return PSO_PIN_FAILED;
    }
    if (num_cores == 0) {
        /* 无后端平台上 query_cores 是恒返 0 的桩，必须与"拓扑查询失败"
         * 区分开，否则诊断会把平台缺能力说成机器坏了。 */
        fail_with(error, error_size, PSO_PIN_HAS_BACKEND
                  ? "cannot query core topology"
                  : "affinity pinning not supported on this platform");
        return PSO_PIN_HAS_BACKEND ? PSO_PIN_FAILED : PSO_PIN_UNSUPPORTED;
    }
    assigned = pso_pin_assign_spread(rank, threads, cores, num_cores,
                                     cpus, PSO_PIN_MAX_CORES);
    if (assigned < 0) {
        fail_with(error, error_size, "invalid rank or threads for pinning");
        return PSO_PIN_FAILED;
    }

#ifdef _WIN32
    {
        DWORD_PTR mask = 0;
        for (i = 0; i < assigned; ++i) {
            /* 多组机器已在函数入口被拒，此处是断言而非可达分支：
               query_cores 只会产出组内位号，恒 < 64。 */
            if (cpus[i] >= 64) {
                fail_with(error, error_size,
                          "logical CPU exceeds single-group 64-bit mask");
                return PSO_PIN_FAILED;
            }
            mask |= ((DWORD_PTR)1) << cpus[i];
        }
        if (!SetProcessAffinityMask(GetCurrentProcess(), mask)) {
            if (error != NULL && error_size > 0) {
                snprintf(error, error_size,
                         "SetProcessAffinityMask failed (error %lu)",
                         (unsigned long)GetLastError());
            }
            return PSO_PIN_FAILED;
        }
    }
    return PSO_PIN_OK;
#elif defined(PSO_PIN_HAS_SCHED)
    {
        cpu_set_t set;
        cpu_set_t check;
        CPU_ZERO(&set);
        for (i = 0; i < assigned; ++i) {
            CPU_SET(cpus[i], &set);
        }
        if (sched_setaffinity(0, sizeof(set), &set) != 0) {
            if (error != NULL && error_size > 0) {
                snprintf(error, error_size, "sched_setaffinity failed: %s",
                         strerror(errno));
            }
            return PSO_PIN_FAILED;
        }
        /* sched_setaffinity 会静默地与容器/任务的 cpuset 相交——回读
         * 生效掩码，请求的 CPU 有一个缺位即视为失败（不允许静默
         * 退化为未绑定）。 */
        CPU_ZERO(&check);
        if (sched_getaffinity(0, sizeof(check), &check) != 0) {
            fail_with(error, error_size,
                      "sched_getaffinity read-back failed");
            return PSO_PIN_FAILED;
        }
        for (i = 0; i < assigned; ++i) {
            if (!CPU_ISSET(cpus[i], &check)) {
                fail_with(error, error_size,
                          "requested CPU not in effective affinity"
                          " (cpuset restriction?)");
                return PSO_PIN_FAILED;
            }
        }
    }
    return PSO_PIN_OK;
#else
    /* 编译期兜底：无后端平台已在 num_cores == 0 处返回 UNSUPPORTED，
     * 走不到这里，但函数必须有返回路径。 */
    (void)rank;
    (void)cpus;
    (void)i;
    fail_with(error, error_size,
              "affinity pinning not supported on this platform");
    return PSO_PIN_UNSUPPORTED;
#endif
}
