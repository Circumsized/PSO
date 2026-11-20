#include "pso_core.h"

#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifdef _OPENMP
#include <omp.h>
#endif

static int within(double value, double lo, double hi)
{
    return isfinite(value) && value >= lo && value <= hi;
}

static int nearly_equal(double actual, double expected)
{
    return fabs(actual - expected) <= 1.0e-12 * (1.0 + fabs(expected));
}

static int test_ck_velocity_formula(void)
{
    pso_options options;
    pso_swarm swarm;
    pso_rng_t expected_rng;
    double social = -0.5;
    double r1;
    double r2;
    double expected;

    pso_options_init(&options);
    if (options.chi != 0.72984 || options.c1 != 1.49618 ||
        options.c2 != 1.49618) {
        fprintf(stderr, "CK defaults changed: chi=%.17g c1=%.17g c2=%.17g\n",
                options.chi, options.c1, options.c2);
        return 1;
    }
    options.function = PSO_FN_SPHERE;
    options.dim = 1;
    options.particles = 1;
    options.lower = -10.0;
    options.upper = 10.0;
    options.vmax_ratio = 1.0;
    options.use_linear_inertia = 0;
    if (pso_swarm_init(&swarm, &options, 1, 17U, 0U) != 0) {
        fputs("CK test initialization failed\n", stderr);
        return 1;
    }
    swarm.position[0] = 0.25;
    swarm.velocity[0] = 0.1;
    swarm.pbest_position[0] = 0.5;
    swarm.pbest_fitness[0] = -1.0;
    pso_rng_seed_stream(&swarm.rngs[0], 777U, 9U);
    expected_rng = swarm.rngs[0];
    r1 = pso_rng_double(&expected_rng);
    r2 = pso_rng_double(&expected_rng);
    expected = options.chi * 0.1 +
               options.c1 * r1 * 0.25 + options.c2 * r2 * -0.75;
    pso_swarm_step(&swarm, &social, 0, 10);
    if (!nearly_equal(swarm.velocity[0], expected) ||
        memcmp(&swarm.rngs[0], &expected_rng, sizeof(expected_rng)) != 0) {
        fprintf(stderr, "CK velocity mismatch: got %.17g expected %.17g\n",
                swarm.velocity[0], expected);
        pso_swarm_destroy(&swarm);
        return 1;
    }
    pso_swarm_destroy(&swarm);
    return 0;
}

static int init_boundary_swarm(pso_swarm *swarm, pso_boundary_mode mode)
{
    pso_options options;

    pso_options_init(&options);
    options.function = PSO_FN_SPHERE;
    options.dim = 1;
    options.particles = 1;
    options.lower = 0.0;
    options.upper = 1.0;
    options.boundary = mode;
    options.chi = 1.0;
    options.c1 = 0.0;
    options.c2 = 0.0;
    options.vmax_ratio = 10.0;
    return pso_swarm_init(swarm, &options, 1, 31U, 0U);
}

static int test_clamp_boundary(void)
{
    pso_swarm swarm;
    double social = 0.75;

    if (init_boundary_swarm(&swarm, PSO_BOUNDARY_CLAMP) != 0) {
        fputs("clamp test initialization failed\n", stderr);
        return 1;
    }
    swarm.position[0] = social;
    swarm.pbest_position[0] = social;
    swarm.velocity[0] = 1.0;
    pso_swarm_step(&swarm, &social, 0, 1);
    if (swarm.position[0] != 1.0 || swarm.velocity[0] != 0.0) {
        fprintf(stderr, "clamp mismatch: x=%.17g v=%.17g\n",
                swarm.position[0], swarm.velocity[0]);
        pso_swarm_destroy(&swarm);
        return 1;
    }
    pso_swarm_destroy(&swarm);
    return 0;
}

static int test_reinit_boundary(void)
{
    pso_swarm swarm;
    pso_rng_t expected_rng;
    double social = 0.75;
    double expected_x;
    double expected_v;

    if (init_boundary_swarm(&swarm, PSO_BOUNDARY_REINIT) != 0) {
        fputs("reinit test initialization failed\n", stderr);
        return 1;
    }
    swarm.position[0] = social;
    swarm.pbest_position[0] = social;
    swarm.velocity[0] = 1.0;
    pso_rng_seed_stream(&swarm.rngs[0], 901U, 2U);
    expected_rng = swarm.rngs[0];
    (void)pso_rng_double(&expected_rng);
    (void)pso_rng_double(&expected_rng);
    expected_x = pso_rng_range(&expected_rng, 0.0, 1.0);
    expected_v = pso_rng_range(&expected_rng, -10.0, 10.0);
    pso_swarm_step(&swarm, &social, 0, 1);
    if (!nearly_equal(swarm.position[0], expected_x) ||
        !nearly_equal(swarm.velocity[0], expected_v)) {
        fprintf(stderr, "reinit mismatch: x=%.17g/%.17g v=%.17g/%.17g\n",
                swarm.position[0], expected_x, swarm.velocity[0], expected_v);
        pso_swarm_destroy(&swarm);
        return 1;
    }
    pso_swarm_destroy(&swarm);
    return 0;
}

static int test_reflect_crossing_parity(void)
{
    static const double starts[] = {0.25, 0.25, 0.75, 0.75};
    static const double velocities[] = {1.0, 2.0, -1.0, -2.0};
    static const double expected_x[] = {0.75, 0.25, 0.25, 0.75};
    static const double expected_v[] = {-1.0, 2.0, 1.0, -2.0};
    size_t i;

    for (i = 0; i < sizeof(starts) / sizeof(starts[0]); ++i) {
        pso_swarm swarm;
        double social = starts[i];
        if (init_boundary_swarm(&swarm, PSO_BOUNDARY_REFLECT) != 0) {
            fputs("reflect test initialization failed\n", stderr);
            return 1;
        }
        swarm.position[0] = starts[i];
        swarm.pbest_position[0] = starts[i];
        swarm.velocity[0] = velocities[i];
        pso_swarm_step(&swarm, &social, 0, 1);
        if (!nearly_equal(swarm.position[0], expected_x[i]) ||
            !nearly_equal(swarm.velocity[0], expected_v[i])) {
            fprintf(stderr,
                    "reflect parity mismatch[%llu]: x=%.17g/%.17g v=%.17g/%.17g\n",
                    (unsigned long long)i, swarm.position[0], expected_x[i],
                    swarm.velocity[0], expected_v[i]);
            pso_swarm_destroy(&swarm);
            return 1;
        }
        pso_swarm_destroy(&swarm);
    }
    return 0;
}

static int test_reflect_turning_point_direction(void)
{
    static const double velocities[] = {-2.25, 1.75, 2.75, -1.25};
    static const double expected_x[] = {0.0, 0.0, 1.0, 1.0};
    static const double expected_v[] = {2.25, 1.75, -2.75, -1.25};
    size_t i;

    for (i = 0; i < sizeof(velocities) / sizeof(velocities[0]); ++i) {
        pso_swarm swarm;
        double social = 0.25;
        if (init_boundary_swarm(&swarm, PSO_BOUNDARY_REFLECT) != 0) {
            fputs("turning-point reflect test initialization failed\n", stderr);
            return 1;
        }
        swarm.position[0] = social;
        swarm.pbest_position[0] = social;
        swarm.velocity[0] = velocities[i];
        pso_swarm_step(&swarm, &social, 0, 1);
        /* These inputs make fmod return exactly 0 or width. */
        if (swarm.position[0] != expected_x[i] ||
            swarm.velocity[0] != expected_v[i]) {
            fprintf(stderr,
                    "reflect turning point mismatch[%llu]: "
                    "x=%.17g/%.17g v=%.17g/%.17g\n",
                    (unsigned long long)i, swarm.position[0], expected_x[i],
                    swarm.velocity[0], expected_v[i]);
            pso_swarm_destroy(&swarm);
            return 1;
        }
        pso_swarm_destroy(&swarm);
    }
    return 0;
}

static int test_reflect_large_finite_values(void)
{
    static const double starts[] = {3.0, -3.0, 1.0e12, -1.0e12};
    static const double velocities[] = {1.0, -1.0, 0.5, -0.5};
    static const double expected_x[] = {0.0, 0.0, 0.5, 0.5};
    static const double expected_v[] = {1.0, 1.0, 0.5, 0.5};
    size_t i;

    for (i = 0; i < sizeof(starts) / sizeof(starts[0]); ++i) {
        pso_swarm swarm;
        double social = starts[i];
        if (init_boundary_swarm(&swarm, PSO_BOUNDARY_REFLECT) != 0) {
            fputs("large reflect test initialization failed\n", stderr);
            return 1;
        }
        swarm.position[0] = starts[i];
        swarm.pbest_position[0] = starts[i];
        swarm.velocity[0] = velocities[i];
        pso_swarm_step(&swarm, &social, 0, 1);
        if (!nearly_equal(swarm.position[0], expected_x[i]) ||
            !nearly_equal(swarm.velocity[0], expected_v[i])) {
            fprintf(stderr,
                    "large reflect mismatch[%llu]: x=%.17g/%.17g v=%.17g/%.17g\n",
                    (unsigned long long)i, swarm.position[0], expected_x[i],
                    swarm.velocity[0], expected_v[i]);
            pso_swarm_destroy(&swarm);
            return 1;
        }
        pso_swarm_destroy(&swarm);
    }

    {
        pso_options options;
        pso_swarm swarm;
        double social = DBL_MAX;
        const double half_max = DBL_MAX * 0.5;

        pso_options_init(&options);
        options.function = PSO_FN_SPHERE;
        options.dim = 1;
        options.lower = -half_max;
        options.upper = half_max;
        options.boundary = PSO_BOUNDARY_REFLECT;
        options.chi = 1.0;
        options.c1 = 0.0;
        options.c2 = 0.0;
        options.vmax_ratio = 1.0;
        if (pso_swarm_init(&swarm, &options, 1, 5U, 0U) != 0) {
            fputs("DBL_MAX reflect test initialization failed\n", stderr);
            return 1;
        }
        swarm.position[0] = DBL_MAX;
        swarm.pbest_position[0] = DBL_MAX;
        swarm.velocity[0] = 1.0;
        pso_swarm_step(&swarm, &social, 0, 1);
        if (!within(swarm.position[0], options.lower, options.upper) ||
            !isfinite(swarm.velocity[0]) || swarm.velocity[0] >= 0.0) {
            fprintf(stderr, "DBL_MAX reflection was unsafe: x=%.17g v=%.17g\n",
                    swarm.position[0], swarm.velocity[0]);
            pso_swarm_destroy(&swarm);
            return 1;
        }
        pso_swarm_destroy(&swarm);
    }

    {
        pso_options options;
        pso_swarm swarm;
        const double half_max = DBL_MAX * 0.5;
        double social = -half_max;
        const double negative_large = -half_max;

        pso_options_init(&options);
        options.function = PSO_FN_SPHERE;
        options.dim = 1;
        options.lower = -half_max;
        options.upper = half_max;
        options.boundary = PSO_BOUNDARY_REFLECT;
        options.chi = 1.0;
        options.c1 = 0.0;
        options.c2 = 0.0;
        options.vmax_ratio = 0.5;
        if (pso_swarm_init(&swarm, &options, 1, 5U, 0U) != 0) {
            fputs("negative large reflect test initialization failed\n", stderr);
            return 1;
        }
        swarm.position[0] = social;
        swarm.pbest_position[0] = social;
        swarm.velocity[0] = negative_large;
        pso_swarm_step(&swarm, &social, 0, 1);
        if (!within(swarm.position[0], options.lower, options.upper) ||
            !isfinite(swarm.velocity[0]) || swarm.velocity[0] <= 0.0) {
            fprintf(stderr,
                    "negative large reflection was unsafe or misdirected: "
                    "x=%.17g v=%.17g\n",
                    swarm.position[0], swarm.velocity[0]);
            pso_swarm_destroy(&swarm);
            return 1;
        }
        pso_swarm_destroy(&swarm);
    }
    return 0;
}

static int swarms_equal(const pso_swarm *a, const pso_swarm *b)
{
    const size_t values = (size_t)a->count * (size_t)a->dim;
    return a->count == b->count && a->dim == b->dim &&
           memcmp(a->position, b->position, values * sizeof(double)) == 0 &&
           memcmp(a->velocity, b->velocity, values * sizeof(double)) == 0 &&
           memcmp(a->pbest_position, b->pbest_position,
                  values * sizeof(double)) == 0 &&
           memcmp(a->fitness, b->fitness,
                  (size_t)a->count * sizeof(double)) == 0 &&
           memcmp(a->pbest_fitness, b->pbest_fitness,
                  (size_t)a->count * sizeof(double)) == 0 &&
           memcmp(a->rngs, b->rngs,
                  (size_t)a->rng_count * sizeof(pso_rng_t)) == 0;
}

static int test_global_particle_streams(void)
{
    static const int counts[] = {2, 2, 2, 1};
    pso_options options;
    pso_swarm whole;
    pso_swarm chunks[4];
    pso_swarm other;
    uint64_t offset = 0U;
    int c;
    int global = 0;

    memset(&whole, 0, sizeof(whole));
    memset(chunks, 0, sizeof(chunks));
    memset(&other, 0, sizeof(other));
    pso_options_init(&options);
    options.function = PSO_FN_SPHERE;
    options.dim = 3;
    options.particles = 7;
    if (pso_swarm_init_range(&whole, &options, 7, 404U, 23U, 0U) != 0) {
        fputs("whole range initialization failed\n", stderr);
        return 1;
    }
    for (c = 0; c < 4; ++c) {
        int i;
        if (pso_swarm_init_range(&chunks[c], &options, counts[c], 404U,
                                 23U, offset) != 0) {
            fputs("chunk range initialization failed\n", stderr);
            goto fail;
        }
        for (i = 0; i < counts[c]; ++i, ++global) {
            pso_swarm one = chunks[c];
            one.count = 1;
            one.rng_count = 1;
            one.position += (size_t)i * (size_t)options.dim;
            one.velocity += (size_t)i * (size_t)options.dim;
            one.pbest_position += (size_t)i * (size_t)options.dim;
            one.fitness += i;
            one.pbest_fitness += i;
            one.rngs += i;
            {
                pso_swarm expected = whole;
                expected.count = 1;
                expected.rng_count = 1;
                expected.position += (size_t)global * (size_t)options.dim;
                expected.velocity += (size_t)global * (size_t)options.dim;
                expected.pbest_position += (size_t)global * (size_t)options.dim;
                expected.fitness += global;
                expected.pbest_fitness += global;
                expected.rngs += global;
                if (!swarms_equal(&one, &expected)) {
                    fprintf(stderr, "chunk stream mismatch at particle %d\n", global);
                    goto fail;
                }
            }
        }
        offset += (uint64_t)counts[c];
    }
    if (pso_swarm_init_range(&other, &options, 7, 404U, 24U, 0U) != 0) {
        fputs("namespace comparison initialization failed\n", stderr);
        goto fail;
    }
    if (swarms_equal(&whole, &other)) {
        fputs("different namespaces produced identical particle streams\n", stderr);
        goto fail;
    }
    for (c = 0; c < 4; ++c) pso_swarm_destroy(&chunks[c]);
    pso_swarm_destroy(&other);
    pso_swarm_destroy(&whole);
    return 0;

fail:
    for (c = 0; c < 4; ++c) pso_swarm_destroy(&chunks[c]);
    pso_swarm_destroy(&other);
    pso_swarm_destroy(&whole);
    return 1;
}

static int test_legacy_wrapper_stream_domain(void)
{
    pso_options options;
    pso_swarm swarm;
    pso_rng_t expected_rng;
    const uint64_t stream_id = UINT64_MAX;
    const uint64_t legacy_stream = stream_id * UINT64_C(1048576);
    double expected_x;
    double expected_v;

    memset(&swarm, 0, sizeof(swarm));
    pso_options_init(&options);
    options.function = PSO_FN_SPHERE;
    options.dim = 1;
    options.lower = -2.0;
    options.upper = 3.0;
    if (pso_swarm_init(&swarm, &options, 1, 123U, stream_id) != 0) {
        fputs("legacy wrapper rejected a high stream_id\n", stderr);
        return 1;
    }
    pso_rng_seed_stream(&expected_rng, 123U, legacy_stream);
    expected_x = pso_rng_range(&expected_rng, options.lower, options.upper);
    expected_v = pso_rng_range(&expected_rng,
                               -options.vmax_ratio * (options.upper - options.lower),
                                options.vmax_ratio * (options.upper - options.lower));
    if (swarm.position[0] != expected_x || swarm.velocity[0] != expected_v ||
        memcmp(&swarm.rngs[0], &expected_rng, sizeof(expected_rng)) != 0) {
        fputs("legacy wrapper stream mapping changed\n", stderr);
        pso_swarm_destroy(&swarm);
        return 1;
    }
    pso_swarm_destroy(&swarm);
    return 0;
}

static int test_range_stream_domain_edges(void)
{
    pso_options options;
    pso_swarm a;
    pso_swarm b;

    memset(&a, 0, sizeof(a));
    memset(&b, 0, sizeof(b));
    pso_options_init(&options);
    options.function = PSO_FN_SPHERE;
    options.dim = 1;
    if (pso_swarm_init_range(&a, &options, 1, 88U, 7U,
                             UINT64_C(1048576)) != 0 ||
        pso_swarm_init_range(&b, &options, 1, 88U, UINT64_MAX,
                             UINT64_MAX) != 0) {
        fputs("range API rejected a defined namespace/index edge\n", stderr);
        pso_swarm_destroy(&a);
        pso_swarm_destroy(&b);
        return 1;
    }
    if (swarms_equal(&a, &b)) {
        fputs("distinct range namespace/index pairs collided in test sample\n", stderr);
        pso_swarm_destroy(&a);
        pso_swarm_destroy(&b);
        return 1;
    }
    pso_swarm_destroy(&a);
    pso_swarm_destroy(&b);
    return 0;
}

static int test_init_rejects_nonfinite_derived_ranges(void)
{
    pso_options options;
    pso_swarm swarm;

    memset(&swarm, 0xA5, sizeof(swarm));
    pso_options_init(&options);
    options.function = PSO_FN_SPHERE;
    options.dim = 1;
    options.lower = -DBL_MAX;
    options.upper = DBL_MAX;
    if (pso_swarm_init(&swarm, &options, 1, 1U, 0U) != -1) {
        fputs("initialization accepted an infinite derived width\n", stderr);
        pso_swarm_destroy(&swarm);
        return 1;
    }
    pso_swarm_destroy(&swarm);

    memset(&swarm, 0xA5, sizeof(swarm));
    options.lower = 0.0;
    options.upper = DBL_MAX;
    options.vmax_ratio = 2.0;
    if (pso_swarm_init(&swarm, &options, 1, 1U, 0U) != -1) {
        fputs("initialization accepted an infinite velocity limit\n", stderr);
        pso_swarm_destroy(&swarm);
        return 1;
    }
    pso_swarm_destroy(&swarm);
    return 0;
}

static int test_nonfinite_fitness_policy(void)
{
    pso_options options;
    pso_swarm swarm;

    pso_options_init(&options);
    options.function = PSO_FN_ACKLEY;
    options.dim = 1;
    options.particles = 2;
    options.lower = 0.0;
    options.upper = 1.0e308;
    if (pso_swarm_init_range(&swarm, &options, 2, 0U, 0U, 0U) != 0) {
        fputs("finite candidate was rejected with a nonfinite candidate\n", stderr);
        return 1;
    }
    if (!isfinite(swarm.fitness[1]) || !isfinite(swarm.best_fitness) ||
        swarm.best_index != 1) {
        fputs("finite fitness was not preferred over nonfinite fitness\n", stderr);
        pso_swarm_destroy(&swarm);
        return 1;
    }
    pso_swarm_destroy(&swarm);

    pso_options_init(&options);
    options.function = PSO_FN_ACKLEY;
    options.dim = 1;
    options.particles = 1;
    options.lower = 3.0e307;
    options.upper = 4.0e307;
    if (pso_swarm_init_range(&swarm, &options, 1, 3U, 0U, 0U) != 0) {
        fputs("local all-nonfinite swarm could not be initialized\n", stderr);
        return 1;
    }
    if (swarm.best_fitness != INFINITY || swarm.pbest_fitness[0] != INFINITY) {
        fputs("invalid local fitness was not normalized for reduction\n", stderr);
        pso_swarm_destroy(&swarm);
        return 1;
    }
    pso_swarm_destroy(&swarm);

    pso_options_init(&options);
    options.function = PSO_FN_ACKLEY;
    options.dim = 1;
    options.particles = 1;
    options.lower = 0.0;
    options.upper = 1.0e308;
    options.chi = 1.0;
    if (pso_swarm_init_range(&swarm, &options, 1, 0U, 0U, 0U) != 0) return 1;
    swarm.position[0] = 1.0;
    swarm.velocity[0] = 0.0;
    pso_swarm_step(&swarm, NULL, 0, 2);
    if (swarm.position[0] != 1.0 ||
        !nearly_equal(swarm.pbest_fitness[0], 3.6253849384403627)) {
        fputs("invalid attractors prevented finite pbest recovery\n", stderr);
        pso_swarm_destroy(&swarm);
        return 1;
    }
    swarm.position[0] = 3.0e307;
    swarm.velocity[0] = 0.0;
    swarm.c1 = 0.0;
    swarm.c2 = 0.0;
    pso_swarm_step(&swarm, NULL, 1, 2);
    if (swarm.fitness[0] != INFINITY ||
        !nearly_equal(swarm.best_fitness, 3.6253849384403627)) {
        fputs("invalid evaluation replaced a finite pbest\n", stderr);
        pso_swarm_destroy(&swarm);
        return 1;
    }
    pso_swarm_destroy(&swarm);
    return 0;
}

static int test_partition_count(void)
{
    static const int expected[] = {3, 3, 2, 2};
    int rank;
    for (rank = 0; rank < 4; ++rank) {
        if (pso_partition_count(10, rank, 4) != expected[rank]) {
            fprintf(stderr, "partition mismatch at rank %d\n", rank);
            return 1;
        }
    }
    if (pso_partition_count(-1, 0, 4) != -1 ||
        pso_partition_count(10, -1, 4) != -1 ||
        pso_partition_count(10, 4, 4) != -1 ||
        pso_partition_count(10, 0, 0) != -1) {
        fputs("invalid partition input was accepted\n", stderr);
        return 1;
    }
    return 0;
}

int main(void)
{
    pso_options options;
    pso_swarm swarm;
    pso_swarm serial;
    pso_swarm parallel;
    double migrant[4] = {0.0, 0.0, 0.0, 0.0};
    double bad_migrant[4] = {NAN, 0.0, 0.0, 0.0};
    int i;

    if (test_ck_velocity_formula() != 0 || test_clamp_boundary() != 0 ||
        test_reinit_boundary() != 0 || test_reflect_crossing_parity() != 0 ||
        test_reflect_turning_point_direction() != 0 ||
        test_reflect_large_finite_values() != 0 ||
        test_global_particle_streams() != 0 ||
        test_legacy_wrapper_stream_domain() != 0 ||
        test_range_stream_domain_edges() != 0 ||
        test_init_rejects_nonfinite_derived_ranges() != 0 ||
        test_nonfinite_fitness_policy() != 0 ||
        test_partition_count() != 0) {
        return 1;
    }

    pso_options_init(&options);
    options.function = PSO_FN_SPHERE;
    options.dim = 4;
    options.particles = 16;
    options.lower = -1.0;
    options.upper = 1.0;
    options.boundary = PSO_BOUNDARY_REFLECT;
    if (pso_swarm_init(&swarm, &options, options.particles, 7, 0) != 0) {
        fputs("swarm initialization failed\n", stderr);
        return 1;
    }

    /* 强制大幅越界和非有限状态，验证反射/恢复路径不会泄漏 NaN/Inf。 */
    swarm.position[0] = 1.0e12;
    swarm.velocity[0] = 1.0e12;
    swarm.position[1] = NAN;
    swarm.velocity[1] = INFINITY;
    pso_swarm_step(&swarm, NULL, 0, 10);
    for (i = 0; i < swarm.count * swarm.dim; ++i) {
        if (!within(swarm.position[i], options.lower, options.upper) ||
            !isfinite(swarm.velocity[i])) {
            fputs("boundary handling produced invalid state\n", stderr);
            pso_swarm_destroy(&swarm);
            return 1;
        }
    }

    if (pso_swarm_inject_migrant(&swarm, bad_migrant, 0.0) != 0) {
        fputs("invalid migrant was accepted\n", stderr);
        pso_swarm_destroy(&swarm);
        return 1;
    }
    if (pso_swarm_inject_migrant(&swarm, migrant, 0.0) != 1 ||
        fabs(swarm.best_fitness) > 1.0e-15) {
        fputs("valid migrant was not installed consistently\n", stderr);
        pso_swarm_destroy(&swarm);
        return 1;
    }

    /* 更差/相等移民必须被拒，且最差槽位的粒子与适应度保持原样：
     * 迁移只替换严格更差的槽位（src/pso_core.c 的 migrant_fit >= 最差
     * pbest_fitness 即 return 0），否则环模型会把坏解写回岛屿。 */
    {
        const int worst = pso_swarm_worst_index(&swarm);
        double keep_position[4];
        double keep_fitness;
        if (worst < 0) {
            fputs("worst index unavailable after valid migrant\n", stderr);
            pso_swarm_destroy(&swarm);
            return 1;
        }
        memcpy(keep_position, &swarm.position[(size_t)worst * 4], sizeof(keep_position));
        keep_fitness = swarm.pbest_fitness[worst];
        if (pso_swarm_inject_migrant(&swarm, migrant, keep_fitness + 1.0) != 0 ||
            pso_swarm_inject_migrant(&swarm, migrant, keep_fitness) != 0) {
            fputs("worse or equal migrant was accepted\n", stderr);
            pso_swarm_destroy(&swarm);
            return 1;
        }
        if (swarm.pbest_fitness[worst] != keep_fitness ||
            memcmp(&swarm.position[(size_t)worst * 4], keep_position,
                   sizeof(keep_position)) != 0) {
            fputs("rejected migrant mutated the worst slot\n", stderr);
            pso_swarm_destroy(&swarm);
            return 1;
        }
    }
    pso_swarm_destroy(&swarm);

    /* 同 seed 的结果必须与 OpenMP 线程数无关。 */
    if (pso_swarm_init(&serial, &options, options.particles, 99, 0) != 0 ||
        pso_swarm_init(&parallel, &options, options.particles, 99, 0) != 0) {
        fputs("reproducibility swarm initialization failed\n", stderr);
        return 1;
    }
#ifdef _OPENMP
    omp_set_num_threads(1);
#endif
    pso_swarm_step(&serial, NULL, 0, 2);
#ifdef _OPENMP
    omp_set_num_threads(4);
#endif
    pso_swarm_step(&parallel, NULL, 0, 2);
    if (!swarms_equal(&serial, &parallel) ||
        serial.best_index != parallel.best_index ||
        serial.best_fitness != parallel.best_fitness ||
        memcmp(serial.best_position, parallel.best_position,
               (size_t)options.dim * sizeof(double)) != 0) {
        fputs("OpenMP thread count changed deterministic result\n", stderr);
        pso_swarm_destroy(&serial);
        pso_swarm_destroy(&parallel);
        return 1;
    }
    pso_swarm_destroy(&serial);
    pso_swarm_destroy(&parallel);
    puts("core invariants: PASS");
    return 0;
}
