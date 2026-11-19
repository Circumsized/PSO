#include "pso_cli.h"

#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int next_arg(int argc, char **argv, int *index, const char **value)
{
    if (*index + 1 >= argc) {
        fprintf(stderr, "missing value for %s\n", argv[*index]);
        return -1;
    }
    *value = argv[++(*index)];
    return 0;
}

static int parse_int(const char *option, const char *text, int *out)
{
    char *end = NULL;
    long value;
    errno = 0;
    value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value < INT_MIN || value > INT_MAX) {
        fprintf(stderr, "invalid value for %s: %s\n", option, text);
        return -1;
    }
    *out = (int)value;
    return 0;
}

static int parse_u64(const char *option, const char *text, uint64_t *out)
{
    char *end = NULL;
    unsigned long long value;
    const char *digits = text + strspn(text, " \t\n\v\f\r");
    /* strtoull 跳过前导空白后还接受正负号：负号在无符号回绕中变成巨大
     * 正整数（" -1" → ULLONG_MAX）且不置 errno，只看 text[0] 的守卫
     * 会被前导空白绕过。判负号必须在跳过空白之后。 */
    errno = 0;
    value = strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || *digits == '-') {
        fprintf(stderr, "invalid value for %s: %s\n", option, text);
        return -1;
    }
    *out = (uint64_t)value;
    return 0;
}

static int parse_double(const char *option, const char *text, double *out)
{
    char *end = NULL;
    double value;
    errno = 0;
    value = strtod(text, &end);
    if (errno != 0 || end == text || *end != '\0' || !isfinite(value)) {
        fprintf(stderr, "invalid value for %s: %s\n", option, text);
        return -1;
    }
    *out = value;
    return 0;
}

static int parse_boundary(const char *option, const char *text, pso_boundary_mode *mode)
{
    if (strcmp(text, "reflect") == 0) {
        *mode = PSO_BOUNDARY_REFLECT;
    } else if (strcmp(text, "clamp") == 0) {
        *mode = PSO_BOUNDARY_CLAMP;
    } else if (strcmp(text, "reinit") == 0) {
        *mode = PSO_BOUNDARY_REINIT;
    } else {
        fprintf(stderr, "invalid value for %s: %s\n", option, text);
        return -1;
    }
    return 0;
}

int pso_parse_args_for(int argc, char **argv, pso_options *options,
                       int mpi_mode, int island_mode)
{
    int i;
    int lower_set = 0;
    int upper_set = 0;
    const char *value;
    if (options == NULL) {
        return -1;
    }

    for (i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            pso_print_usage(argv[0], mpi_mode, island_mode);
            return 1;
        } else if (strcmp(argv[i], "--particles") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_int("--particles", value, &options->particles)) return -1;
        } else if (strcmp(argv[i], "--dim") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_int("--dim", value, &options->dim)) return -1;
        } else if (strcmp(argv[i], "--max-evals") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_u64("--max-evals", value, &options->max_evals)) return -1;
        } else if (strcmp(argv[i], "--max-iters") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_int("--max-iters", value, &options->max_iters)) return -1;
        } else if (strcmp(argv[i], "--target") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_double("--target", value, &options->target)) return -1;
        } else if (strcmp(argv[i], "--function") == 0) {
            const pso_fn_info *info;
            if (next_arg(argc, argv, &i, &value)) return -1;
            options->function = pso_bench_from_name(value);
            if (options->function == PSO_FN_COUNT) {
                fprintf(stderr, "unknown function: %s\n", value);
                return -1;
            }
            info = pso_bench_info(options->function);
            if (!lower_set) options->lower = info->lower;
            if (!upper_set) options->upper = info->upper;
        } else if (strcmp(argv[i], "--lower") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_double("--lower", value, &options->lower)) return -1;
            lower_set = 1;
        } else if (strcmp(argv[i], "--upper") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_double("--upper", value, &options->upper)) return -1;
            upper_set = 1;
        } else if (strcmp(argv[i], "--seed") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_u64("--seed", value, &options->seed)) return -1;
        } else if (strcmp(argv[i], "--migration-interval") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_int("--migration-interval", value, &options->migration_interval)) return -1;
        } else if (strcmp(argv[i], "--report-interval") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_int("--report-interval", value, &options->report_interval)) return -1;
        } else if (strcmp(argv[i], "--boundary") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_boundary("--boundary", value, &options->boundary)) return -1;
        } else if (strcmp(argv[i], "--chi") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_double("--chi", value, &options->chi)) return -1;
        } else if (strcmp(argv[i], "--c1") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_double("--c1", value, &options->c1)) return -1;
        } else if (strcmp(argv[i], "--c2") == 0) {
            if (next_arg(argc, argv, &i, &value) || parse_double("--c2", value, &options->c2)) return -1;
        } else if (strcmp(argv[i], "--linear-inertia") == 0) {
            options->use_linear_inertia = 1;
        } else if (strcmp(argv[i], "--no-early-stop") == 0) {
            options->stop_on_target = 0;
        } else if (strcmp(argv[i], "--pin-cpus") == 0) {
            options->pin_cpus = 1;
        } else if (strcmp(argv[i], "--history") == 0) {
            if (next_arg(argc, argv, &i, &value)) return -1;
            options->history_path = value;
        } else if (strcmp(argv[i], "--verbose") == 0) {
            options->verbose = 1;
        } else if (strcmp(argv[i], "--quiet") == 0) {
            options->verbose = 0;
        } else {
            fprintf(stderr, "unknown option: %s\n", argv[i]);
            return -1;
        }
    }

    if (options->particles <= 0 || options->dim <= 0 || options->max_iters <= 0 ||
        options->target < 0.0 || !isfinite(options->target) ||
        !isfinite(options->lower) || !isfinite(options->upper) ||
        options->lower >= options->upper || options->migration_interval <= 0 ||
        options->report_interval <= 0 || options->chi <= 0.0 ||
        !isfinite(options->chi) || options->c1 < 0.0 || !isfinite(options->c1) ||
        options->c2 < 0.0 || !isfinite(options->c2) || options->vmax_ratio <= 0.0 ||
        !isfinite(options->vmax_ratio) || !isfinite(options->inertia_start) ||
        !isfinite(options->inertia_end) || options->inertia_start < 0.0 ||
        options->inertia_end < 0.0 ||
        (options->max_evals > 0 && options->max_evals < (uint64_t)options->particles)) {
        fprintf(stderr, "invalid option value; run with --help\n");
        return -1;
    }
    if (options->max_evals > 0 &&
        (options->max_evals - (uint64_t)options->particles) /
            (uint64_t)options->particles > (uint64_t)INT_MAX) {
        fprintf(stderr, "max-evals requires more than INT_MAX iterations\n");
        return -1;
    }
    return 0;
}

int pso_parse_args(int argc, char **argv, pso_options *options)
{
    return pso_parse_args_for(argc, argv, options, 0, 0);
}

void pso_print_usage(const char *program, int mpi_mode, int island_mode)
{
    printf("Usage: %s [options]\n", program);
    printf("  --function NAME       sphere|rosenbrock|rastrigin|griewank|ackley\n");
    printf("  --particles N         total particles (default %d)\n", PSO_DEFAULT_PARTICLES);
    printf("  --dim N               dimensions (default %d)\n", PSO_DEFAULT_DIM);
    printf("  --max-evals N         unified objective-evaluation budget\n");
    printf("  --max-iters N         iteration limit when max-evals is zero\n");
    printf("  --target X            success threshold (default %.3g)\n", PSO_DEFAULT_TARGET);
    printf("  --no-early-stop       continue to the configured budget after success\n");
    printf("  --history PATH        write per-iteration convergence best to PATH\n");
    printf("  --pin-cpus            bind the process to fixed logical CPUs\n");
    printf("  --lower X             override the function's lower search bound\n");
    printf("  --upper X             override the function's upper search bound\n");
    printf("  --seed N              reproducible base seed (default %llu)\n", (unsigned long long)PSO_DEFAULT_SEED);
    printf("  --boundary MODE       reflect|clamp|reinit (default reflect)\n");
    printf("  --chi X               velocity constriction factor (default %.5f)\n", PSO_DEFAULT_CHI);
    printf("  --c1 X                effective cognitive coefficient (default %.5f)\n", PSO_DEFAULT_C1);
    printf("  --c2 X                effective social coefficient (default %.5f)\n", PSO_DEFAULT_C2);
    printf("  --linear-inertia      use w=%.2f -> %.2f instead of constriction factor\n", PSO_DEFAULT_INERTIA_START, PSO_DEFAULT_INERTIA_END);
    printf("  --migration-interval N  island migration period (default %d)\n", PSO_DEFAULT_MIGRATION);
    printf("  --report-interval N   progress report period (default %d)\n", PSO_DEFAULT_REPORT_INTERVAL);
    printf("  --verbose             print progress (off by default)\n");
    printf("  --quiet               keep progress off explicitly (default)\n");
    printf("  --help                show this help\n");
    if (mpi_mode) {
        printf("MPI mode: total particles are divided across ranks.\n");
    }
    if (island_mode) {
        printf("Island mode: best-worst migration on a unidirectional ring.\n");
    }
}
