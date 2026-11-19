/* pso_rng.h — xoshiro256++ 伪随机数发生器
 *
 * 用 xoshiro256++ 替换 libc rand()/srand()，解决三个问题：
 *   1. 旧实现每个 MPI 进程用 time(NULL) 播种，同秒启动 => 各进程初始随机
 *      序列相同；且没有显式的粒子流映射（有效分区的重复见 docs/01 §2.2）。
 *   2. 旧实现 get_rand() 每次调用都重新 srand()，同秒内返回同一值，并污染
 *      全局 RNG 状态（缺陷 A4）。
 *   3. rand() 非线程安全，低位随机性差，阻碍 OpenMP（缺陷 C4）。
 *
 * 本实现为纯头文件、无全局状态：每个粒子持有一份 pso_rng_t，流由
 * base_seed 与粒子流编号经 splitmix64 确定性派生；粒子与流固定绑定，
 * 因而不受 MPI 分块或 OpenMP 调度影响，固定 base_seed 即可复现实验。
 *
 * 算法出处：Blackman & Vigna, "Scrambled Linear Pseudorandom Number
 * Generators" (2021)；xoshiro256++ 周期 2^256-1，通过 BigCrush。
 */
#ifndef PSO_RNG_H
#define PSO_RNG_H

#include <stdint.h>

typedef struct {
    uint64_t s[4];
} pso_rng_t;

static inline uint64_t pso_rotl(const uint64_t x, int k)
{
    return (x << k) | (x >> (64 - k));
}

/* splitmix64：用于把一个 64 位种子扩展成 256 位状态，
 * 也用于从 base_seed 和确定的粒子流编号派生状态。 */
static inline uint64_t pso_splitmix64(uint64_t *x)
{
    uint64_t z = (*x += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

static inline void pso_rng_seed(pso_rng_t *r, uint64_t seed)
{
    uint64_t x = seed;
    int i;
    for (i = 0; i < 4; ++i) {
        r->s[i] = pso_splitmix64(&x);
    }
    /* splitmix64 不会产生全零状态，但显式兜底以防万一：
     * xoshiro 的全零状态是吸收态，会永远返回 0。 */
    if ((r->s[0] | r->s[1] | r->s[2] | r->s[3]) == 0) {
        r->s[0] = 0x9E3779B97F4A7C15ULL;
    }
}

static inline uint64_t pso_rng_next(pso_rng_t *r)
{
    const uint64_t result = pso_rotl(r->s[0] + r->s[3], 23) + r->s[0];
    const uint64_t t = r->s[1] << 17;

    r->s[2] ^= r->s[0];
    r->s[3] ^= r->s[1];
    r->s[1] ^= r->s[2];
    r->s[0] ^= r->s[3];
    r->s[2] ^= t;
    r->s[3] = pso_rotl(r->s[3], 45);

    return result;
}

/* 返回 [0,1) 上的均匀双精度数：取高 53 位，避免 rand() 低位质量差的问题。 */
static inline double pso_rng_double(pso_rng_t *r)
{
    return (double)(pso_rng_next(r) >> 11) * (1.0 / 9007199254740992.0);
}

static inline double pso_rng_range(pso_rng_t *r, double lo, double hi)
{
    return lo + (hi - lo) * pso_rng_double(r);
}

/* 无偏的 [0,n) 整数：用 Lemire 拒绝采样，避免 rand()%n 的取模偏置。 */
static inline uint64_t pso_rng_below(pso_rng_t *r, uint64_t n)
{
    uint64_t threshold, v;
    if (n <= 1) {
        return 0;
    }
    threshold = (0 - n) % n; /* 2^64 mod n */
    for (;;) {
        v = pso_rng_next(r);
        if (v >= threshold) {
            return v % n;
        }
    }
}

/* 由 base_seed 与粒子流编号确定性派生 RNG 状态。 */
static inline void pso_rng_seed_stream(pso_rng_t *r, uint64_t base_seed,
                                       uint64_t stream_id)
{
    uint64_t x = base_seed + 0x9E3779B97F4A7C15ULL * (stream_id + 1);
    pso_rng_seed(r, pso_splitmix64(&x));
}

#endif /* PSO_RNG_H */
