#include "pso_cli.h"
#include "pso_core.h"
#include "pso_pin.h"
#include "pso_report.h"

#include <inttypes.h>
#include <math.h>
#include <mpi.h>
#include <stdio.h>
#include <stdlib.h>

#ifdef _OPENMP
#include <omp.h>
#endif

static int max_threads(void)
{
#ifdef _OPENMP
    return omp_get_max_threads();
#else
    return 1;
#endif
}

/* 失败退出前仅关闭本进程打开的 history 文件，绝不删除用户提供的路径：
 * 该路径可能是设备节点、FIFO、符号链接或他人文件，按名删除是进程外损害
 * （root/Linux 下 `--history /dev/full` 曾会 unlink 掉字符设备本身）。
 * 空/残缺 sidecar 由下游消费者硬失败拒绝；history 仅 rank 0 持有。 */
static void abandon_history(FILE *history)
{
    if (history != NULL) {
        fclose(history);
    }
}

typedef struct { double fitness; int rank; } pso_fit_rank;

static int mpi_check(int rc, const char *operation, int rank)
{
    char message[MPI_MAX_ERROR_STRING];
    int length = 0;
    if (rc == MPI_SUCCESS) return 0;
    if (MPI_Error_string(rc, message, &length) != MPI_SUCCESS) {
        length = snprintf(message, sizeof(message), "error code %d", rc);
    }
    fprintf(stderr, "rank %d: %s failed: %.*s\n", rank, operation, length, message);
    return -1;
}

static uint64_t global_particle_offset(int total, int rank, int size)
{
    const int base = total / size;
    const int remainder = total % size;
    return (uint64_t)(rank * base + (rank < remainder ? rank : remainder));
}

int main(int argc, char **argv)
{
    pso_options options;
    pso_swarm swarm;
    pso_fit_rank local, global;
    pso_result_line result;
    FILE *history = NULL;
    double *gbest = NULL;
    int rank = -1;
    int size, rc, provided, thread_ok, steps = 0, limit;
    int local_ok, all_ok, reached;
    int count;
    uint64_t offset;
    double start, local_elapsed, elapsed_max;
    uint64_t evals;
    uint64_t fes_to_target;

    rc = MPI_Init_thread(&argc, &argv, MPI_THREAD_FUNNELED, &provided);
    if (rc != MPI_SUCCESS) {
        fputs("MPI_Init_thread failed\n", stderr);
        return EXIT_FAILURE;
    }
    /* rank 先取回：后续 mpi_check 的诊断才能带上真实 rank，而不是 -1。
     * 两调用之间无依赖（rank 仅用于诊断输出）。 */
    rc = MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    if (mpi_check(rc, "MPI_Comm_rank", rank) != 0) {
        MPI_Abort(MPI_COMM_WORLD, rc);
        return EXIT_FAILURE;
    }
    rc = MPI_Comm_set_errhandler(MPI_COMM_WORLD, MPI_ERRORS_RETURN);
    if (mpi_check(rc, "MPI_Comm_set_errhandler", rank) != 0) {
        MPI_Abort(MPI_COMM_WORLD, rc);
        return EXIT_FAILURE;
    }
    rc = MPI_Comm_size(MPI_COMM_WORLD, &size);
    if (mpi_check(rc, "MPI_Comm_size", rank) != 0) {
        MPI_Abort(MPI_COMM_WORLD, rc);
        return EXIT_FAILURE;
    }
    thread_ok = provided >= MPI_THREAD_FUNNELED;
    rc = MPI_Allreduce(&thread_ok, &all_ok, 1, MPI_INT, MPI_LAND, MPI_COMM_WORLD);
    if (mpi_check(rc, "MPI_Allreduce(thread level)", rank) != 0) {
        MPI_Abort(MPI_COMM_WORLD, rc);
        return EXIT_FAILURE;
    }
    if (!all_ok) {
        if (rank == 0) {
            fputs("MPI implementation does not provide MPI_THREAD_FUNNELED\n", stderr);
        }
        MPI_Finalize();
        return EXIT_FAILURE;
    }
    pso_options_init(&options);
    rc = pso_parse_args_for(argc, argv, &options, 1, 0);
    if (rc == 1) { MPI_Finalize(); return 0; }
    if (rc != 0 || options.particles < size) {
        if (rank == 0) fputs("invalid options or too many MPI ranks\n", stderr);
        MPI_Finalize();
        return EXIT_FAILURE;
    }
    limit = pso_options_iteration_limit(&options);
    {
        /* 绑定失败必须集体一致退出，否则单 rank 提前返回会让其余
         * rank 卡死在后续集合通信上。 */
        int pin_ok = 1;
        if (options.pin_cpus) {
            char pin_error[128];
            int pin_rc = pso_pin_current_process(rank, max_threads(), pin_error,
                                                 sizeof(pin_error));
            pin_ok = pin_rc == PSO_PIN_OK;
            if (!pin_ok) {
                fprintf(stderr, "rank %d: pin-cpus %s: %s\n", rank,
                        pin_rc == PSO_PIN_UNSUPPORTED ? "unsupported" : "failed",
                        pin_error);
            }
        }
        rc = MPI_Allreduce(&pin_ok, &all_ok, 1, MPI_INT, MPI_LAND,
                           MPI_COMM_WORLD);
        if (mpi_check(rc, "MPI_Allreduce(pin)", rank) != 0) {
            MPI_Abort(MPI_COMM_WORLD, rc);
            return EXIT_FAILURE;
        }
        if (!all_ok) {
            MPI_Finalize();
            return EXIT_FAILURE;
        }
    }
    if ((size_t)options.dim > SIZE_MAX / sizeof(*gbest)) {
        if (rank == 0) fputs("dimension is too large for position buffer\n", stderr);
        MPI_Finalize();
        return EXIT_FAILURE;
    }
    gbest = (double *)malloc((size_t)options.dim * sizeof(*gbest));
    count = pso_partition_count(options.particles, rank, size);
    offset = global_particle_offset(options.particles, rank, size);
    local_ok = gbest != NULL && count > 0;
    if (local_ok) {
        local_ok = pso_swarm_init_range(&swarm, &options, count, options.seed,
                                        0U, offset) == 0;
    }
    all_ok = 0;
    rc = MPI_Allreduce(&local_ok, &all_ok, 1, MPI_INT, MPI_LAND, MPI_COMM_WORLD);
    if (mpi_check(rc, "MPI_Allreduce(allocation)", rank) != 0) {
        if (local_ok) pso_swarm_destroy(&swarm);
        free(gbest);
        MPI_Abort(MPI_COMM_WORLD, rc);
        return EXIT_FAILURE;
    }
    if (!all_ok) {
        if (local_ok) pso_swarm_destroy(&swarm);
        free(gbest);
        if (rank == 0) fputs("one or more ranks failed to allocate swarm state\n", stderr);
        MPI_Finalize();
        return EXIT_FAILURE;
    }

    if (rank == 0 && options.history_path != NULL) {
        history = fopen(options.history_path, "w");
    }
    {
        int history_ok = (rank != 0 || options.history_path == NULL || history != NULL)
                             ? 1 : 0;
        rc = MPI_Allreduce(&history_ok, &all_ok, 1, MPI_INT, MPI_LAND,
                           MPI_COMM_WORLD);
        if (mpi_check(rc, "MPI_Allreduce(history)", rank) != 0) {
            abandon_history(history);
            free(gbest);
            pso_swarm_destroy(&swarm);
            MPI_Abort(MPI_COMM_WORLD, rc);
            return EXIT_FAILURE;
        }
    }
    if (!all_ok) {
        abandon_history(history);
        free(gbest);
        pso_swarm_destroy(&swarm);
        if (rank == 0) fputs("rank 0 failed to open history file\n", stderr);
        MPI_Finalize();
        return EXIT_FAILURE;
    }

    rc = MPI_Barrier(MPI_COMM_WORLD);
    if (mpi_check(rc, "MPI_Barrier", rank) != 0) {
        free(gbest);
        pso_swarm_destroy(&swarm);
        MPI_Abort(MPI_COMM_WORLD, rc);
        return EXIT_FAILURE;
    }
    start = MPI_Wtime();
    evals = (uint64_t)options.particles;
    local.rank = rank;
    local.fitness = swarm.best_fitness;
    rc = MPI_Allreduce(&local, &global, 1, MPI_DOUBLE_INT, MPI_MINLOC, MPI_COMM_WORLD);
    if (mpi_check(rc, "MPI_Allreduce(initial best)", rank) != 0) {
        free(gbest);
        pso_swarm_destroy(&swarm);
        MPI_Abort(MPI_COMM_WORLD, rc);
        return EXIT_FAILURE;
    }
    if (rank == global.rank) {
        for (int j = 0; j < options.dim; ++j) gbest[j] = swarm.best_position[j];
    }
    rc = MPI_Bcast(gbest, options.dim, MPI_DOUBLE, global.rank, MPI_COMM_WORLD);
    if (mpi_check(rc, "MPI_Bcast(initial best)", rank) != 0) {
        free(gbest);
        pso_swarm_destroy(&swarm);
        MPI_Abort(MPI_COMM_WORLD, rc);
        return EXIT_FAILURE;
    }
    if (!isfinite(global.fitness)) {
        if (rank == 0) fputs("initial swarm has no finite fitness\n", stderr);
        abandon_history(history);
        free(gbest);
        pso_swarm_destroy(&swarm);
        MPI_Finalize();
        return EXIT_FAILURE;
    }
    if (swarm.nonfinite_count > 0) {
        fprintf(stderr,
                "rank %d warning: %d of %d local particles have non-finite "
                "fitness (normalized to +inf)\n",
                rank, swarm.nonfinite_count, count);
    }
    reached = global.fitness <= options.target;
    fes_to_target = reached ? evals : UINT64_C(0);
    if (history != NULL) {
        if (pso_history_append(history, 0, evals, global.fitness) != 0) {
            /* history 只由 rank 0 写；此处单独退出会挂死其余 rank，
             * 必须用 MPI_Abort 集体终止。 */
            fputs("cannot write history file\n", stderr);
            abandon_history(history);
            free(gbest);
            pso_swarm_destroy(&swarm);
            MPI_Abort(MPI_COMM_WORLD, EXIT_FAILURE);
            return EXIT_FAILURE;
        }
    }

    while (steps < limit && !(options.stop_on_target && reached)) {
        pso_swarm_step(&swarm, gbest, steps, limit);
        ++steps;
        evals += (uint64_t)options.particles;
        local.fitness = swarm.best_fitness;
        rc = MPI_Allreduce(&local, &global, 1, MPI_DOUBLE_INT, MPI_MINLOC, MPI_COMM_WORLD);
        if (mpi_check(rc, "MPI_Allreduce(iteration best)", rank) != 0) {
            free(gbest);
            pso_swarm_destroy(&swarm);
            MPI_Abort(MPI_COMM_WORLD, rc);
            return EXIT_FAILURE;
        }
        if (rank == global.rank) {
            for (int j = 0; j < options.dim; ++j) gbest[j] = swarm.best_position[j];
        }
        rc = MPI_Bcast(gbest, options.dim, MPI_DOUBLE, global.rank, MPI_COMM_WORLD);
        if (mpi_check(rc, "MPI_Bcast(iteration best)", rank) != 0) {
            free(gbest);
            pso_swarm_destroy(&swarm);
            MPI_Abort(MPI_COMM_WORLD, rc);
            return EXIT_FAILURE;
        }
        reached = global.fitness <= options.target;
        if (reached && fes_to_target == UINT64_C(0)) fes_to_target = evals;
        if (history != NULL) {
            if (pso_history_append(history, steps, evals, global.fitness) != 0) {
                fputs("cannot write history file\n", stderr);
                abandon_history(history);
                free(gbest);
                pso_swarm_destroy(&swarm);
                MPI_Abort(MPI_COMM_WORLD, EXIT_FAILURE);
                return EXIT_FAILURE;
            }
        }
        if (options.verbose && rank == 0 && steps % options.report_interval == 0) {
            fprintf(stderr,
                    "progress implementation=mpi-data-parallel step=%d best=%.17g target=%.17g\n",
                    steps, global.fitness, options.target);
        }
    }

    local_elapsed = MPI_Wtime() - start;
    elapsed_max = 0.0;
    rc = MPI_Reduce(&local_elapsed, &elapsed_max, 1, MPI_DOUBLE, MPI_MAX, 0,
                    MPI_COMM_WORLD);
    if (mpi_check(rc, "MPI_Reduce(elapsed)", rank) != 0) {
        free(gbest);
        pso_swarm_destroy(&swarm);
        MPI_Abort(MPI_COMM_WORLD, rc);
        return EXIT_FAILURE;
    }
    if (history != NULL && fclose(history) != 0) {
        /* fclose 负责把缓冲的收敛轨迹 flush 落盘（CERT FIO34-C），
         * 关闭失败即 sidecar 不完整；仅 rank 0 持有该文件，须集体终止。
         * 残留文件保留在原路径——程序不删除用户路径，见 abandon_history。 */
        fputs("cannot close history file\n", stderr);
        free(gbest);
        pso_swarm_destroy(&swarm);
        MPI_Abort(MPI_COMM_WORLD, EXIT_FAILURE);
        return EXIT_FAILURE;
    }
    if (rank == 0) {
        result = (pso_result_line){
            .implementation = "mpi-data-parallel",
            .function = pso_bench_info(options.function)->name,
            .particles = options.particles,
            .dim = options.dim,
            .max_evals = options.max_evals,
            .max_iters = options.max_iters,
            .seed = options.seed,
            .boundary = pso_boundary_name(options.boundary),
            .dynamics = options.use_linear_inertia ? "linear-inertia" : "constriction",
            .target = options.target,
            .lower = options.lower,
            .upper = options.upper,
            .chi = options.chi,
            .c1 = options.c1,
            .c2 = options.c2,
            .vmax_ratio = options.vmax_ratio,
            .inertia_start = options.inertia_start,
            .inertia_end = options.inertia_end,
            .ranks = size,
            .threads = max_threads(),
            .migration_interval = 0,
            .iterations = steps,
            .evals = evals,
            .best = global.fitness,
            .success = reached,
            .fes_to_target = reached ? fes_to_target
                : (options.max_evals > UINT64_C(0) ? options.max_evals : evals),
            .stop_reason = options.stop_on_target && reached
                ? "target"
                : (options.max_evals > UINT64_C(0) ? "evaluation_budget" : "iteration_limit"),
            .stop_on_target = options.stop_on_target,
            .timing_scope = "solver",
            .elapsed_max_s = elapsed_max
        };
        if (pso_report_result(stdout, &result) != 0) {
            fputs("cannot write result line\n", stderr);
            free(gbest);
            pso_swarm_destroy(&swarm);
            MPI_Abort(MPI_COMM_WORLD, EXIT_FAILURE);
            return EXIT_FAILURE;
        }
    }
    free(gbest);
    pso_swarm_destroy(&swarm);
    MPI_Finalize();
    return 0;
}
