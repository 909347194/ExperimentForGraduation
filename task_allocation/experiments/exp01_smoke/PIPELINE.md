# exp01_smoke：从实验配置到结果绘制的全链路记录

本文回答两个问题：**一次实验的每个数字/每张图是从哪来的**（可追踪、可复现），
以及**当前方法与实验设计还有哪些硬伤**（严格审查结论）。

---

## 1. 全链路（五个阶段，单向、无环路）

| 阶段 | 入口 | 职责 | 关键产物 |
|---|---|---|---|
| ① 配置 | `config.yaml` | 唯一参数源；`methods/` 不读配置、不写死数值 | `ConfigBundle` |
| ② 场景装配 | `build.py` | 机巢 CSV + DEM → Nest / LocalFrame / DEMTerrain；机队、任务批次 | `scenario` / `terrain` / `uavs` / `batch` |
| ③ 滚动闭环 | `run.py` → `RollingHorizon` | 逐 seed 跑 `TaskPool → selection → MUAS(DMDE) → 完成反馈` | `CycleRecord[]` |
| ④ 指标汇总 | `metrics.py` | `build_metrics` / `build_solution` / `build_run_meta` / `build_seed_summary` | `results/*.json` |
| ⑤ 出图 | `plot/` | **只读 json**、只绘制，不含任何计算 | `results/figures/*.png` |

阶段 ③ 对 `experiment.seeds` 中每个 seed 独立重建机队与任务批次
（`_run_once`），场景/地形/代价估算器跨 seed 复用；求解器种子同步设为该 seed，
保证任务生成与优化两端都随 seed 变化、且同配置可复现。

`metrics.json` 的 `cycles` 只装**代表 seed（首个）**，`seed_summary` 才是 5 个
seed 的 mean±std——因此 8/11 张图反映的是代表 seed，只有 `outcome_summary.png` /
`seed_summary.png` / `ab_compare.png` 覆盖全部 seed（引用图时须注明）。

---

## 2. 追踪表：配置段 → 消费代码 → 产物字段 → 用到的图

| 配置段 | 被谁读取 | 落到哪个字段 | 哪张图用它 |
|---|---|---|---|
| `problem`（机巢数/容量/机队规模/速度/航程） | `build.build_scenario` / `build_uavs` | `metrics.problem`、`nests`、`fleet` | `geo_overview`、`fleet_state` |
| `tasks`（规模/散布/收益/时间窗 `deadline_cycles_*`） | `build_task_config` → `build_arrivals` | `pool_counts.n_new`、`tasks[].latest`、`tasks[].arrival_time` | `task_dynamics` |
| `selection`（α、五权重、边际收益） | `RollingHorizon` → `selection.run` | `cycles[].selection.*`（`n_after_prefilter`/`n_after_marginal`/`n_selected`） | `task_selection` |
| `muas`（λ、`w_penalty`、`w_violation`、剔除、终点进编码、Lamarckian） | `run_selective_muas` | `cycles[].muas.*`（`f1_reward`、`f2_cost`、`feasible`、`penalty`、`coded_end_nests`、`n_evals`） | `allocation`、`task_dynamics` |
| `constraints`（11 开关 + 9 权重） | `MUASProblemConfig.constraints` → `check_feasible`；关闭 `time_window` 时同步关第一阶段预筛 | `cycles[].muas.violations`、`feasible` | `constraint_state` |
| `solver`（种群/代数/ζ/δ） | `DMDEConfig` | `muas.n_evals`、`solution.cycles[].cost_history` | `dmde_search` |
| `cost`（DEM 步长、离地余量、爬升率） | `build_terrain` / `build_cost_estimator` | `run_meta.dem_terrain`、`solution.dem` | `geo_overview`（高程底图） |
| `rolling`（周期数/周期长/`complete_after_cycle`/`completion_feasibility_gate`/`recharge_at_nest`/`faults`/`strict_capacity`/`event_replan`） | `HorizonConfig`、`EventTriggerConfig` | `cycles[].completed_ids`、`executed_ids`、`deferred_ids`、`expired_ids`、`infeasible_ids`、`released_by_fault`、`replan` | `task_dynamics`、`allocation` |
| `plot`（formats/dpi） | `run.main` → `plot.make_plots` → `plot.registry` | — | 全部 11 张 |

### 2.1 图的分层与查找

图的唯一事实来源是 `plot/registry.py`（`FIGURES`）——**新增一张图 = 在表里加一行**。
图按「**看图目的**」分四层（论文叙事线），而不是按实现技术：

| 层 | 回答什么问题 | 特性 |
|---|---|---|
| `setup` | 实验设了什么？（问题规模 / 算法超参 / 约束开关） | 参数即图，全部过程图的口径基准 |
| `initial` | 实验开始前世界长什么样？（地形 / 机巢 / 初始机队 / 首批任务） | 一次性静态，**与求解无关** |
| `process` | 这一轮怎么跑的？（滚动闭环的过程观测） | 随周期演化，看的是单次运行 |
| `report` | 实验说明什么？（可直接进论文的结论图） | 常跨多 seed / 多组配置汇总 |

命名约定（**四者同名**，见 `registry.py` 顶部）：

```
模块名 ≡ 图 id ≡ 输出文件名 ≡ 绘制函数名去掉 plot_
plot/initial/geo_overview.py  →  plot_geo_overview  →  geo_overview.png
```

因此产出图文件时不再有「模块名 ≠ 图名」这类需要猜的错位（如旧 `cycles.py` 误产出 `cycle_metrics.png`）。
层内另有 `topic` 二级标签（mechanism / allocation / spatial / solver / constraint /
fleet / robustness / comparison / config）用于再归类，但**不建目录**——避免层级深过头。

当前 11 张图（此表由 `python -c "from plot import registry; print(registry.describe())"`
生成，改动后请重跑覆盖）：

| 层 | 图 id | 说明 | 数据来源 |
|---|---|---|---|
| setup | `setup_params` | 实验设定总览：问题规模 / DMDE 超参 / 选择机制 / 约束开关 / 滚动设定 | `config.yaml 全段 + solution.json 的 nests / tasks / uavs` |
| initial | `geo_overview` | 初始态势：地形 · 机巢 · 初始机队 · 任务散布 | `solution.json 的 dem / nests / tasks / uavs` |
| process | `task_selection` | 动态任务选择：漏斗逐级收敛 / 保留率 / 能力上限 / 池守恒 | `metrics.json 的 cycles[].selection 全字段 / muas.n_selected` |
| process | `dmde_search` | DMDE 搜索机制：动态交叉率 CR（公式 3-9）/ 探索—开发平衡 / 收敛曲线 / 求解开销 | `solution.json 的 cycles[].cost_history（长度−1 即 S）+ config.yaml 的 solver.zeta` |
| process | `allocation` | 单周期分配结构：UAV—任务甘特 / 空间分配图 / 逐周期 DEM 演化 | `solution.json 的 cycles[].tours + tasks[] / nests[] / dem` |
| process | `task_dynamics` | 任务命运：池水位 / 流量 / 状态矩阵 / 等待与滞留 / 地理生命周期 | `metrics.json 的 cycles[].pool_counts / completed_ids / deferred_ids + solution.json 的 tasks[]` |
| process | `fleet_state` | 机队与机巢：三口径出动 / 占用热力图 / 选巢归因 / 机队迁移 | `metrics.json 的 cycles[].muas / nest_occupancy + solution.json 的 tours / nests` |
| process | `constraint_state` | 约束与时限：预筛归因 / 约束状态 / 罚项量纲 / 时间窗满足度 | `metrics.json 的 cycles[].selection.filter_reasons / muas.{violations,penalty,infeasible_tags} + tasks[].{earliest,latest,completed_at}` |
| report | `outcome_summary` | 结果总览：完成率 CR 轨迹 / 未完成任务终局构成 / 双目标 / 关键指标 | `metrics.json 的 cycles[].pool_counts / completed_ids / summary + per_seed` |
| report | `seed_summary` | 跨 seed 稳健性：三指标均值±σ / 分布箱线 / 逐 seed 演化 / 明细表 | `metrics.json 的 per_seed / seed_summary / seeds（仅多 seed 运行）` |
| report | `ab_compare` | 两组配置对照：完成率 / 收益—代价 / 权衡 / Δ% | `results/metrics_<tag>.json 的 per_seed / seed_summary（需 ≥2 组对照）` |
**改图不必重跑**：`plot.render_from_results(results_dir)` 直接读已落盘的
`metrics.json` + `solution.json` 重绘。只想出一部分图（例如凑论文插图）：

```bash
# 只出结论层（跨 seed 稳健性）
python -c "from plot import render_from_results as r; \
r('task_allocation/experiments/exp01_smoke/results', groups=['report'])"
# 或跑实验时限定：python run.py --group scene --figure geo_overview
```

配套分析脚本 `analyze.py`：给定 `metrics.json` 打印可行率、违约分类、闸门拦截量、
终点机巢分布与跨 seed 汇总，用于两组配置对照。

---

## 3. 本轮两组对照运行（5 seed × 3 周期 = 15 个周期解）

配置差异仅两项：A 组 `constraints.time_window=false`（`w_violation` 无惩罚可缩放）；
B 组 `time_window=true` + `muas.w_violation=0.01`。产物已归档为
`results/metrics_tw_off.json` 与 `results/metrics_tw_on.json`。

| 指标 | A：时间窗关闭 | B：时间窗开启 + 惩罚解耦 |
|---|---|---|
| 周期解可行率 | **15/15** | **12/15（0.80±0.16）** |
| 时间窗违约 | 0 | 4 次 / 4 个任务 |
| 完成率 | 0.852 ± 0.083 | 0.487 ± 0.050 |
| 总奖励 F1 | 95.61 ± 18.49 | 61.74 ± 17.27 |
| 总路径代价 | 313803 ± 95565 m | 129377 ± 52927 m |
| 平均等待 | 651.6 ± 28.9 s | 652.3 ± 44.8 s |
| 闸门拦截的违约交付 | 0 | 1.0 ± 0.89 个/次 |
| 终点机巢 | 8/8 全用，单周期多样性最高 8/9 | 8/8 全用，最高 6/8 |

读法：B 组完成率下降**不是**因为算法退化，而是时间窗真正生效后的权衡
（且已核实：18 个任务中**物理不可达 = 0**，最近机位中位距离 8.9 km ≈ 596 s，
窗口中位 1790 s，都能赶上）。完成率由"迟到也算完成"的 85% 回落到诚实的 49%。

---

## 4. 严格审查结论

### 方法侧

| # | 问题 | 严重度 | 建议 |
|---|---|---|---|
| M1 | **单一 `w_violation` 无法同时标定两类量纲**：机巢容量罚 1e4/单位、时间窗罚按秒计，共用一个系数时前者（×0.01→100）会彻底压过后者（1000 s→10） | 高 | 罚项按各自典型尺度归一化，或改为分类权重 `w_violation_window` / `w_violation_capacity` |
| M2 | **`metrics.f2_cost` 与适应度口径不一致**：F2 仍是「路径 + 惩罚」，而适应度已拆成 `λ·path + w_violation·penalty` | 中 | metrics 分开报 `path_cost` 与 `penalty` |
| M3 | **完成指标缺少构成分解**：未完成任务无法区分「闸门拦截 / 过期 / 仍在池」 | 中 | summary 补 `n_expired`、`n_infeasible_delivery`、`n_remaining_pool`（后两者已有） |
| M4 | `complete_after_cycle` 简化：周期末瞬移+标完成，不建模飞行过程 → `avg_wait≈652 s≈cycle_length` 实为周期量化产物 | 中 | 论文明确该语义，或改用估计到达时刻 |
| M5 | `consume_range(max(est_cost,0))` 在 `est_cost=inf` 时把剩余航程打成 `-inf` | 中 | 加 `np.isfinite` 保护 |
| M6 | 第一阶段可达性用欧氏距离、`_cost_hint` 把 `inf` 兜成 1e6，未用三维代价 | 中 | prefilter 接入 3D 代价 |
| M7 | `end_nest_reachability` 与 `range` 双重计罚（末段 `inf` 必然同时触发航程违约） | 低 | 论文说明冗余关系，或只保留其一 |
| M8 | 禁飞区未实现（`vertical_section` 只有最小离地余量） | 已知 TODO | 按用户决定挂起 |

### 实验侧（exp01）

| # | 问题 | 严重度 | 建议 |
|---|---|---|---|
| E1 | **关键约束在 exp01 里是惰性的**：24 架机 / 32 泊位 → 容量最大占用仅 4（=上限但未越界）；`recharge_at_nest` 每周期回满 + 120 km 航程 → 航程违约恒为 0；α=2.5×24=60 ≫ T_t(≤11) → Top-αK 预筛完全不生效 | 高 | 增设压力场景：`n_uavs=32`、关闭 `recharge_at_nest`、扩大 `spread_m`，让容量/航程/预筛真正咬合 |
| E2 | **无基线、无消融**：`experiments/` 只有 exp01/exp02，而 config 注释多处承诺 `event_replan` / `w_time` 用于 exp04 消融（`end_nest_in_encoding` 旋钮已随方案 B 移至 pro 分支） | 高 | 建 exp04：两个创新点各跑一组开关对照 |
| E3 | **参与机数被 SRP 编码限制为 `min(K_avail, M-1)`**：24 架可用却只有 8 架参与，机队规模效应无法体现 | 高 | 论文明确该建模约束；或补充 K≈M 的对比场景 |
| E4 | `w_violation=0.01` 是拍定的，未做灵敏度 | 中 | 跑 0 / 0.002 / 0.01 / 0.05，出「完成率—违约率」权衡曲线 |
| E5 | 3 个周期太短，滞留任务是否最终执行看不到稳态 | 中 | 主实验提到 8–10 周期 |
| E6 | 事件驱动（`replan`、`faults`、`excluded_nest_ids`）在 exp01 从未真正触发 | 中 | 开 `faults: {2:[3]}` + 机巢不可用，验证反应式重规划 |
| E7 | 统计口径：5 seed、`ddof=0` 标准差、无显著性检验；8/11 张图只画代表 seed | 中 | 补 boxplot/多样本，图注注明代表 seed |
| E8 | `F3`（`w_time`，负载均衡项）默认 0，论文 §5(3) 未获实验支撑 | 低 | 消融时打开并对比瓶颈机时间 |

---

## 5. 校验与回归

- 常驻测试 **30 项全绿**（`task_allocation/tests/`）：
  - `test_end_nest_encoding.py`（3）——终点机巢由 `cost_vector` 决定而非退化为最近机巢；
  - `test_completion_gate.py`（4）——完成闸门开/关/UAV 级归因 + 惩罚量纲解耦；
  - `test_constraint_switches.py`（4）——11 类约束开关；
  - 其余为约束、算子保真、事件重规划。
- 复现命令：`cd task_allocation/experiments/exp01_smoke && python run.py [--pdf]`。
