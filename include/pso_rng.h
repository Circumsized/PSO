/* pso_rng.h — xoshiro256++ 伪随机数发生器
 *
 * 用 xoshiro256++ 替换 libc rand()/srand()，解决三个问题：
 *   1. 旧实现每个 MPI 进程用 time(NULL) 播种，同秒启动 => 各进程初始随机
 *      序列相同，且没有显式的粒子流映射。
 *   2. 旧实现 get_rand() 每次调用都重新 srand()，同秒内返回同一值，并污染
 *      全局 RNG 状态。
 *   3. rand() 非线程安全，低位随机性差，阻碍 OpenMP。
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

/* 循环左移。两个移位计数都掩到 0..63：k=0 时右移计数 (64-0)&63=0、
 * k=64 时左移计数 64&63=0，任意 int k 都不会触发移位计数 >=64 的未定义
 * 行为（调用点均为编译期常量，优化器会折叠，无运行时开销）。 */
static inline uint64_t pso_rotl(const uint64_t x, int k)
{
    return (x << (k & 63)) | (x >> ((64 - k) & 63));
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

/* 无偏的 [0,n) 整数：拒绝采样取模，避免 rand()%n 的取模偏置。
 * 阈值 threshold=(2^64-n)%n（与 Lemire 2019 论文同式的溢出安全等价），
 * 拒绝 v<threshold 后返回 v%n——这是经典无偏取模（arc4random_uniform
 * 同式），不是 Lemire Algorithm 5 的乘积高位变体。本函数当前无生产
 * 调用方（仅测试使用），保留作工具函数。 */
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

/* 由 base_seed 与粒子流编号确定性派生 RNG 状态。
 *
 * 已知限制（有意保留并归档，见 docs/07 [R12]）：该仿射派生的种子是
 * 同一条 splitmix64 状态序列上的等距点，各流填态字互为同一输出流的
 * 等距平移窗口——结构化相关，而非独立流。NumPy 并行随机数文档把
 * root_seed+worker_id 式派生明列为 UNSAFE（多轮批量扫参时不同根种子
 * 的 worker 种子集可重叠）。改为 SeedSequence 式哈希派生会使全部既有
 * 位级结果失效（违反项目可复现性红线，见 docs/02，与已冻结数字冲突），故维持
 * 现状，留待主版本变更时再议。乘法式（乘数约 1.14e19）下实际种子
 * 碰撞需 |Δseed|=K·|Δstream|，现实参数范围内不可达；缺陷在相关性
 * 而非碰撞。 */
static inline void pso_rng_seed_stream(pso_rng_t *r, uint64_t base_seed,
                                       uint64_t stream_id)
{
    uint64_t x = base_seed + 0x9E3779B97F4A7C15ULL * (stream_id + 1);
    pso_rng_seed(r, pso_splitmix64(&x));
}

#endif /* PSO_RNG_H */
