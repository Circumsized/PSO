#include "pso_report.h"

#include <inttypes.h>
#include <stdio.h>

int pso_report_result(FILE *out, const pso_result_line *result)
{
    int written;
    if (out == NULL || result == NULL) {
        return -1;
    }
    written = fprintf(out,
            "record=result schema=pso_v1 implementation=%s function=%s particles=%d dim=%d "
            "max_evals=%" PRIu64 " max_iters=%d seed=%" PRIu64 " boundary=%s dynamics=%s "
            "target=%.17g lower=%.17g upper=%.17g chi=%.17g c1=%.17g c2=%.17g vmax_ratio=%.17g "
            "inertia_start=%.17g inertia_end=%.17g ranks=%d threads=%d migration_interval=%d "
            "iterations=%d evals=%" PRIu64 " best=%.17g success=%d fes_to_target=%" PRIu64
            " stop_reason=%s stop_on_target=%d timing_scope=%s elapsed_max_s=%.9f\n",
            result->implementation, result->function, result->particles, result->dim,
            result->max_evals, result->max_iters, result->seed, result->boundary,
            result->dynamics, result->target, result->lower, result->upper,
            result->chi, result->c1, result->c2, result->vmax_ratio,
            result->inertia_start, result->inertia_end, result->ranks, result->threads,
            result->migration_interval, result->iterations, result->evals, result->best,
            result->success, result->fes_to_target, result->stop_reason,
            result->stop_on_target, result->timing_scope, result->elapsed_max_s);
    if (written < 0) {
        return -1;
    }
    /* fprintf 成功只说明字节进了缓冲区；磁盘满等延迟写错误要靠显式 flush
     * 才能在本函数内暴露，否则进程仍会以退出码 0 结束（CERT FIO34-C）。 */
    if (fflush(out) != 0) {
        return -1;
    }
    return 0;
}

int pso_history_append(FILE *out, int iteration, uint64_t evals, double best)
{
    int written;
    if (out == NULL) {
        return -1;
    }
    written = fprintf(out, "iteration=%d evals=%llu best=%.17g\n",
                      iteration, (unsigned long long)evals, best);
    if (written < 0) {
        return -1;
    }
    return 0;
}
