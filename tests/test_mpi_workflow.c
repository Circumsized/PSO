#include "pso_core.h"

#include <math.h>
#include <mpi.h>
#include <stdio.h>
#include <string.h>

#define CHECK(condition, message) do { \
    if (!(condition)) { \
        fprintf(stderr, "rank %d: %s\n", rank, message); \
        local_ok = 0; \
    } \
} while (0)

typedef struct {
    double fitness;
    int rank;
} fit_rank;

static int nearly_equal(double actual, double expected)
{
    return fabs(actual - expected) <= 1.0e-12 * (1.0 + fabs(expected));
}

static int collective_ok(int local_ok)
{
    int all_ok = 0;
    MPI_Allreduce(&local_ok, &all_ok, 1, MPI_INT, MPI_LAND, MPI_COMM_WORLD);
    return all_ok;
}

int main(int argc, char **argv)
{
    pso_options options;
    pso_swarm swarm;
    fit_rank local, global;
    double gbest[2];
    double migrant[2], received[2], migrant_fit, received_fit;
    double previous_global;
    int counts[4] = {0, 0, 0, 0};
    int rank, size, provided, rc, local_ok = 1, all_ok;
    int initialized = 0;

    rc = MPI_Init_thread(&argc, &argv, MPI_THREAD_FUNNELED, &provided);
    if (rc != MPI_SUCCESS) return 1;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);
    CHECK(provided >= MPI_THREAD_FUNNELED, "MPI thread level below FUNNELED");
    CHECK(size == 4, "test_mpi_workflow requires exactly 4 ranks");
    all_ok = collective_ok(local_ok);
    if (!all_ok) {
        MPI_Finalize();
        return 1;
    }

    counts[rank] = pso_partition_count(10, rank, 4);
    MPI_Allgather(MPI_IN_PLACE, 0, MPI_DATATYPE_NULL,
                  counts, 1, MPI_INT, MPI_COMM_WORLD);
    CHECK(counts[0] == 3 && counts[1] == 3 &&
          counts[2] == 2 && counts[3] == 2,
          "partition counts differ from 3,3,2,2");

    pso_options_init(&options);
    options.function = PSO_FN_SPHERE;
    options.dim = 2;
    options.particles = 10;
    options.lower = -100.0;
    options.upper = 100.0;
    options.seed = 7;
    rc = pso_swarm_init_range(&swarm, &options, counts[rank], options.seed,
                              UINT64_C(0),
                              (uint64_t)(rank * (10 / 4) +
                              (rank < 10 % 4 ? rank : 10 % 4)));
    initialized = rc == 0;
    CHECK(initialized, "range swarm initialization failed");
    all_ok = collective_ok(local_ok);
    if (!all_ok) {
        if (initialized) pso_swarm_destroy(&swarm);
        MPI_Finalize();
        return 1;
    }

    local.fitness = swarm.best_fitness;
    local.rank = rank;
    MPI_Allreduce(&local, &global, 1, MPI_DOUBLE_INT, MPI_MINLOC,
                  MPI_COMM_WORLD);
    if (rank == global.rank) memcpy(gbest, swarm.best_position, sizeof(gbest));
    MPI_Bcast(gbest, options.dim, MPI_DOUBLE, global.rank, MPI_COMM_WORLD);
    CHECK(nearly_equal(pso_bench_eval(options.function, gbest, options.dim),
                       global.fitness), "initial global best does not evaluate");
    previous_global = global.fitness;

    for (int iteration = 0; iteration < 3; ++iteration) {
        pso_swarm_step(&swarm, gbest, iteration, 3);
        local.fitness = swarm.best_fitness;
        local.rank = rank;
        MPI_Allreduce(&local, &global, 1, MPI_DOUBLE_INT, MPI_MINLOC,
                      MPI_COMM_WORLD);
        if (rank == global.rank) memcpy(gbest, swarm.best_position, sizeof(gbest));
        MPI_Bcast(gbest, options.dim, MPI_DOUBLE, global.rank, MPI_COMM_WORLD);
        CHECK(global.fitness <= previous_global,
              "global best fitness increased after an iteration");
        CHECK(nearly_equal(pso_bench_eval(options.function, gbest, options.dim),
                           global.fitness), "broadcast global best does not evaluate");
        previous_global = global.fitness;
        if (!collective_ok(local_ok)) {
            pso_swarm_destroy(&swarm);
            MPI_Finalize();
            return 1;
        }
    }

    /* Markers remain in Sphere's [-100,100] domain and are always better than
     * the deliberately degraded destination worst. */
    migrant[0] = 0.5 + (double)rank;
    migrant[1] = -0.25 - (double)rank;
    migrant_fit = pso_bench_eval(PSO_FN_SPHERE, migrant, 2);
    {
        const int worst = pso_swarm_worst_index(&swarm);
        const size_t row = (size_t)worst * 2U;
        swarm.position[row] = 90.0;
        swarm.position[row + 1U] = 90.0;
        swarm.pbest_position[row] = 90.0;
        swarm.pbest_position[row + 1U] = 90.0;
        swarm.fitness[worst] = 16200.0;
        swarm.pbest_fitness[worst] = 16200.0;
        pso_swarm_recompute_best(&swarm);
    }
    MPI_Sendrecv(migrant, 2, MPI_DOUBLE, (rank + 1) % size, 301,
                 received, 2, MPI_DOUBLE, (rank + size - 1) % size, 301,
                 MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    MPI_Sendrecv(&migrant_fit, 1, MPI_DOUBLE, (rank + 1) % size, 302,
                 &received_fit, 1, MPI_DOUBLE, (rank + size - 1) % size, 302,
                 MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    {
        const int source = (rank + size - 1) % size;
        const double expected_position[2] = {
            0.5 + (double)source, -0.25 - (double)source
        };
        const int worst = pso_swarm_worst_index(&swarm);
        const size_t row = (size_t)worst * 2U;
        CHECK(nearly_equal(received[0], expected_position[0]) &&
              nearly_equal(received[1], expected_position[1]),
              "ring migrant did not come from left neighbor");
        CHECK(nearly_equal(received_fit,
              pso_bench_eval(PSO_FN_SPHERE, expected_position, 2)),
              "received migrant fitness/source mismatch");
        CHECK(pso_swarm_inject_migrant(&swarm, received, received_fit) == 1,
              "valid better migrant was not injected");
        CHECK(nearly_equal(swarm.position[row], received[0]) &&
              nearly_equal(swarm.position[row + 1U], received[1]) &&
              nearly_equal(swarm.pbest_position[row], received[0]) &&
              nearly_equal(swarm.pbest_position[row + 1U], received[1]),
              "migrant did not replace worst position and pbest");
        CHECK(nearly_equal(swarm.fitness[worst], received_fit) &&
              nearly_equal(swarm.pbest_fitness[worst], received_fit),
              "migrant fitness fields were not synchronized");
        CHECK(swarm.velocity[row] == 0.0 &&
              swarm.velocity[row + 1U] == 0.0,
              "migrant velocity was not reset");
    }

    all_ok = collective_ok(local_ok);
    pso_swarm_destroy(&swarm);
    if (rank == 0 && all_ok) {
        puts("MPI workflow: PASS (4 ranks)");
    }
    MPI_Finalize();
    return all_ok ? 0 : 1;
}
