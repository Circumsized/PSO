# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 约定。
所有显著变更记录于此；性能数字仅在 `docs/03-基准测试报告.md` 声明的协议下采集。

## [未发布]

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
- `plot_results.py --manifest-root` 严格路径补两类硬失败：结果行缺
  pso_v1 钉住字段（CONFIG_FIELDS 任一缺失，此前静默产生空单元格并独立
  成组）；`max_evals`/`evals` 与绑定命令 `--max-evals` 的预算一致性
  （固定预算须结束于完整一代边界，`stop_on_target=1` 的合法早停放行）。
  Python 178→182。六个 formal manifest root 只读实跑回归：可解析的
  v3/v4/v5-pinned 全部通过、无误伤（formal-20260911 与
  formal-plan-20260911-v2 为 dry-run 壳、formal-20260912-linux 为历史
  相对路径 manifest，改动前即不可解析）。

## [1.0.0] - 2026-11-19

### 首次公开发布

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
- 2026-11-20 GitHub Actions 四作业（Windows MS-MPI / Linux Debug+ASan+UBSan /
  Linux Release / Python）首次全绿（run 44653870418，当时套件 45 项；
  CTest 45→47 后的首次全绿为 run 44655986835，提交 e4f267f）：MS-MPI 资产钉定
  v10.1.1、OpenMPI 4-rank 测试加 `--oversubscribe`、manifest 主机探测在
  runner 上降级不中断。
