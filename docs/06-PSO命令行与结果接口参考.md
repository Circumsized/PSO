# PSO 命令行与结果接口参考

## 文档信息

| 项目 | 内容 |
|---|---|
| 文档编号 | DOC-06 |
| 文档名称 | PSO 命令行与结果接口参考 |
| 适用对象 | 求解器使用者、实验脚本维护者、结果解析器维护者 |
| 结果协议 | `pso_v1` |

本文适用于三个求解器入口。命令从仓库根目录执行；示例 shell 为 Bash，Windows 使用 Git Bash。`<名称>` 表示需要替换的语法占位符，不能原样执行。

参数依据为 [pso_cli.c](../src/pso_cli.c:73) 和 [默认配置](../include/pso_config.h:9)。结果依据为 [pso_report.c](../src/pso_report.c:6)。

## 1. 语法

### 1.1 串行入口

```text
pso_serial [options]
```

### 1.2 MPI 数据并行入口

```text
mpiexec -n <ranks> pso_mpi_ms [options]
```

### 1.3 MPI 岛模型入口

```text
mpiexec -n <ranks> pso_mpi_island [options]
```

Windows 用户可使用 `pso_serial.exe`、`pso_mpi_ms.exe` 和 `pso_mpi_island.exe`。

MPI 启动器的进程数由 `mpiexec -n` 设置。不要使用 `--ranks` 作为求解器参数。求解器没有 `--ranks` 选项。

## 2. 说明

程序读取命令行参数，初始化粒子群，执行目标函数评估，并输出一行 `pso_v1` 结果记录。

程序默认：

- 使用 `rastrigin`；
- 使用 20000 个粒子；
- 使用 50 个维度；
- 使用 1000000 次最大评估；
- 使用 seed 20240229；
- 使用 `reflect` 边界；
- 使用收缩因子动力学；
- 启用达到 target 后提前停止；
- 不输出进度信息。

使用 `--help` 查看当前程序的帮助文本。本文参数表与源代码共同构成接口说明；如果二者发生差异，以当前 `--help` 和测试契约为准，并更新本文。

## 3. 输入参数

### 3.1 问题参数

| 参数 | 类型 | 默认值 | 约束 | 说明 |
|---|---:|---:|---|---|
| `--function NAME` | 字符串 | `rastrigin` | `sphere`、`rosenbrock`、`rastrigin`、`griewank`、`ackley` | 选择目标函数。 |
| `--particles N` | 正整数 | `20000` | `N > 0` | 串行总粒子数；MPI 总粒子数。 |
| `--dim D` | 正整数 | `50` | `D > 0` | 搜索维度。 |
| `--lower X` | 有限浮点数 | 随函数 | `lower < upper` | 覆盖搜索下界。 |
| `--upper X` | 有限浮点数 | 随函数 | `upper > lower` | 覆盖搜索上界。 |
| `--target X` | 有限浮点数 | `1e-8` | `X >= 0` | 成功阈值。 |

内置函数的默认搜索区间如下：

| 函数 | 下界 | 上界 |
|---|---:|---:|
| `sphere` | `-100` | `100` |
| `rosenbrock` | `-100` | `100` |
| `rastrigin` | `-5.12` | `5.12` |
| `griewank` | `-600` | `600` |
| `ackley` | `-32.768` | `32.768` |

如果命令行同时指定 `--function` 和边界覆盖值，程序使用显式边界值。请在 `--function` 之后指定 `--lower` 和 `--upper`，以便命令保持易读。

### 3.2 预算参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---:|---:|---|
| `--max-evals B` | 无符号整数 | `1000000` | 总目标函数评估上限。 |
| `--max-iters K` | 正整数 | `1000` | 仅在 `--max-evals 0` 时使用。 |
| `--no-early-stop` | 开关 | 关闭 | 达到 target 后继续执行到预算或迭代上限。 |

当 `B > 0` 时必须满足 `B >= N`。程序先评估初始粒子群，再计算更新代数上限：

```text
K = floor((B - N) / N)
```

`N` 是粒子总数。实际 `iterations` 可能因达到 target 而小于 `K`。关闭提前停止时，`evals = N * (K + 1)`；预算余数不足一代时不再评估。

例如 `N=8,B=33` 时，最多完成三代更新，实际 `evals=32`。若未命中 target，`fes_to_target=33` 是配置预算的失败标记，不是实际执行了 33 次评估。

`B=N` 时只初始化，报告 `iterations=0`。`B=0` 时使用 `max_iters`；`max_iters` 即使被预算覆盖也必须为正整数。预算换算的更新代数不能超过当前平台的 `INT_MAX`。

首次命中按初始种群或完整一代结束时的评估数记录，不追溯到代内某个粒子的先后顺序。`--no-early-stop` 保留首次命中值，继续完成配置上限；不保证非整倍数预算被耗尽。

### 3.3 随机参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---:|---:|---|
| `--seed S` | 无符号整数 | `20240229` | 基础随机种子。负号被拒绝。 |

完整算法配置和数值环境一致、目标函数值有限时，串行与数据并行共享全局粒子随机流。不同编译器或数学库的结果应按声明容差比较。岛模型使用另一随机命名空间；它不承诺与串行等价。详细前提见 [算法说明第 5 节](05-PSO算法与优化理论.md#5-随机流与等价性)。

### 3.4 动力学参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---:|---:|---|
| `--chi X` | 有限浮点数 | `0.72984` | 收缩因子模式的速度系数。必须大于 0。 |
| `--c1 X` | 有限浮点数 | `1.49618` | 个体认知系数。必须不小于 0。 |
| `--c2 X` | 有限浮点数 | `1.49618` | 社会认知系数。必须不小于 0。 |
| `--linear-inertia` | 开关 | 关闭 | 使用 `inertia_start` 到 `inertia_end` 的线性惯性模式。 |

`--linear-inertia` 使用默认 `w=0.90` 到 `w=0.40`，保留配置的 `c1` 和 `c2`。该模式不使用 `chi` 参与更新，但仍检查其合法性并输出该字段。当前 CLI 不支持 `--inertia-start`、`--inertia-end` 或 `--vmax-ratio`。

整数参数 `particles`、`dim` 和周期使用 C `int` 的范围。`seed` 与 `max-evals` 使用 64 位无符号整数。有限参数也可能组合出过大的区间或内存请求；配置被接受不等于任意规模都能运行。

### 3.5 边界和输出参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---:|---:|---|
| `--boundary MODE` | 枚举 | `reflect` | `reflect`、`clamp` 或 `reinit`。 |
| `--history PATH` | 路径 | 无 | 写 best-so-far sidecar。程序以写模式打开该路径。 |
| `--verbose` | 开关 | 关闭 | 输出进度信息。 |
| `--quiet` | 开关 | 默认行为 | 明确关闭进度信息；不隐藏结果行。 |
| `--report-interval K` | 正整数 | `10` | 每隔 K 代报告进度；仅 verbose 时生效。 |
| `--pin-cpus` | 开关 | 关闭 | 请求拓扑感知的进程 CPU 亲和性。 |
| `--help` 或 `-h` | 开关 | 无 | 打印帮助并正常退出，不产生结果行。 |

进度写入标准错误，结果行写入标准输出。重复设置 `--verbose` 或 `--quiet` 时，最后一个开关决定进度输出状态。

`--history` 的父目录必须已经存在。程序使用 `fopen(...,"w")` 打开路径，因此会截断同名文件。MPI 中只有 rank 0 写轨迹；内容是全局最好值，不是每岛各一份。

### 3.6 岛模型参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---:|---:|---|
| `--migration-interval K` | 正整数 | `10` | 每 `K` 代执行一次迁移。 |

当前版本固定迁移数量为 1。各 rank 向右邻居发送本岛最好解，接收左邻居的移民后，尝试替换自身最差 pbest。`ranks=1` 时不执行迁移。

三个入口均能解析该选项，但串行和数据并行不执行迁移，并在结果中写 `migration_interval=0`。使用这些入口时不要把改变该参数当作有效消融。

### 3.7 构建选项和环境变量

| 名称 | 默认值或来源 | 含义 |
|---|---|---|
| `PSO_ENABLE_MPI` | `ON` | 请求构建 MPI 目标；依赖未找到时会警告并跳过。 |
| `PSO_ENABLE_OPENMP` | `ON` | 请求启用 OpenMP；以配置阶段的发现结果为准。 |
| `PSO_ENABLE_SANITIZERS` | `OFF` | 非 MSVC 配置中请求 ASan/UBSan；运行库缺失时警告并禁用。 |
| `BUILD_TESTING` | CTest 默认 `ON` | 构建并注册测试；Python 用例还要求发现解释器。 |
| `CMAKE_BUILD_TYPE` | 建议显式指定 `Release` 或 `Debug` | 单配置生成器的构建类型。 |
| `OMP_NUM_THREADS` | OpenMP 运行库决定 | 每进程请求的线程数。 |
| `OMP_DYNAMIC` | OpenMP 运行库决定 | 示例设 `FALSE`，减少动态线程数调整。 |
| `MSMPI_INC` | MS-MPI SDK 安装环境 | Windows fallback 的头文件搜索路径。 |
| `MSMPI_LIB64` | MS-MPI SDK 安装环境 | Windows fallback 的 x64 库搜索路径。 |

`ctest -C Release` 选择测试配置，不会把 Debug 二进制重新编译为 Release。单配置生成器应在 CMake 配置时设置 `CMAKE_BUILD_TYPE`。

`pso_env_probe` 检查 MPI 线程等级和 OpenMP 环境。例如启用 MPI 后可运行 `mpiexec -n 2 ./build/pso_env_probe`。无 MPI 配置则直接运行 `./build-serial/pso_env_probe`。

## 4. 输出参数

### 4.1 pso_v1 结果行

程序成功运行时输出一行以以下前缀开头的结果记录：

```text
record=result schema=pso_v1
```

记录由空格分隔的 `name=value` 字段组成，以换行结束。下表按 C 输出顺序列出完整字段。解析器应按字段名读取。

| 字段 | 类型或固定值 | 定义 |
|---|---|---|
| `record` | `result` | 记录类别。 |
| `schema` | `pso_v1` | 协议版本。 |
| `implementation` | 字符串 | `serial`、`mpi-data-parallel` 或 `mpi-island`。 |
| `function` | 字符串 | 目标函数名。 |
| `particles` | 正整数 | 所有 rank 的粒子总数。 |
| `dim` | 正整数 | 维度。 |
| `max_evals` | uint64 | 配置的评估预算；0 表示使用迭代上限。 |
| `max_iters` | 正整数 | 配置的迭代上限；有评估预算时仍输出该字段。 |
| `seed` | uint64 | 基础随机种子。 |
| `boundary` | 枚举 | `reflect`、`clamp` 或 `reinit`。 |
| `dynamics` | 枚举 | `constriction` 或 `linear-inertia`。 |
| `target` | double | 数值目标阈值。 |
| `lower` | double | 各维搜索下界。 |
| `upper` | double | 各维搜索上界。 |
| `chi` | double | 收缩模式的旧速度系数。 |
| `c1` | double | 有效个体系数。 |
| `c2` | double | 有效社会系数。 |
| `vmax_ratio` | double | 起始速度上限与区间宽度的比，默认 `0.20`。 |
| `inertia_start` | double | 起始惯性权重，默认 `0.90`。 |
| `inertia_end` | double | 末尾惯性权重，默认 `0.40`。 |
| `ranks` | 正整数 | MPI 进程数；串行为 1。 |
| `threads` | 正整数 | `omp_get_max_threads()` 返回值；未启用 OpenMP 时为 1。MPI 中取 rank 0 的报告值。 |
| `migration_interval` | 整数 | 岛模型的配置周期；串行和数据并行为 0。 |
| `iterations` | 非负整数 | 初始化后实际完成的更新代数。 |
| `evals` | uint64 | 实际评估数，包含初始化。 |
| `best` | double | 已访问位置中的最小适应度；有效分析记录要求有限。 |
| `success` | 0/1 | best 是否小于或等于 target。 |
| `fes_to_target` | uint64 | 命中时记录首次命中的整代评估数；未命中且预算大于 0 时记配置预算，否则记实际 evals。 |
| `stop_reason` | 枚举 | `target`、`evaluation_budget` 或 `iteration_limit`。 |
| `stop_on_target` | 0/1 | 是否启用目标提前停止。 |
| `timing_scope` | `solver` | 初始化评估后起表的 solver 区间。 |
| `elapsed_max_s` | double，秒 | 单进程为本进程耗时；MPI 为所有 rank 的区间耗时最大值。 |

除时间外，double 字段以 `%.17g` 输出；时间以 `%.9f` 输出。二进制舍入可能使默认 `0.20` 显示为 `0.20000000000000001`。不要把数值等价与字符串相等混为一谈。

没有参与当前动力学的参数仍会出现在结果行中。`threads` 不是对每次并行区实际活跃线程数的测量。为减少动态调整，示例使用 `OMP_DYNAMIC=FALSE`。


### 4.2 history sidecar

启用 `--history PATH` 后，sidecar 使用以下格式：

```text
iteration=0 evals=<initial-evals> best=<finite-value>
iteration=1 evals=<next-evals> best=<finite-value>
```

要求如下：

1. `iteration` 从 0 连续增加。
2. `evals` 严格增加。
3. `best` 不增加。
4. 每一行的 `best` 必须是有限值。
5. sidecar 必须与对应日志和 seed 一一对应。

失败退出可能留下 0 字节或残缺的 sidecar：程序不删除用户提供的
`--history` 路径（该路径可能是设备、FIFO 或他人文件）。残缺文件不构成
有效证据——解析器按上述要求硬失败；同目录重跑会被实验 runner 的
sidecar 冲突预检拦截，请改用新的 `--output-dir`。

## 5. 示例

### 示例 1：验证预算

```bash
./build/pso_serial --function sphere --particles 10 --dim 2 --max-evals 40 --target 0 --seed 7 --no-early-stop
```

检查以下条件：

```text
iterations=3
 evals=40
 stop_reason=evaluation_budget
 stop_on_target=0
```

### 示例 2：只执行初始化

```bash
./build/pso_serial --function sphere --particles 16 --dim 3 --max-evals 16 --seed 7
```

检查：

```text
iterations=0
evals=16
```

### 示例 3：生成轨迹

```bash
./build/pso_serial --function sphere --particles 8 --dim 2 --max-evals 32 --seed 7 --no-early-stop --history build/sphere.history
```

程序同时输出结果行和 `build/sphere.history`。使用新路径保存文件。

### 示例 4：执行岛模型迁移

```bash
mpiexec -n 4 ./build/pso_mpi_island --function sphere --particles 40 --dim 2 --max-evals 200 --seed 7 --target 0 --no-early-stop --migration-interval 1
```

`--particles` 是所有 rank 的总粒子数。程序把总粒子数分到 rank。总粒子数必须不小于 rank 数。

### 示例 5：生成实验计划

```bash
python scripts/run_experiments.py --mode baseline --functions sphere,rastrigin --particles 2000 --dim 50 --max-evals 100000 --threads 1 --runs 30 --pin --history --output-dir build/doc-experiment-plan
```

该命令默认只生成 `manifest.json`。检查 manifest 后，再决定是否使用 `--execute`。

## 6. 错误和限制

### 6.1 参数错误

参数解析失败时，程序打印错误信息并返回非零退出码。常见原因如下：

- 粒子数或维度不为正数；
- `max-evals` 小于粒子数；
- 下界不小于上界；
- 浮点参数为 NaN 或无穷；
- seed 带负号；
- 迁移周期或报告周期不为正数。

### 6.2 MPI 启动错误

MPI 启动器必须与程序链接的 MPI 实现匹配。总粒子数必须不小于 rank 数。入口请求至少 `MPI_THREAD_FUNNELED`；支持等级不足时停止。

| 现象或诊断 | 操作 |
|---|---|
| `MPI targets will be skipped` | 安装 MPI 开发环境；Windows 同时检查 Runtime、SDK、x64 库及 `MSMPI_INC`、`MSMPI_LIB64`。重新配置并检查目标。 |
| 启动器找不到程序 | 检查程序路径；Windows 使用 `.exe`，路径有空格时加引号。 |
| `MPI_THREAD_FUNNELED` 不受支持 | 更换提供所需线程支持的 MPI 实现。 |
| rank 数大于粒子总数 | 减少 `-n` 或增加 `--particles`。 |
| `pin-cpus` 失败或平台不支持 | 核对可用 CPU、容器 cpuset 和并发度；不要将未绑定运行混入绑定计时组。 |
| 某 rank 失败或 CTest 超时 | 保存错误日志并排查通信或资源问题；不要将不完整输出纳入统计。 |

### 6.3 输出错误

结果行是完整结果契约的一部分。结果写入失败时，调用方应返回非零退出码。不要使用未写完整的日志进行统计。

初始种群没有任何有限适应度时，程序以非零退出并在标准错误打印 `initial swarm has no finite fitness`，不发布结果行；MPI 路径所有 rank 基于同一归约结果集体退出。

初始种群**部分**粒子适应度非有限时，运行继续（该策略见 `docs/05 §4`），但每个入口在标准错误打印计数提示：串行为 `warning: <n> of <N> particles have non-finite fitness (normalized to +inf)`，MPI 路径各 rank 报各自本岛/分区的计数（MS-MPI 多 rank stderr 可能逐字符交错）。该提示不进入结果行契约，且**只统计初始种群**——粒子在迭代过程中变非有限不重复提示（归一与 best 选择逻辑不受影响）。

### 6.4 性能解释限制

`elapsed_max_s` 不含初始化评估，但包含 solver 区间中的循环通信、history 写入和进度输出。正式计时应关闭进度输出，启用固定 CPU 亲和性，并使用至少 30 个 seed。

单次运行只能说明一次运行的结果。单次运行不能证明平均加速、扩展规律或统计显著性。

## 7. 相关文档

- 算法和公式：[05-PSO算法与优化理论.md](05-PSO算法与优化理论.md)。
- 架构和通信：[02-优化设计方案.md](02-优化设计方案.md)。
- 测量协议：[03-基准测试报告.md](03-基准测试报告.md)。
- 文献和写作约定：[07-参考文献与文档写作约定.md](07-参考文献与文档写作约定.md)。
