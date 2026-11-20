# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 约定。
所有显著变更记录于此；性能数字仅在 `docs/03-基准测试报告.md` 声明的协议下采集。

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
- 已登记延后项：MPI 余数预算钉子与 MPI 写失败回归（需 Linux CI 绿灯），
  见 `docs/03` 的复验记录。
- 2026-11-20 GitHub Actions 四作业（Windows MS-MPI / Linux Debug+ASan+UBSan /
  Linux Release / Python）首次全绿（run 44653870418）：MS-MPI 资产钉定 v10.1.1、
  OpenMPI 4-rank 测试加 `--oversubscribe`、manifest 主机探测在 runner 上降级不中断。
