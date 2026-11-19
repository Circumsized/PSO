/* pso_bench.h — 标准测试函数集
 *
 * 内置 5 个常用基准函数，并统一元数据（搜索域、全局最优值、
 * 最优点位置），使基准协议可以自动校验与报告。
 */
#ifndef PSO_BENCH_H
#define PSO_BENCH_H

typedef enum {
    PSO_FN_SPHERE = 0,
    PSO_FN_ROSENBROCK,
    PSO_FN_RASTRIGIN,
    PSO_FN_GRIEWANK,
    PSO_FN_ACKLEY,
    PSO_FN_COUNT
} pso_fn_id;

typedef struct {
    const char *name;
    double lower;      /* 搜索域下界（各维相同） */
    double upper;      /* 搜索域上界 */
    double optimum;    /* 全局最优的函数值 */
    double opt_x;      /* 全局最优点各维取值（用于自检） */
} pso_fn_info;

/* 计算指定测试函数在 x[0..dim-1] 处的值。 */
double pso_bench_eval(pso_fn_id id, const double *x, int dim);

/* 查询函数元数据；id 非法时返回 NULL。 */
const pso_fn_info *pso_bench_info(pso_fn_id id);

/* 按名称解析函数 id；无匹配时返回 PSO_FN_COUNT。 */
pso_fn_id pso_bench_from_name(const char *name);

#endif /* PSO_BENCH_H */
