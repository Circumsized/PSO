/* pso_pin.h — 进程级 CPU 亲和性绑定（--pin-cpus）
 *
 * 拓扑感知绑核：先枚举物理核（每核一个"兄弟组"），按兄弟数降序
 * （性能核优先、同组内按枚举序稳定排序）得到核序列，再把全局线程
 * 索引 i = rank*threads + k 轮转铺开：
 *   核 = sorted_cores[i % 核数]，槽位 = i / 核数（超出该核逻辑 CPU 数时取模）。
 * 这样 R*T ≤ 核数时每线程独占一个物理核，不共享 SMT 兄弟；混合架构
 * （如 P+E 核）下优先使用核数多的物理核。协议边界见 docs/03。 */
#ifndef PSO_PIN_H
#define PSO_PIN_H

#include <stddef.h>

#define PSO_PIN_OK           0
#define PSO_PIN_UNSUPPORTED -1   /* 平台无亲和性系统调用 */
#define PSO_PIN_FAILED      -2   /* 查询失败或系统调用拒绝 */

#define PSO_PIN_MAX_CORES 256
#define PSO_PIN_MAX_SIBLINGS 8

/* 本机是否存在可用的亲和性后端。无后端时 pso_pin_current_process 必须
 * 返回 PSO_PIN_UNSUPPORTED（平台根本没有这个调用），而不是与"拓扑查询
 * 失败"混同为 PSO_PIN_FAILED。 */
#if defined(_WIN32) || defined(__linux__)
#define PSO_PIN_HAS_BACKEND 1
#else
#define PSO_PIN_HAS_BACKEND 0
#endif

/* 一个物理核（兄弟组）：logicals 为该核的逻辑 CPU 编号，count 为个数。 */
typedef struct {
    int logicals[PSO_PIN_MAX_SIBLINGS];
    int count;
} pso_pin_core;

/* 纯计算：给定核表（可乱序），先按 count 降序稳定排序，再按全局线程
 * 索引轮转分配。返回写入 cpus 的元素数；参数非法或容量不足返回 -1。 */
int pso_pin_assign_spread(int rank, int threads,
                          const pso_pin_core *cores, int num_cores,
                          int *cpus, int cpus_capacity);

/* 便捷语义（保留旧接口）：把逻辑 CPU 视为每核单线程的平铺核表。 */
int pso_pin_assign(int rank, int threads, int total_cpus,
                   int *cpus, int cpus_capacity);

/* 查询本机物理核表。返回核数；无后端或无拓扑数据返回 0；
 * 拓扑数据存在但畸形返回 -1（此时解析器已被证伪，调用方不得
 * 静默退化为"每逻辑 CPU 独立成核"）。 */
int pso_pin_query_cores(pso_pin_core *cores, int capacity);

/* 绑定当前进程；threads 通常传 omp_get_max_threads()（无 OpenMP 时 1）。
 * 失败时把原因写入 error（可为 NULL）。绑定失败必须视为致命错误：
 * 静默以未绑定状态继续会污染计时测量。 */
int pso_pin_current_process(int rank, int threads,
                            char *error, size_t error_size);

#endif
