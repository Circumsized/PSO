/* 环境自检：验证 C 编译器、OpenMP 与 MPI 三者是否可用，以及它们的实际生效配置。
 * 不承载任何 PSO 行为断言，仅输出生效配置一行，供 CTest 的
 * PASS_REGULAR_EXPRESSION 匹配（target 名 pso_env_probe，测试名 environment_probe）。 */
#include <stdio.h>

#ifdef _OPENMP
#include <omp.h>
#endif

#ifdef PSO_WITH_MPI
#include <mpi.h>
#endif

int main(int argc, char **argv)
{
    int threads = 0;

#ifdef PSO_WITH_MPI
    int rank = 0, size = 1, provided = 0;
    if (MPI_Init_thread(&argc, &argv, MPI_THREAD_FUNNELED, &provided) != MPI_SUCCESS) {
        fputs("MPI_Init_thread failed\n", stderr);
        return 1;
    }
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);
#else
    (void)argc;
    (void)argv;
#endif

#ifdef _OPENMP
#pragma omp parallel reduction(+ : threads)
    threads++;
#else
    threads = 1;
#endif

#ifdef PSO_WITH_MPI
    printf("mpi rank %d/%d, thread_level=%d, omp threads=%d\n",
           rank, size, provided, threads);
    MPI_Finalize();
#else
    printf("serial, omp threads=%d\n", threads);
#endif
    return 0;
}
