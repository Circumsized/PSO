/* pso_core.h — 串行、MPI 与 MPI+OpenMP 共用的 PSO 内核 */
#ifndef PSO_CORE_H
#define PSO_CORE_H

#include <stdint.h>
#include "pso_config.h"
#include "pso_rng.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int count;
    int dim;
    pso_fn_id function;
    double lower;
    double upper;
    pso_boundary_mode boundary;
    double chi;
    double c1;
    double c2;
    double vmax_ratio;
    int use_linear_inertia;
    double inertia_start;
    double inertia_end;

    /* 连续行主序：position[i*dim+j]，便于缓存访问和向量化。 */
    double *position;
    double *velocity;
    double *pbest_position;
    double *fitness;
    double *pbest_fitness;
    double *best_position;
    double best_fitness;
    int best_index;
    /* 初始化时适应度非有限（已归一为 +INFINITY）的粒子数；仅供入口做
     * stderr 提示，不进入 pso_v1 结果行契约。 */
    int nonfinite_count;

    /* 每粒子 RNG 状态：索引与粒子固定绑定，因此 OpenMP 线程数/调度变化不
     * 会改变随机序列；range 初始化器用 namespace 和全局粒子索引派生流。 */
    pso_rng_t *rngs;
    int rng_count;
} pso_swarm;

/* Legacy initializer: particle i keeps the historical uint64_t stream mapping
 * stream_id * 1048576 + i (mod 2^64) over the full stream_id/count domain. */
int pso_swarm_init(pso_swarm *swarm, const pso_options *options,
                   int count, uint64_t base_seed, uint64_t stream_id);
/* Range initializer: each particle is keyed by the semantic pair
 * (stream_namespace, global_particle_offset+i), with uint64_t addition defined
 * modulo 2^64 at the offset boundary. For a fixed namespace, distinct uint64_t
 * indices map to distinct streams; chunking any non-wrapping global range is
 * stable. This mapping is intentionally independent of the legacy wrapper. */
int pso_swarm_init_range(pso_swarm *swarm, const pso_options *options,
                         int count, uint64_t base_seed,
                         uint64_t stream_namespace,
                         uint64_t global_particle_offset);
int pso_partition_count(int total, int rank, int size);
void pso_swarm_destroy(pso_swarm *swarm);

/* 以 social_best 为社会项更新一代。social_best 必须在调用期间保持不变；
 * 传 NULL 时使用本岛的当前 best_position。 */
void pso_swarm_step(pso_swarm *swarm, const double *social_best,
                    int iteration, int total_iterations);

void pso_swarm_recompute_best(pso_swarm *swarm);
const double *pso_swarm_best_position(const pso_swarm *swarm);
int pso_swarm_worst_index(const pso_swarm *swarm);

/* 岛模型迁移：仅当 migrant_fit 优于本岛最差粒子时替换它，并同步更新
 * position / velocity / pbest_position / pbest_fitness 四元状态。 */
int pso_swarm_inject_migrant(pso_swarm *swarm, const double *migrant_position,
                             double migrant_fit);

/* 跨平台单调墙钟；MPI 版本使用 MPI_Wtime，主程序也可调用它做本地计时。 */
double pso_wall_time(void);

#ifdef __cplusplus
}
#endif

#endif /* PSO_CORE_H */
