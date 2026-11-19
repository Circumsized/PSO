/* pso_config.h — 运行时参数与默认配置 */
#ifndef PSO_CONFIG_H
#define PSO_CONFIG_H

#include <stdint.h>
#include <stddef.h>
#include "pso_bench.h"

#define PSO_DEFAULT_PARTICLES       20000
#define PSO_DEFAULT_DIM             50
#define PSO_DEFAULT_MAX_EVALS       UINT64_C(1000000)
#define PSO_DEFAULT_MAX_ITERS       1000
#define PSO_DEFAULT_TARGET          1.0e-8
#define PSO_DEFAULT_SEED            UINT64_C(20240229)
#define PSO_DEFAULT_MIGRATION       10
#define PSO_DEFAULT_REPORT_INTERVAL 10

/* 标准模式中 chi 仅作用于惯性项，c1/c2 是已包含收缩效果的有效加速度
 * 系数；--linear-inertia 可切换到 w=0.9 -> 0.4 的对照策略。 */
#define PSO_DEFAULT_CHI             0.72984
#define PSO_DEFAULT_C1              1.49618
#define PSO_DEFAULT_C2              1.49618
#define PSO_DEFAULT_VMAX_RATIO      0.20
#define PSO_DEFAULT_INERTIA_START   0.90
#define PSO_DEFAULT_INERTIA_END     0.40

typedef enum {
    PSO_BOUNDARY_REFLECT = 0,
    PSO_BOUNDARY_CLAMP,
    PSO_BOUNDARY_REINIT
} pso_boundary_mode;

typedef struct {
    int particles;                 /* 串行总粒子数 / MPI 总粒子数 */
    int dim;
    uint64_t max_evals;            /* >0 时优先，按总粒子数换算代数 */
    int max_iters;                 /* max_evals=0 时使用 */
    double target;
    pso_fn_id function;
    double lower;
    double upper;
    uint64_t seed;
    int migration_interval;
    int report_interval;
    int verbose;
    int use_linear_inertia;
    int stop_on_target;
    const char *history_path;
    int pin_cpus;                  /* --pin-cpus：绑定到固定逻辑 CPU 块 */
    pso_boundary_mode boundary;
    double chi;
    double c1;
    double c2;
    double vmax_ratio;
    double inertia_start;
    double inertia_end;
} pso_options;

void pso_options_init(pso_options *options);

/* 返回在统一评估预算下各实现应执行的迭代数；若预算只够初始种群则返回 0。 */
int pso_options_iteration_limit(const pso_options *options);

const char *pso_boundary_name(pso_boundary_mode mode);

#endif /* PSO_CONFIG_H */
