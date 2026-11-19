/* pso_cli.h — 三个可执行程序共用的命令行解析 */
#ifndef PSO_CLI_H
#define PSO_CLI_H

#include "pso_config.h"

/* 0=成功，1=用户请求 --help，-1=参数错误。 */
int pso_parse_args(int argc, char **argv, pso_options *options);
/* 同上，但 --help 输出按入口类型附带 MPI/island 说明。 */
int pso_parse_args_for(int argc, char **argv, pso_options *options,
                       int mpi_mode, int island_mode);
void pso_print_usage(const char *program, int mpi_mode, int island_mode);

#endif /* PSO_CLI_H */
