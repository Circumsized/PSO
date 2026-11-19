#include <mpi.h>

#include <stdio.h>

int main(int argc, char **argv)
{
    int rank, size, provided, thread_level, rc;
    double local_elapsed, max_elapsed = -1.0;
    struct {
        double value;
        int rank;
    } local, global;

    rc = MPI_Init_thread(&argc, &argv, MPI_THREAD_FUNNELED, &provided);
    if (rc != MPI_SUCCESS) {
        fputs("MPI_Init_thread failed\n", stderr);
        return 1;
    }
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);
    MPI_Query_thread(&thread_level);
    if (provided < MPI_THREAD_FUNNELED || thread_level < MPI_THREAD_FUNNELED) {
        fprintf(stderr, "rank %d: insufficient MPI thread level (%d/%d)\n",
                rank, provided, thread_level);
        MPI_Finalize();
        return 1;
    }
    local.value = (double)(size - rank);
    local.rank = rank;
    MPI_Allreduce(&local, &global, 1, MPI_DOUBLE_INT, MPI_MINLOC, MPI_COMM_WORLD);
    if (global.rank != size - 1 || global.value != 1.0) {
        fprintf(stderr, "rank %d: MINLOC failed (got %.3f from %d)\n",
                rank, global.value, global.rank);
        MPI_Finalize();
        return 1;
    }
    local_elapsed = (double)rank + 0.25;
    MPI_Reduce(&local_elapsed, &max_elapsed, 1, MPI_DOUBLE, MPI_MAX, 0,
               MPI_COMM_WORLD);
    if (rank == 0 && max_elapsed != (double)(size - 1) + 0.25) {
        fprintf(stderr, "MAX failed (got %.3f)\n", max_elapsed);
        MPI_Finalize();
        return 1;
    }
    if (rank == 0) {
        printf("MPI reductions: PASS (%d ranks, thread_level=%d)\n",
               size, thread_level);
    }
    MPI_Finalize();
    return 0;
}
