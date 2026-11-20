# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 约定。
所有显著变更记录于此；性能数字仅在 `docs/03-基准测试报告.md` 声明的协议下采集。

## [未发布]

### Added

- 新增 [docs/01-用户指南.md](docs/01-用户指南.md)：MATLAB 文档体系式的
  专业技术文档（User's Guide 定位）——快速开始（构建、首个优化并逐字段读懂
  `pso_v1` 输出、history sidecar、两个 MPI 入口的真实运行与关键观察）、PSO
  基本概念与术语表、入口选型决策表、常用参数速览与选择三原则、基准函数表、
  六个常见任务操作步骤、故障诊断表、分阶段深入学习路径。示例输出均为开发机
  真实运行（单次运行，仅说明格式；可引用的性能数字以 docs/03 协议为准）。
  参数与字段的完整定义仍以 docs/06 为单一真相，本文不重复全表。

### Fixed

- 文档与实现一致性修正（全量审阅确认项）：`docs/02`/`docs/05` 中收敛汇总
  「忽略/跳过未登记 history」的表述改为实际的硬失败行为；`docs/03` 补披露
  manifest 未记录 CPU 型号/编译器/OpenMP 版本、且 MS-MPI 的 mpiexec 不支持
  `--version`（探测失败 version=null 已留档）；47 项 CTest 首次全绿的归属
  由 run 44653870418/d91c4fc（该 run 实际执行 45 项）更正为
  run 44655986835/e4f267f；冒烟溯源句改指 formal-* 归档；删除不存在的
  「保留最后 30 对」去重机制描述；强扩展表补 sphere t2 两行（复算自
  formal-20260912-v5-pinned）；`docs/05` §3.3 REINIT 速度按实际的 V_0
  初始尺度表述；`docs/02` 补「无停滞检测/随机重启为有意设计」声明；
  AGENTS.md 补记两个 MPI 可选二进制在 PSO_TEST_BUILD_DIR 注入时缺失即
  skip 的豁免。
- 采集工具绑核对称性修复（全量审阅确认项 #5）：`run_experiments.py`
  的 `experiment_command` 按 mpiexec 版本串判别 MPI 种类——OpenMPI 系
  （`Open MPI|OpenRTE`）追加 `--oversubscribe`，且 `pin=False` 时追加
  `--bind-to none`（恢复与 MS-MPI `MPIEXEC_AFFINITY=0` 同构的「不绑核」
  语义，OpenMPI 自 1.8 起默认绑核曾使 Linux pin=false 对照静默失真）；
  MS-MPI/未知版本维持现状。版本探测结果一次性回填，不重复调用。
  `_platform_text` docstring 机理描述修正。Python +2（OpenMPI/MS-MPI
  分支用例）。
- `plot_results.py --manifest-root` 严格路径补两类硬失败：结果行缺
  pso_v1 钉住字段（CONFIG_FIELDS 任一缺失，此前静默产生空单元格并独立
  成组）；`max_evals`/`evals` 与绑定命令 `--max-evals` 的预算一致性
  （固定预算须结束于完整一代边界，`stop_on_target=1` 的合法早停放行）。
  Python 178→182。六个 formal manifest root 只读实跑回归：可解析的
  v3/v4/v5-pinned 全部通过、无误伤（formal-20260911 与
  formal-plan-20260911-v2 为 dry-run 壳、formal-20260912-linux 为历史
  相对路径 manifest，改动前即不可解析）。

### Changed

- 代码卫生微批（全量审阅低优先级项，行为不变或仅改诊断）：删除
  serial/mpi-ms 入口的 `fes_to_target` 死预赋值；`pso_rotl` 改移位计数
  掩码形式消除潜在 UB；`pso_wall_time` 去掉 `QueryPerformanceFrequency`
  懒初始化缓存；pin 核表枚举上界改 `8*sizeof(ULONG_PTR)`；两个 MPI 入口
  将 `MPI_Comm_rank` 提前使 `mpi_check` 诊断带真实 rank；MSVC 下请求
  sanitizer 补 WARNING；docker 复现脚本 ctest 加 `--timeout 120` 并断言
  Python 解释器（Dockerfile 改用 python3）；`ctest_gate.py`/
  `check_rank1_equivalence.py` 子进程加 120s 超时；`run_benchmark.sh` 加
  可执行文件存在性检查；`analyze_speedups.py` mode 守卫收紧为 `all`。
- 测试加固：新增 `serial_help_mode` CTest（47→48 项）；`inject_migrant`
  拒绝路径直断言、Ackley 非零探针与 `from_name(NULL)` 分支、CLI 负例、
  ctest_gate 退出码归一化与超时用例、analyze_speedups mode 拒绝用例。
  Python 184→188。金标正则与 MPI 钉子逐位不变（全量重建零警告，
  CTest 48/48、Python 188 全绿）。
- RNG 单元金标与出处归档（联网取证轮）：`tests/test_rng.c` 增加
  xoshiro256++ 位流金标准（`pso_rng_next`/`double`/`range`/`below`
  固定输出、流派生差异值、`n<=1` 边界），把不变量 #1 的地基从端到端
  金标间接钉住升级为单元直钉；`pso_rng_below` 注释按实际算法精确
  命名（经典无偏取模，非 Lemire Algorithm 5 乘积高位变体）；
  `pso_rng.h` 归档流派生仿射派生偏离主流做法的已知限制（NumPy
  并行 RNG 文档将 `root_seed+worker_id` 式派生列为 UNSAFE；因位级
  复现红线不修改，留待主版本）。
- 外部依据登记：docs/07 新增 [R9]–[R13]（OpenMPI man page v4.1、
  MS-MPI `MPIEXEC_AFFINITY` 源码与文档、Lemire 无偏区间整数论文、
  NumPy 并行 RNG 文档、SPSO-2011/CEC-2013 基准套件），每条带用途
  与限制；docs/03 绑核段补官方依据引用；AGENTS.md 新增「修复与
  复审流程」节（批次化、计数级联、外部来源登记、独立复审、红线
  核查、门禁七条）。
- 发布面整理：CITATION.cff 发布日期与 CHANGELOG [1.0.0] 对齐
  （2026-11-19）；CHANGELOG 1.0.0 分节改为标准 Added 结构、公开发布后记
  明确标注日期、补版本对比链接；LICENSE 恢复为标准 MIT 正文（Scope notes
  迁至 NOTICE.md，GitHub 许可证探测由 NOASSERTION 恢复为 MIT，notes 内容
  未丢失）；README 增加 CI 状态徽章；CI 的 `actions/checkout@v4→v7`、
  `actions/setup-python@v5→v7`（已用 gh api 核实两者当前主版本均为 v7）。
  已推送并发版：main 推送 8 笔（CI run 44708960129 四作业全绿，实证
  actions v7 生效）；v1.0.0 annotated tag 指向 e4f267f（CHANGELOG [1.0.0]
  所述状态）并创建 GitHub release。本批次 8 笔修复列于本节 [未发布]，
  将于下一版本（1.0.1）发布。备注：提交 2616d0e 的消息尾注「推送与 tag
  待确认」为提交时状态，公开历史不可改，以本条为准。
- 文档体系学习者化：README 文档导航表重构为学习者视角（按阅读顺序排列，
  新增读者列，用户指南置顶）；docs/02、docs/03、docs/05 标题下补读者标注
  横幅，明确各自受众与进入路径。修正此前导航表全部面向维护者提问
  （「模块如何分工」「架构约束」）而无学习者入口（「我想快速上手」）的缺陷。
- 文档体系符合开源实践标准：README 导航表改按文档类型（Diátaxis：教程/
  操作指南/参考/解读）分组并附各类定义，替换原「读者」分组；新增四张图——
  README 架构总览（Mermaid）、docs/01 PSO 主循环流程图（Mermaid）与真实
  收敛曲线（`docs/figures/convergence-demo.png`，来自 §1.3 命令的开发机
  单次运行，仅说明格式）、docs/02 §3.3 环岛迁移拓扑图（Mermaid）。

## [1.0.0] - 2026-11-19

### Added

- 串行、MPI 数据并行与环形岛模型三个 C11 求解器入口，共享核心、命令行、
  评估预算换算与 `pso_v1` 结果协议；OpenMP 混合并行可选。
- xoshiro256++ 粒子级随机流按全局粒子索引派生：固定 `(函数, 维度, 粒子数,
  预算, seed)` 跨 rank 数与线程调度逐位可复现；岛模型按命名空间豁免。
- 实验工具链：`run_experiments.py` 固定预算矩阵与 manifest（默认 dry-run、
  拒绝覆盖已执行证据、sidecar 冲突预检）；速度比、收敛/ERT、配对统计
  （Wilcoxon signed-rank 为主，Mann-Whitney U 对照）与逐位对比工具。
- 质量门禁：CTest 45 项（数值金标准、MPI 迁移金标、退出码包装判据、
  sanitizer 硬失败、OpenMP 生效断言）与 Python 175 项单测；CI 覆盖
  Linux/Windows/Python 三作业。
- 第三轮审计遗留项的收尾：`mpi_data_failure_fes_budget` /
  `mpi_island_failure_fes_budget` 补齐非整数倍预算下的失败轮口径钉子
  （`evals=32 fes_to_target=33`）；`test_io_failures` 增加 MPI 两入口的
  `/dev/full` 写失败哨兵与跨平台"只读 history 目标打开失败不被删除"用例，
  源码哨兵扩展到 `unlink(`/`DeleteFileA(`/`DeleteFileW(`（Linux 侧由 CI 实跑）。
  CTest 45→47，Python 175→178。
- 公开发布后记（2026-11-20）：GitHub Actions 四作业（Windows MS-MPI /
  Linux Debug+ASan+UBSan / Linux Release / Python）首次全绿
  （run 44653870418，当时套件 45 项；CTest 45→47 后的首次全绿为
  run 44655986835，提交 e4f267f）：MS-MPI 资产钉定 v10.1.1、OpenMPI
  4-rank 测试加 `--oversubscribe`、manifest 主机探测在 runner 上降级不中断。

[1.0.0]: https://github.com/Circumsized/PSO/releases/tag/v1.0.0
