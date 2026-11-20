#include "pso_cli.h"
#include "pso_report.h"

#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static int expect_parse_failure(int argc, char **argv)
{
    pso_options options;
    pso_options_init(&options);
    return pso_parse_args(argc, argv, &options) == -1;
}

static int test_history_append(void)
{
    char buffer[128];
    FILE *file = tmpfile();
    if (file == NULL) {
        fputs("tmpfile unavailable for history test\n", stderr);
        return 1;
    }
    if (pso_history_append(file, 0, 8, 1.5) != 0 ||
        pso_history_append(file, 3, 32, 0.25) != 0 ||
        pso_history_append(NULL, 1, 1, 1.0) != -1) {
        fputs("history append return contract broken\n", stderr);
        fclose(file);
        return 1;
    }
    rewind(file);
    if (!fgets(buffer, sizeof(buffer), file) ||
        strcmp(buffer, "iteration=0 evals=8 best=1.5\n") != 0) {
        fprintf(stderr, "history line 1 malformed: %s\n", buffer);
        fclose(file);
        return 1;
    }
    if (!fgets(buffer, sizeof(buffer), file) ||
        strcmp(buffer, "iteration=3 evals=32 best=0.25\n") != 0) {
        fprintf(stderr, "history line 2 malformed: %s\n", buffer);
        fclose(file);
        return 1;
    }
    fclose(file);
    return 0;
}

int main(void)
{
    char *negative_seed[] = {"test", "--seed", "-1"};
    char *nan_target[] = {"test", "--target", "nan"};
    char *inf_upper[] = {"test", "--upper", "inf"};
    char *zero_particles[] = {"test", "--particles", "0"};
    char *blanked_negative_seed[] = {"test", "--seed", " -1"};
    char *blanked_negative_budget[] = {"test", "--particles", "4", "--dim", "2",
                                       "--max-evals", "\t-1"};
    char *max_seed[] = {"test", "--seed", "18446744073709551615"};
    char *max_seed_blank[] = {"test", "--seed", " 18446744073709551615"};
    char *valid[] = {"test", "--function", "sphere", "--particles", "32",
                     "--dim", "3", "--max-evals", "32", "--seed", "7"};
    char *bounds_before_function[] = {"test", "--lower", "-3", "--upper", "4",
                                      "--function", "sphere"};
    char *bounds_after_function[] = {"test", "--function", "sphere", "--lower", "-3",
                                     "--upper", "4"};
    char *unknown_option[] = {"test", "--nope"};
    char *zero_dim[] = {"test", "--dim", "0"};
    char *negative_iters[] = {"test", "--max-iters", "-1"};
    pso_options options;

    if (!expect_parse_failure(3, negative_seed) ||
        !expect_parse_failure(3, nan_target) ||
        !expect_parse_failure(3, inf_upper) ||
        !expect_parse_failure(3, zero_particles) ||
        !expect_parse_failure(3, blanked_negative_seed) ||
        !expect_parse_failure(7, blanked_negative_budget)) {
        fputs("invalid CLI value was accepted\n", stderr);
        fputs("空白前缀负号绕过无符号守卫\n", stderr);
        return 1;
    }
    /* 负例：未知选项走 src/pso_cli.c 的 else 分支返回 -1；--dim 0 与
     * --max-iters -1 解析成功但被随后的值域校验拒绝，同样返回 -1。 */
    if (!expect_parse_failure(2, unknown_option) ||
        !expect_parse_failure(3, zero_dim) ||
        !expect_parse_failure(3, negative_iters)) {
        fputs("未知选项或非正整数值被接受\n", stderr);
        return 1;
    }
    pso_options_init(&options);
    if (pso_parse_args(11, valid, &options) != 0 ||
        options.function != PSO_FN_SPHERE || options.particles != 32 ||
        options.dim != 3 || options.max_evals != UINT64_C(32) || options.seed != UINT64_C(7)) {
        fputs("valid CLI value was rejected\n", stderr);
        return 1;
    }
    if (pso_options_iteration_limit(&options) != 0) {
        fputs("initial-only evaluation budget did not produce zero steps\n", stderr);
        return 1;
    }
    pso_options_init(&options);
    if (pso_parse_args(7, bounds_before_function, &options) != 0 ||
        options.lower != -3.0 || options.upper != 4.0) {
        fputs("bounds before --function were overwritten\n", stderr);
        return 1;
    }
    pso_options_init(&options);
    if (pso_parse_args(7, bounds_after_function, &options) != 0 ||
        options.lower != -3.0 || options.upper != 4.0) {
        fputs("bounds after --function were rejected\n", stderr);
        return 1;
    }

    {
        char *no_stop_args[] = {"test", "--no-early-stop"};
        char *bad_budget[] = {"test", "--particles", "8", "--max-evals", "4"};
        char *bounds_before_function[] = {"test", "--lower", "-2", "--upper", "3",
                                          "--function", "sphere"};
        char huge_budget_text[32];
        char *huge_budget[] = {"test", "--particles", "1", "--max-evals",
                               huge_budget_text};
        pso_options parsed;
        pso_options_init(&parsed);
        if (pso_parse_args(2, no_stop_args, &parsed) != 0 || parsed.stop_on_target) {
            fputs("--no-early-stop was not honored\n", stderr);
            return 1;
        }
        if (!expect_parse_failure(5, bad_budget)) {
            fputs("budget smaller than particle count was accepted\n", stderr);
            return 1;
        }
        pso_options_init(&parsed);
        if (pso_parse_args(7, bounds_before_function, &parsed) != 0 ||
            parsed.function != PSO_FN_SPHERE || parsed.lower != -2.0 ||
            parsed.upper != 3.0) {
            fputs("explicit bounds depended on --function argument order\n", stderr);
            return 1;
        }
        snprintf(huge_budget_text, sizeof(huge_budget_text), "%llu",
                 (unsigned long long)INT_MAX + 2ULL);
        if (!expect_parse_failure(5, huge_budget)) {
            fputs("budget requiring more than INT_MAX iterations was accepted\n", stderr);
            return 1;
        }
    }

    {
        char *history_args[] = {"test", "--history", "conv.log"};
        pso_options parsed;
        pso_options_init(&parsed);
        if (pso_parse_args(3, history_args, &parsed) != 0 ||
            parsed.history_path == NULL ||
            strcmp(parsed.history_path, "conv.log") != 0) {
            fputs("--history path was not parsed\n", stderr);
            return 1;
        }
        pso_options_init(&parsed);
        if (parsed.history_path != NULL) {
            fputs("history_path default is not NULL\n", stderr);
            return 1;
        }
    }
    {
        pso_options u64_seed;
        pso_options_init(&u64_seed);
        if (pso_parse_args(3, max_seed, &u64_seed) != 0 ||
            u64_seed.seed != UINT64_C(18446744073709551615)) {
            fputs("UINT64_MAX 合法 seed 被拒绝\n", stderr);
            return 1;
        }
        pso_options_init(&u64_seed);
        if (pso_parse_args(3, max_seed_blank, &u64_seed) != 0 ||
            u64_seed.seed != UINT64_C(18446744073709551615)) {
            fputs("带前导空白的 UINT64_MAX seed 被拒绝\n", stderr);
            return 1;
        }
    }
    if (test_history_append() != 0) {
        return 1;
    }
    puts("CLI validation: PASS");
    return 0;
}
