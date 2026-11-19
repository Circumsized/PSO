/* pso_report.h — 统一的一行结果输出 */
#ifndef PSO_REPORT_H
#define PSO_REPORT_H

#include <stdint.h>
#include <stdio.h>
#include "pso_config.h"

typedef struct {
    const char *implementation;
    const char *function;
    int particles;
    int dim;
    uint64_t max_evals;
    int max_iters;
    uint64_t seed;
    const char *boundary;
    const char *dynamics;
    double target;
    double lower;
    double upper;
    double chi;
    double c1;
    double c2;
    double vmax_ratio;
    double inertia_start;
    double inertia_end;
    int ranks;
    int threads;
    int migration_interval;
    int iterations;
    uint64_t evals;
    double best;
    int success;
    uint64_t fes_to_target;
    const char *stop_reason;
    int stop_on_target;
    const char *timing_scope;
    double elapsed_max_s;
} pso_result_line;

/* 输出 pso_v1 结果行。返回 0 成功、-1 失败：结果行是整个输出契约（架构
 * 不变量 3），stdout 重定向到磁盘或管道时写入可能失败，必须让调用方能够
 * 据此中止运行，而不是带着退出码 0 丢掉唯一的测量记录。 */
int pso_report_result(FILE *out, const pso_result_line *result);

/* 收敛轨迹行：iteration=0 记初始种群，之后每代一行。返回 0 成功、-1 失败。 */
int pso_history_append(FILE *out, int iteration, uint64_t evals, double best);

#endif /* PSO_REPORT_H */
