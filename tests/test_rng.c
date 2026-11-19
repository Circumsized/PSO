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
    printf("rng reproducibility: PASS\n");
    return 0;
}
