# AGENTS.md

C11 particle-swarm optimizer with three entrypoints (serial, MPI data-parallel, and ring island), a shared core, and reproducible benchmark tooling. Project status: feature-complete; current work is review-driven quality fixes that must **not** change measured numbers.

All repo prose — README, CHANGELOG, `docs/`, and code comments — is Simplified Chinese. Write new docs, commit messages, and comments in Chinese to match.

## Layout

- `include/` + `src/`: shared core (`pso_core`, `pso_bench`, `pso_config`, `pso_cli`, `pso_pin`, `pso_report`, `pso_rng.h`) plus three entrypoints: `main_serial.c` (serial), `main_mpi_ms.c` (MPI data-parallel, kept as comparison target), `main_mpi_island.c` (ring island model — the primary scheme).
- `tests/`: C unit and smoke tests. `scripts/`: Python tooling. `scripts/tests/`: Python unit tests (stdlib `unittest` only, no third-party deps; CI runs Python 3.12).
- `docs/02-优化设计方案.md` — architecture invariants; `docs/03-基准测试报告.md` — measurement protocol and provenance. Read the relevant one before touching the matching area.
- `results/formal-*/` — tracked measured evidence (manifests, logs, convergence sidecars). Never delete or overwrite these; only raw smoke logs under `results/` are gitignored.
- `CHANGELOG.md` follows Keep a Changelog (zh-CN), update it for significant changes.

## Build & test

Dev machine: Windows, MinGW GCC, MS-MPI, Git Bash shell. Linux second environment via Docker.

```bash
# Windows (CTest needs -C Release)
cmake -S . -B build -G "MinGW Makefiles" -DPSO_ENABLE_MPI=ON -DPSO_ENABLE_OPENMP=ON
cmake --build build --parallel
ctest --test-dir build --output-on-failure -C Release

# Linux
cmake -S . -B build -DPSO_ENABLE_MPI=ON -DPSO_ENABLE_OPENMP=ON && cmake --build build --parallel
ctest --test-dir build --output-on-failure

# Python tooling (188 tests, full suite ~8 s; also registered in CTest and ci.yml)
python -m unittest discover -s scripts/tests -p 'test_*.py' -v
```

- Options: `PSO_ENABLE_SANITIZERS=ON` (Debug, non-MSVC; auto-disables when libasan/libubsan are missing). Linux sanitizer/Release reproduction: `scripts/linux_docker_check.sh` with `Dockerfile.linux`.
- Several pre-existing build trees exist (`build/`, `build-warn/`, `build-sanitize/`, …); all gitignored. Note `scripts/tests/test_io_failures.py` resolves binaries from `PSO_TEST_BUILD_DIR` when CTest injects it (the current build tree wins and a missing binary fails), otherwise it falls back to searching `build-warn/` then `build/` — build in either before relying on it directly. The two MPI targets (`pso_mpi_ms`/`pso_mpi_island`) are treated as optional binaries via `optional_built`: when `PSO_TEST_BUILD_DIR` is injected but they are missing, the corresponding cases skip — a deliberate exemption, in contrast to `built()`'s must-fail semantics.

## Architecture invariants (do not break)

1. **Reproducibility:** RNG is xoshiro256++ + splitmix64, with streams bound to *global particle indices*. A fixed `(function, dim, particles, max-evals, seed)` must give bit-identical results regardless of MPI rank count or OpenMP thread scheduling. Never seed from `time()` or rank/thread identity. By-design exemption (see `docs/02` §3.3): `pso_mpi_island` derives per-island namespaces (`0x100000000 + rank`) with local indices, so island results intentionally vary with rank count; the rank-count invariance claim covers serial and `pso_mpi_ms`.
2. **Uniformity across entrypoints:** serial, `pso_mpi_ms`, and `pso_mpi_island` share the same core, CLI, functions, parameters, evaluation-budget accounting, and stop criteria. Changes must apply to all three; no collective call may be skipped or left unchecked (`mpi_check`).
3. **`pso_v1` output contract:** each run prints one `record=result schema=pso_v1 implementation=...` line plus an optional best-so-far sidecar. The field set and formatting are pinned by Python parsers (`scripts/plot_results.py`, guards in `scripts/tests/`) and by CTest `PASS_REGULAR_EXPRESSION`s — changing either side requires updating both.
4. **Timing scope:** measured `elapsed_max_s` excludes swarm init (`timing_scope=solver`); keep any new entrypoint consistent with `docs/03` §采集条件.

## Benchmark & measurement gotchas

- Performance claims are only citable from `scripts/run_experiments.py` under the `docs/03` protocol (fixed hardware, `--pin-cpus`, ≥30 seeds). It is **dry-run by default**; add `--execute` to actually run. `--append` and `--history` are mutually exclusive (appending breaks sidecar pairing). `run_benchmark.sh` is a quick smoke path only — its timings are never publishable.
- Quality-only rounds must not re-collect or silently alter numbers; per the current CHANGELOG round, timing/speedup figures stay frozen. Statistical comparisons use paired Wilcoxon signed-rank (`scripts/analyze_stats.py`) with Mann-Whitney U as a control.

## Code conventions

- C11 with `CMAKE_C_EXTENSIONS OFF` (strict `-std=c11`) and project-wide `-Wall -Wextra -Wpedantic` (`/W4` on MSVC); the tree is expected to stay zero-warning. Because extensions are off, POSIX calls need feature-test macros (e.g. `_GNU_SOURCE` for `clock_gettime`) — this has bitten before.
- Windows/MinGW specifics are isolated behind the `pso_mpi` INTERFACE target in `CMakeLists.txt` (no `mpicc` wrapper with MS-MPI; `mpi.h` is included as SYSTEM because `-Wpedantic` trips on it). Keep FindMPI handling inside that target.
- Parser scripts hard-fail on budget inconsistencies and log-name drift rather than warning; preserve that behavior in new tooling.

## 修复与复审流程

1. 修复按批次组织，每批次独立提交，CHANGELOG 同批更新；批次间文件有交集时串行，无交集可并行派发。
2. 测试计数（CTest/Python）任何变动必须同批级联：AGENTS.md 计数句、CHANGELOG 计数条目，改完 grep 全仓核对（级联断裂曾把 188 写成 186）。
3. 历史溯源声明只修归属错误，不改写历史事实；引用 CI run 号前先 `gh run view` 核实日志中的实际项数。
4. 外部事实主张（工具链行为、文献结论）必须附官方来源并登记到 docs/07，含「用途」与「限制」两栏；查不到就写缺口，不用二手来源充当官方依据。
5. 独立复审换人执行（发现者不复核）；每条将作为事实行动的发现先经独立复核或确定性检查证实，未证实的标注「未复核」。
6. 红线核查每次必做：`results/formal-*` 只读（`git diff` 该路径为空）、求解轨迹不变（金标全绿）、不引入新依赖。
7. 门禁：全量重建零警告 + `ctest -C Release` 全绿 + Python 全套件 + 可解析的 formal manifest root 只读实跑（`plot_results --manifest-root`，`--output` 到临时目录；可解析的为 v3/v4/v5-pinned 三个，必须全部通过；另三个——formal-20260911 与 formal-plan-20260911-v2 为 dry-run 壳、formal-20260912-linux 为历史相对路径 manifest——改动前即不可解析，其拒绝行为不得退化）。
