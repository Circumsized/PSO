#include "pso_bench.h"

#include <math.h>
#include <stdio.h>

static int close_to_zero(double value)
{
    return fabs(value) < 1.0e-12;
}

static int close_relative(double value, double reference)
{
    /* 相对容差 1e-9：实测跨优化档重排噪声 <=7e-16、模拟 libm ±1 ULP
     * 差异 <=4e-15，而系数级变异的最小相对信号 3.1e-3——两侧各有
     * 5 个数量级以上余量（docs/03 的跨平台 2 ULP 记录也在噪声侧）。 */
    return fabs(value / reference - 1.0) < 1.0e-9;
}

int main(void)
{
    double x[8] = {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
    double ones[8] = {1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0};
    int failed = 0;
    int id;

    if (!close_to_zero(pso_bench_eval(PSO_FN_SPHERE, x, 8))) failed = 1;
    if (!close_to_zero(pso_bench_eval(PSO_FN_RASTRIGIN, x, 8))) failed = 1;
    if (!close_to_zero(pso_bench_eval(PSO_FN_GRIEWANK, x, 8))) failed = 1;
    if (!close_to_zero(pso_bench_eval(PSO_FN_ACKLEY, x, 8))) failed = 1;
    if (!close_to_zero(pso_bench_eval(PSO_FN_ROSENBROCK, ones, 8))) failed = 1;

    for (id = 0; id < PSO_FN_COUNT; ++id) {
        const pso_fn_info *info = pso_bench_info((pso_fn_id)id);
        if (info == NULL || info->name == NULL || info->lower >= info->upper ||
            fabs(info->optimum) > 1.0e-12) {
            failed = 1;
        }
        if (pso_bench_from_name(info->name) != (pso_fn_id)id) {
            failed = 1;
        }
    }

    {
        const double large[] = {1.0e155, -2.0e155};
        const double expected[] = {2.5e306, 1.0e307};
        for (int i = 0; i < 2; ++i) {
            const double actual = pso_bench_eval(PSO_FN_GRIEWANK, &large[i], 1);
            if (!isfinite(actual) || fabs(actual / expected[i] - 1.0) > 1.0e-14) {
                fputs("Griewank intermediate overflow\n", stderr);
                failed = 1;
            }
        }
    }

    {
        /* 非最优点上的系数敏感钉子：ones[8] 是 rastrigin/rosenbrock 的
         * 最优点邻域（x=1 时两处系数项恰好抵消），系数级变异在其上
         * 信号为零；这里用非整数坐标让 10.0/100.0/4000.0 的改动可见。
         * 三个参考值已从本文件链接的 pso_bench.c 实编译核对。 */
        const double probe[8] = {0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8};
        if (!close_relative(pso_bench_eval(PSO_FN_RASTRIGIN, probe, 8),
                            100.13016994374948)) {
            fputs("rastrigin coefficient drift\n", stderr);
            failed = 1;
        }
        if (!close_relative(pso_bench_eval(PSO_FN_ROSENBROCK, probe, 8),
                            67.76)) {
            fputs("rosenbrock coefficient drift\n", stderr);
            failed = 1;
        }
        if (!close_relative(pso_bench_eval(PSO_FN_GRIEWANK, probe, 8),
                            0.16668306121728593)) {
            fputs("griewank coefficient drift\n", stderr);
            failed = 1;
        }
    }

    if (pso_bench_from_name("not-a-function") != PSO_FN_COUNT) failed = 1;
    if (!isnan(pso_bench_eval(PSO_FN_SPHERE, NULL, 8))) failed = 1;
    if (!isnan(pso_bench_eval(PSO_FN_SPHERE, x, 0))) failed = 1;
    if (!isnan(pso_bench_eval(PSO_FN_SPHERE, x, -1))) failed = 1;
    if (!isnan(pso_bench_eval((pso_fn_id)-1, x, 8))) failed = 1;
    if (!isnan(pso_bench_eval(PSO_FN_COUNT, x, 8))) failed = 1;

    if (failed) {
        fprintf(stderr, "benchmark function test failed\n");
        return 1;
    }
    printf("benchmark functions: PASS\n");
    return 0;
}
