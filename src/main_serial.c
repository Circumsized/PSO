#include "pso_cli.h"
#include "pso_core.h"
#include "pso_pin.h"
#include "pso_report.h"

#include <inttypes.h>
#include <math.h>
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
 * ——root/Linux 下 `--history /dev/full` 曾会 unlink 掉字符设备本身。
 * 空/残缺 sidecar 由下游消费者硬失败拒绝（load_history/parse_log_strict），
 * run_experiments 的非空 sidecar 预检也会拦截同目录重跑。 */
static void abandon_history(FILE *history)
{
    if (history != NULL) {
        fclose(history);
    }
}

int main(int argc, char **argv)
{
    pso_options options;
    pso_swarm swarm;
    pso_result_line result;
    FILE *history = NULL;
    int rc;
    int limit;
    int steps = 0;
    int reached;
    double start;
    double elapsed;
    uint64_t evals;
    uint64_t fes_to_target;

    pso_options_init(&options);
    rc = pso_parse_args(argc, argv, &options);
    if (rc == 1) return 0;
    if (rc != 0) return EXIT_FAILURE;
    if (options.pin_cpus) {
        char pin_error[128];
        int pin_rc = pso_pin_current_process(0, max_threads(), pin_error,
                                             sizeof(pin_error));
        if (pin_rc != PSO_PIN_OK) {
            fprintf(stderr, "pin-cpus %s: %s\n",
                    pin_rc == PSO_PIN_UNSUPPORTED ? "unsupported" : "failed",
                    pin_error);
            return EXIT_FAILURE;
        }
    }
    limit = pso_options_iteration_limit(&options);
    if (options.history_path != NULL) {
        history = fopen(options.history_path, "w");
        if (history == NULL) {
            fputs("cannot open history file\n", stderr);
            return EXIT_FAILURE;
        }
    }
    if (pso_swarm_init_range(&swarm, &options, options.particles, options.seed,
                             UINT64_C(0), UINT64_C(0)) != 0) {
        fputs("failed to allocate serial swarm\n", stderr);
        abandon_history(history);
        return EXIT_FAILURE;
    }
    start = pso_wall_time();
    evals = (uint64_t)options.particles;
    if (!isfinite(swarm.best_fitness)) {
        fputs("initial swarm has no finite fitness\n", stderr);
        abandon_history(history);
        pso_swarm_destroy(&swarm);
        return EXIT_FAILURE;
    }
    if (swarm.nonfinite_count > 0) {
        fprintf(stderr,
                "warning: %d of %d particles have non-finite fitness "
                "(normalized to +inf)\n",
                swarm.nonfinite_count, options.particles);
    }
    reached = swarm.best_fitness <= options.target;
    fes_to_target = reached ? evals : UINT64_C(0);
    if (history != NULL) {
        if (pso_history_append(history, 0, evals, swarm.best_fitness) != 0) {
            fputs("cannot write history file\n", stderr);
            abandon_history(history);
            pso_swarm_destroy(&swarm);
            return EXIT_FAILURE;
        }
    }
    while (steps < limit && !(options.stop_on_target && reached)) {
        pso_swarm_step(&swarm, NULL, steps, limit);
        ++steps;
        evals += (uint64_t)options.particles;
        reached = swarm.best_fitness <= options.target;
        if (reached && fes_to_target == UINT64_C(0)) fes_to_target = evals;
        if (history != NULL) {
            if (pso_history_append(history, steps, evals, swarm.best_fitness) != 0) {
                fputs("cannot write history file\n", stderr);
                abandon_history(history);
                pso_swarm_destroy(&swarm);
                return EXIT_FAILURE;
            }
        }
        if (options.verbose && steps % options.report_interval == 0) {
            fprintf(stderr, "progress implementation=serial step=%d best=%.17g target=%.17g\n",
                    steps, swarm.best_fitness, options.target);
        }
    }
    elapsed = pso_wall_time() - start;
    if (history != NULL && fclose(history) != 0) {
        /* 缓冲区里的收敛轨迹要等 fclose 的 flush 才真正落盘（CERT FIO34-C）：
         * 关闭失败意味着 sidecar 不完整，宁可失败也不能带 0 退出码静默发布；
         * 但残留文件保留在原路径——程序不删除用户路径，见 abandon_history。 */
        fputs("cannot close history file\n", stderr);
        pso_swarm_destroy(&swarm);
        return EXIT_FAILURE;
    }
    result = (pso_result_line){
        .implementation = "serial",
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
        .ranks = 1,
        .threads = max_threads(),
        .migration_interval = 0,
        .iterations = steps,
        .evals = evals,
        .best = swarm.best_fitness,
        .success = reached,
        .fes_to_target = reached ? fes_to_target
            : (options.max_evals > UINT64_C(0) ? options.max_evals : evals),
        .stop_reason = options.stop_on_target && reached
            ? "target"
            : (options.max_evals > UINT64_C(0) ? "evaluation_budget" : "iteration_limit"),
        .stop_on_target = options.stop_on_target,
        .timing_scope = "solver",
        .elapsed_max_s = elapsed
    };
    if (pso_report_result(stdout, &result) != 0) {
        fputs("cannot write result line\n", stderr);
        pso_swarm_destroy(&swarm);
        return EXIT_FAILURE;
    }
    pso_swarm_destroy(&swarm);
    return 0;
}
