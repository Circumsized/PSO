#include "pso_bench.h"

#include <float.h>
#include <math.h>
#include <stddef.h>
#include <string.h>

#ifndef PSO_PI
#define PSO_PI 3.141592653589793238462643383279502884
#endif

static const pso_fn_info PSO_FUNCTIONS[PSO_FN_COUNT] = {
    {"sphere",     -100.0,   100.0,    0.0, 0.0},
    {"rosenbrock", -100.0,   100.0,    0.0, 1.0},
    {"rastrigin",  -5.12,    5.12,     0.0, 0.0},
    {"griewank",   -600.0,   600.0,    0.0, 0.0},
    {"ackley",     -32.768,  32.768,   0.0, 0.0}
};

double pso_bench_eval(pso_fn_id id, const double *x, int dim)
{
    int i;
    double sum = 0.0;

    if (x == NULL || dim <= 0 || id < 0 || id >= PSO_FN_COUNT) {
        return NAN;
    }

    switch (id) {
    case PSO_FN_SPHERE:
        for (i = 0; i < dim; ++i) {
            sum += x[i] * x[i];
        }
        return sum;

    case PSO_FN_ROSENBROCK:
        for (i = 0; i + 1 < dim; ++i) {
            const double t = x[i + 1] - x[i] * x[i];
            sum += 100.0 * t * t + (x[i] - 1.0) * (x[i] - 1.0);
        }
        return sum;

    case PSO_FN_RASTRIGIN:
        for (i = 0; i < dim; ++i) {
            sum += x[i] * x[i] - 10.0 * cos(2.0 * PSO_PI * x[i]) + 10.0;
        }
        return sum;

    case PSO_FN_GRIEWANK: {
        double product = 1.0;
        for (i = 0; i < dim; ++i) {
            /* 仅在平方可能溢出时缩放，保留普通区间的舍入顺序。 */
            sum += fabs(x[i]) > sqrt(DBL_MAX)
                ? (x[i] / 4000.0) * x[i] : x[i] * x[i] / 4000.0;
            product *= cos(x[i] / sqrt((double)(i + 1)));
        }
        return sum - product + 1.0;
    }

    case PSO_FN_ACKLEY: {
        double sum_sq = 0.0;
        double sum_cos = 0.0;
        for (i = 0; i < dim; ++i) {
            sum_sq += x[i] * x[i];
            sum_cos += cos(2.0 * PSO_PI * x[i]);
        }
        return -20.0 * exp(-0.2 * sqrt(sum_sq / (double)dim))
             - exp(sum_cos / (double)dim) + 20.0 + exp(1.0);
    }

    default:
        return NAN;
    }
}

const pso_fn_info *pso_bench_info(pso_fn_id id)
{
    if (id < 0 || id >= PSO_FN_COUNT) {
        return NULL;
    }
    return &PSO_FUNCTIONS[id];
}

pso_fn_id pso_bench_from_name(const char *name)
{
    int i;
    if (name == NULL) {
        return PSO_FN_COUNT;
    }
    for (i = 0; i < PSO_FN_COUNT; ++i) {
        if (strcmp(name, PSO_FUNCTIONS[i].name) == 0) {
            return (pso_fn_id)i;
        }
    }
    return PSO_FN_COUNT;
}
