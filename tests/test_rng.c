#include "pso_rng.h"

#include <math.h>
#include <stdio.h>

int main(void)
{
    pso_rng_t a;
    pso_rng_t b;
    pso_rng_t c;
    double sum = 0.0;
    int i;

    pso_rng_seed_stream(&a, 1234U, 0U);
    pso_rng_seed_stream(&b, 1234U, 0U);
    pso_rng_seed_stream(&c, 1234U, 1U);
    for (i = 0; i < 1000; ++i) {
        const uint64_t va = pso_rng_next(&a);
        const uint64_t vb = pso_rng_next(&b);
        if (va != vb) {
            fprintf(stderr, "same stream is not reproducible at %d\n", i);
            return 1;
        }
        sum += pso_rng_double(&c);
    }
    if (!(sum > 0.0 && sum < 1000.0)) {
        fprintf(stderr, "rng range test failed\n");
        return 1;
    }
    for (i = 0; i < 1000; ++i) {
        const uint64_t value = pso_rng_below(&c, 7U);
        if (value >= 7U) {
            fprintf(stderr, "rng bounded value out of range\n");
            return 1;
        }
    }
    /* 金标准位流：直钉 xoshiro256++ 状态更新、splitmix64 填态与流派生的
     * 具体输出。位流一旦变化，全部下游金标（serial_golden_best 等）都会
     * 漂移；此处单元级直钉可在第一时间发现，而非等端到端金标失败后回溯。
     * 数值为纯整数/双精度算术，跨平台逐位一致。 */
    {
        static const uint64_t expected[] = {
            3461099069969393381ULL, 8061819933140498303ULL,
            12212854405366846594ULL, 4001603851269060711ULL,
            6505172811700904846ULL, 144992917163357390ULL
        };
        pso_rng_seed_stream(&a, 1234U, 0U);
        for (i = 0; i < 6; ++i) {
            const uint64_t value = pso_rng_next(&a);
            if (value != expected[i]) {
                fprintf(stderr,
                        "rng golden mismatch at %d: %llu != %llu\n",
                        i, (unsigned long long)value,
                        (unsigned long long)expected[i]);
                return 1;
            }
        }
    }
    /* 流派生差异：同 base_seed 下 stream 1 的首值必须不同于 stream 0，
     * 且等于其自身金标（同时钉住派生逻辑不被意外改成常数或同流）。 */
    {
        static const uint64_t expected_stream1 = 17370043548596047016ULL;
        pso_rng_seed_stream(&a, 1234U, 1U);
        const uint64_t value = pso_rng_next(&a);
        if (value != expected_stream1) {
            fprintf(stderr, "rng stream-1 golden mismatch: %llu\n",
                    (unsigned long long)value);
            return 1;
        }
    }
    /* 双精度与区间金标：高 53 位取值，17 位有效数字往返一致。 */
    {
        static const double expected_double[] = {
            0.18762655654241867, 0.43703213428489351
        };
        static const double expected_range[] = {
            -3.1237344345758133, -0.62967865715106441
        };
        pso_rng_seed_stream(&a, 1234U, 0U);
        for (i = 0; i < 2; ++i) {
            if (pso_rng_double(&a) != expected_double[i]) {
                fprintf(stderr, "rng double golden mismatch at %d\n", i);
                return 1;
            }
        }
        pso_rng_seed_stream(&a, 1234U, 0U);
        for (i = 0; i < 2; ++i) {
            if (pso_rng_range(&a, -5.0, 5.0) != expected_range[i]) {
                fprintf(stderr, "rng range golden mismatch at %d\n", i);
                return 1;
            }
        }
    }
    /* 有界整数金标 + n<=1 边界：拒绝采样路径与守卫分支都要钉住。 */
    {
        static const uint64_t expected[] = { 4, 2, 6, 3 };
        pso_rng_seed_stream(&a, 1234U, 0U);
        for (i = 0; i < 4; ++i) {
            const uint64_t value = pso_rng_below(&a, 7U);
            if (value != expected[i]) {
                fprintf(stderr, "rng below(7) golden mismatch at %d\n", i);
                return 1;
            }
        }
        pso_rng_seed_stream(&a, 1234U, 0U);
        if (pso_rng_below(&a, 0U) != 0U || pso_rng_below(&a, 1U) != 0U) {
            fprintf(stderr, "rng below n<=1 boundary mismatch\n");
            return 1;
        }
    }
    printf("rng reproducibility: PASS\n");
    return 0;
}
