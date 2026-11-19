#include "pso_config.h"

#include <limits.h>
#include <stddef.h>

void pso_options_init(pso_options *options)
{
    const pso_fn_info *info;
    if (options == NULL) {
        return;
    }

    options->particles = PSO_DEFAULT_PARTICLES;
    options->dim = PSO_DEFAULT_DIM;
    options->max_evals = PSO_DEFAULT_MAX_EVALS;
    options->max_iters = PSO_DEFAULT_MAX_ITERS;
    options->target = PSO_DEFAULT_TARGET;
    options->function = PSO_FN_RASTRIGIN;
    options->seed = PSO_DEFAULT_SEED;
    options->migration_interval = PSO_DEFAULT_MIGRATION;
    options->report_interval = PSO_DEFAULT_REPORT_INTERVAL;
    options->verbose = 0;
    options->use_linear_inertia = 0;
    options->stop_on_target = 1;
    options->history_path = NULL;
    options->pin_cpus = 0;
    options->boundary = PSO_BOUNDARY_REFLECT;
    options->chi = PSO_DEFAULT_CHI;
    options->c1 = PSO_DEFAULT_C1;
    options->c2 = PSO_DEFAULT_C2;
    options->vmax_ratio = PSO_DEFAULT_VMAX_RATIO;
    options->inertia_start = PSO_DEFAULT_INERTIA_START;
    options->inertia_end = PSO_DEFAULT_INERTIA_END;

    info = pso_bench_info(options->function);
    options->lower = info != NULL ? info->lower : -5.12;
    options->upper = info != NULL ? info->upper : 5.12;
}

int pso_options_iteration_limit(const pso_options *options)
{
    uint64_t iterations;
    if (options == NULL) {
        return 1;
    }
    if (options->max_evals > 0 && options->particles > 0) {
        /* 初始种群已经消耗 particles 次评估；每一代也消耗同样数量。
         * 取整向下，确保报告中的 evals 不超过 max_evals。 */
        if (options->max_evals <= (uint64_t)options->particles) {
            return 0;
        }
        iterations = (options->max_evals - (uint64_t)options->particles)
                   / (uint64_t)options->particles;
        if (iterations > (uint64_t)INT_MAX) {
            return INT_MAX;
        }
        return (int)iterations;
    }
    return options->max_iters > 0 ? options->max_iters : 1;
}

const char *pso_boundary_name(pso_boundary_mode mode)
{
    switch (mode) {
    case PSO_BOUNDARY_REFLECT:
        return "reflect";
    case PSO_BOUNDARY_CLAMP:
        return "clamp";
    case PSO_BOUNDARY_REINIT:
        return "reinit";
    default:
        return "unknown";
    }
}
