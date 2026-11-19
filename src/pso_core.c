/* pso_core.c — 粒子状态、初始化、更新、边界、pbest/本岛 best、迁移注入 */
/* -std=c11 的严格 ISO 模式下 glibc 不导出 clock_gettime/CLOCK_MONOTONIC */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include "pso_core.h"

#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <time.h>
#endif

static pso_rng_t *particle_rng(pso_swarm *swarm, int particle)
{
    if (particle < 0 || particle >= swarm->rng_count) {
        /* 流绑定全局粒子索引是不变量 1：索引越界说明调用方状态已损坏，
         * 静默回落到粒子 0 的流会让两个粒子共享同一随机流并产出看似
         * 可信实则错误的结果，因此宁可中止。 */
        fputs("particle_rng: particle index out of range\n", stderr);
        abort();
    }
    return &swarm->rngs[particle];
}

static uint64_t mix_stream_index(uint64_t value)
{
    value ^= value >> 30;
    value *= UINT64_C(0xBF58476D1CE4E5B9);
    value ^= value >> 27;
    value *= UINT64_C(0x94D049BB133111EB);
    return value ^ (value >> 31);
}

static uint64_t derive_particle_stream(uint64_t stream_namespace,
                                       uint64_t global_particle_index)
{
    const uint64_t key = mix_stream_index(
        stream_namespace ^ UINT64_C(0xD1B54A32D192ED03));
    /* 对固定 namespace，加法和 mix 都是 64 位双射，因此不同 index 不碰撞。
     * 跨 namespace 使用确定的高质量 keyed permutation，不依赖固定窗口。 */
    return mix_stream_index(global_particle_index + key);
}

static double positive_mod(double value, double modulus)
{
    double result = fmod(value, modulus);
    if (result < 0.0) result += modulus;
    if (result >= modulus) result = 0.0;
    return result;
}

static void apply_boundary(pso_swarm *swarm, double *x, double *v, pso_rng_t *rng)
{
    const double lo = swarm->lower;
    const double hi = swarm->upper;
    const double width = hi - lo;

    if (!isfinite(*x) || !isfinite(*v)) {
        *x = pso_rng_range(rng, lo, hi);
        *v = 0.0;
        return;
    }

    if (swarm->boundary == PSO_BOUNDARY_CLAMP) {
        if (*x < lo) {
            *x = lo;
            *v = 0.0;
        } else if (*x > hi) {
            *x = hi;
            *v = 0.0;
        }
        return;
    }

    if (swarm->boundary == PSO_BOUNDARY_REINIT) {
        if (*x < lo || *x > hi) {
            *x = pso_rng_range(rng, lo, hi);
            *v = pso_rng_range(rng, -swarm->vmax_ratio * (hi - lo),
                               swarm->vmax_ratio * (hi - lo));
        }
        return;
    }

    /* 把可表示的位移折叠到三角波周期；当 2*width 或 x-lo 会溢出时，
     * 先缩放一半再取模。超过 double 可分辨穿越奇偶时，不猜测物理穿越
     * 次数，而以该可表示周期余数所在区段确定折叠和导数方向。 */
    if (*x < lo || *x > hi) {
        double phase;
        double distance = *x - lo;
        int ascending;
        int at_lo;
        int at_hi;

        if (isfinite(distance) && width <= DBL_MAX * 0.5) {
            phase = positive_mod(distance, 2.0 * width);
            at_lo = phase == 0.0;
            at_hi = phase == width;
            ascending = phase <= width;
            *x = ascending ? lo + phase : hi - (phase - width);
        } else {
            const double half_phase = positive_mod(*x * 0.5 - lo * 0.5,
                                                   width);
            at_lo = half_phase == 0.0;
            at_hi = half_phase == width * 0.5;
            ascending = half_phase <= width * 0.5;
            *x = ascending
                ? lo + 2.0 * half_phase
                : hi - 2.0 * (half_phase - width * 0.5);
        }
        if (*x < lo) *x = lo;
        if (*x > hi) *x = hi;
        if ((at_lo && *v < 0.0) || (at_hi && *v > 0.0) ||
            (!at_lo && !at_hi && !ascending)) {
            *v = -*v;
        }
    }
}

static int initialize_swarm(pso_swarm *swarm, const pso_options *options,
                            int count, uint64_t base_seed,
                            uint64_t stream_namespace,
                            uint64_t global_particle_offset,
                            int legacy_streams)
{
    size_t particle_values;
    double width;
    double vmax;
    int i;
    const pso_fn_info *info;

    if (swarm == NULL) {
        return -1;
    }
    memset(swarm, 0, sizeof(*swarm));
    if (options == NULL || count <= 0 || options->dim <= 0) {
        return -1;
    }
    swarm->count = count;
    swarm->dim = options->dim;
    swarm->function = options->function;
    swarm->lower = options->lower;
    swarm->upper = options->upper;
    swarm->boundary = options->boundary;
    swarm->chi = options->chi;
    swarm->c1 = options->c1;
    swarm->c2 = options->c2;
    swarm->vmax_ratio = options->vmax_ratio;
    swarm->use_linear_inertia = options->use_linear_inertia;
    swarm->inertia_start = options->inertia_start;
    swarm->inertia_end = options->inertia_end;
    info = pso_bench_info(swarm->function);
    width = swarm->upper - swarm->lower;
    vmax = swarm->vmax_ratio * width;
    if (info == NULL || !isfinite(swarm->lower) || !isfinite(swarm->upper) ||
        !isfinite(width) || width <= 0.0 ||
        !isfinite(swarm->chi) || swarm->chi <= 0.0 ||
        !isfinite(swarm->c1) || swarm->c1 < 0.0 ||
        !isfinite(swarm->c2) || swarm->c2 < 0.0 ||
        !isfinite(swarm->vmax_ratio) || swarm->vmax_ratio <= 0.0 ||
        !isfinite(vmax)) {
        return -1;
    }

    if ((size_t)count > SIZE_MAX / sizeof(double) ||
        (size_t)count > SIZE_MAX / sizeof(pso_rng_t) ||
        (size_t)count > SIZE_MAX / (size_t)swarm->dim ||
        (size_t)count * (size_t)swarm->dim > SIZE_MAX / sizeof(double)) {
        return -1;
    }
    particle_values = (size_t)count * (size_t)swarm->dim;
    if ((size_t)swarm->dim > SIZE_MAX / sizeof(double)) {
        return -1;
    }
    swarm->position = (double *)malloc(particle_values * sizeof(double));
    swarm->velocity = (double *)malloc(particle_values * sizeof(double));
    swarm->pbest_position = (double *)malloc(particle_values * sizeof(double));
    swarm->fitness = (double *)malloc((size_t)count * sizeof(double));
    swarm->pbest_fitness = (double *)malloc((size_t)count * sizeof(double));
    swarm->best_position = (double *)malloc((size_t)swarm->dim * sizeof(double));
    swarm->rng_count = count;
    swarm->rngs = (pso_rng_t *)malloc((size_t)swarm->rng_count * sizeof(pso_rng_t));

    if (swarm->position == NULL || swarm->velocity == NULL ||
        swarm->pbest_position == NULL || swarm->fitness == NULL ||
        swarm->pbest_fitness == NULL || swarm->best_position == NULL ||
        swarm->rngs == NULL) {
        pso_swarm_destroy(swarm);
        return -1;
    }

    swarm->nonfinite_count = 0;
    for (i = 0; i < count; ++i) {
        int j;
        pso_rng_t rng;
        /* range API 以 (namespace,index) 派生；legacy 保留原窗口映射和
         * uint64_t 模算术，因而不缩小旧 wrapper 的输入域或改变结果。 */
        {
            const uint64_t index = global_particle_offset + (uint64_t)i;
            const uint64_t stream = legacy_streams
                ? stream_namespace * UINT64_C(1048576) + index
                : derive_particle_stream(stream_namespace, index);
            pso_rng_seed_stream(&rng, base_seed, stream);
        }
        for (j = 0; j < swarm->dim; ++j) {
            const size_t k = (size_t)i * (size_t)swarm->dim + (size_t)j;
            swarm->position[k] = pso_rng_range(&rng, swarm->lower, swarm->upper);
            swarm->velocity[k] = pso_rng_range(&rng, -vmax, vmax);
            swarm->pbest_position[k] = swarm->position[k];
        }
        swarm->fitness[i] = pso_bench_eval(swarm->function,
                                            &swarm->position[(size_t)i * (size_t)swarm->dim],
                                            swarm->dim);
        if (!isfinite(swarm->fitness[i])) {
            swarm->fitness[i] = INFINITY;
            ++swarm->nonfinite_count;
        }
        swarm->pbest_fitness[i] = swarm->fitness[i];
        swarm->rngs[i] = rng;
    }
    pso_swarm_recompute_best(swarm);
    (void)info;
    return 0;
}

int pso_swarm_init_range(pso_swarm *swarm, const pso_options *options,
                         int count, uint64_t base_seed,
                         uint64_t stream_namespace,
                         uint64_t global_particle_offset)
{
    return initialize_swarm(swarm, options, count, base_seed, stream_namespace,
                            global_particle_offset, 0);
}

int pso_swarm_init(pso_swarm *swarm, const pso_options *options,
                   int count, uint64_t base_seed, uint64_t stream_id)
{
    return initialize_swarm(swarm, options, count, base_seed, stream_id, 0U, 1);
}

int pso_partition_count(int total, int rank, int size)
{
    if (total < 0 || size <= 0 || rank < 0 || rank >= size) {
        return -1;
    }
    return total / size + (rank < total % size ? 1 : 0);
}

void pso_swarm_destroy(pso_swarm *swarm)
{
    if (swarm == NULL) {
        return;
    }
    free(swarm->position);
    free(swarm->velocity);
    free(swarm->pbest_position);
    free(swarm->fitness);
    free(swarm->pbest_fitness);
    free(swarm->best_position);
    free(swarm->rngs);
    memset(swarm, 0, sizeof(*swarm));
}

void pso_swarm_recompute_best(pso_swarm *swarm)
{
    int i;
    int best = 0;
    if (swarm == NULL || swarm->count <= 0 || swarm->pbest_fitness == NULL ||
        swarm->pbest_position == NULL || swarm->best_position == NULL) {
        return;
    }
    swarm->best_fitness = INFINITY;
    for (i = 0; i < swarm->count; ++i) {
        if (isfinite(swarm->pbest_fitness[i]) &&
            swarm->pbest_fitness[i] < swarm->best_fitness) {
            best = i;
            swarm->best_fitness = swarm->pbest_fitness[i];
        }
    }
    swarm->best_index = best;
    memcpy(swarm->best_position,
           &swarm->pbest_position[(size_t)best * (size_t)swarm->dim],
           (size_t)swarm->dim * sizeof(double));
}

void pso_swarm_step(pso_swarm *swarm, const double *social_best,
                    int iteration, int total_iterations)
{
    int i;
    const double *social;
    double progress = 0.0;
    double inertia;
    double vmax;

    if (swarm == NULL || swarm->count <= 0 || swarm->dim <= 0 ||
        swarm->position == NULL || swarm->velocity == NULL ||
        swarm->pbest_position == NULL || swarm->fitness == NULL ||
        swarm->pbest_fitness == NULL || swarm->best_position == NULL ||
        swarm->rngs == NULL || swarm->rng_count <= 0) {
        return;
    }
    social = social_best != NULL ? social_best : swarm->best_position;
    if (social == NULL) {
        return;
    }
    if (total_iterations > 1) {
        progress = (double)iteration / (double)(total_iterations - 1);
        if (progress < 0.0) progress = 0.0;
        if (progress > 1.0) progress = 1.0;
    }
    inertia = swarm->use_linear_inertia
        ? swarm->inertia_start + (swarm->inertia_end - swarm->inertia_start) * progress
        : 1.0;
    vmax = swarm->vmax_ratio * (swarm->upper - swarm->lower) * (1.0 - 0.5 * progress);

#ifdef _OPENMP
#pragma omp parallel for schedule(static)
#endif
    for (i = 0; i < swarm->count; ++i) {
        int j;
        pso_rng_t *rng = particle_rng(swarm, i);
        const size_t row = (size_t)i * (size_t)swarm->dim;
        for (j = 0; j < swarm->dim; ++j) {
            const size_t k = row + (size_t)j;
            const double r1 = pso_rng_double(rng);
            const double r2 = pso_rng_double(rng);
            const double personal_delta = isfinite(swarm->pbest_fitness[i])
                ? swarm->pbest_position[k] - swarm->position[k] : 0.0;
            const double social_delta = social_best != NULL || isfinite(swarm->best_fitness)
                ? social[j] - swarm->position[k] : 0.0;
            double v = swarm->use_linear_inertia
                ? inertia * swarm->velocity[k]
                : swarm->chi * swarm->velocity[k];
            v += swarm->c1 * r1 * personal_delta +
                 swarm->c2 * r2 * social_delta;
            if (v > vmax) v = vmax;
            if (v < -vmax) v = -vmax;
            swarm->velocity[k] = v;
            swarm->position[k] += v;
            apply_boundary(swarm, &swarm->position[k], &swarm->velocity[k], rng);
        }
        swarm->fitness[i] = pso_bench_eval(swarm->function,
                                           &swarm->position[row], swarm->dim);
        if (!isfinite(swarm->fitness[i])) swarm->fitness[i] = INFINITY;
        if (swarm->fitness[i] < swarm->pbest_fitness[i]) {
            swarm->pbest_fitness[i] = swarm->fitness[i];
            memcpy(&swarm->pbest_position[row], &swarm->position[row],
                   (size_t)swarm->dim * sizeof(double));
        }
    }
    pso_swarm_recompute_best(swarm);
}

const double *pso_swarm_best_position(const pso_swarm *swarm)
{
    return swarm != NULL ? swarm->best_position : NULL;
}

int pso_swarm_worst_index(const pso_swarm *swarm)
{
    int i;
    int worst = 0;
    if (swarm == NULL || swarm->count <= 0 || swarm->pbest_fitness == NULL) {
        return -1;
    }
    for (i = 1; i < swarm->count; ++i) {
        if (swarm->pbest_fitness[i] > swarm->pbest_fitness[worst]) {
            worst = i;
        }
    }
    return worst;
}

int pso_swarm_inject_migrant(pso_swarm *swarm, const double *migrant_position,
                             double migrant_fit)
{
    int i;
    int worst;
    if (swarm == NULL || migrant_position == NULL || swarm->position == NULL ||
        swarm->velocity == NULL || swarm->pbest_position == NULL ||
        swarm->fitness == NULL || swarm->pbest_fitness == NULL ||
        !isfinite(migrant_fit)) {
        return 0;
    }
    for (i = 0; i < swarm->dim; ++i) {
        if (!isfinite(migrant_position[i]) || migrant_position[i] < swarm->lower ||
            migrant_position[i] > swarm->upper) {
            return 0;
        }
    }
    worst = pso_swarm_worst_index(swarm);
    if (worst < 0 || migrant_fit >= swarm->pbest_fitness[worst]) {
        return 0;
    }
    memcpy(&swarm->position[(size_t)worst * (size_t)swarm->dim], migrant_position,
           (size_t)swarm->dim * sizeof(double));
    memcpy(&swarm->pbest_position[(size_t)worst * (size_t)swarm->dim], migrant_position,
           (size_t)swarm->dim * sizeof(double));
    for (i = 0; i < swarm->dim; ++i) {
        swarm->velocity[(size_t)worst * (size_t)swarm->dim + (size_t)i] = 0.0;
    }
    swarm->fitness[worst] = migrant_fit;
    swarm->pbest_fitness[worst] = migrant_fit;
    pso_swarm_recompute_best(swarm);
    return 1;
}

/* 计时基准（timing_scope=solver 的起表/终表）。时钟调用失败时宁可中止，
 * 也不能返回 0.0 之类哨兵值——那会以退出码 0 发布 elapsed_max_s=0 的伪
 * 测量并污染全部计时/加速比统计（docs/03 §采集条件）。 */
double pso_wall_time(void)
{
#ifdef _WIN32
    static LARGE_INTEGER frequency;
    static int initialized = 0;
    LARGE_INTEGER counter;
    if (!initialized) {
        if (QueryPerformanceFrequency(&frequency) == 0) {
            fputs("QueryPerformanceFrequency failed\n", stderr);
            abort();
        }
        initialized = 1;
    }
    if (QueryPerformanceCounter(&counter) == 0) {
        fputs("QueryPerformanceCounter failed\n", stderr);
        abort();
    }
    return (double)counter.QuadPart / (double)frequency.QuadPart;
#else
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        fputs("clock_gettime(CLOCK_MONOTONIC) failed\n", stderr);
        abort();
    }
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1.0e-9;
#endif
}
