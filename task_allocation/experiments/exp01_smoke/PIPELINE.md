# exp01_smoke：从实验配置到结果绘制的全链路记录

本文回答两个问题：**一次实验的每个数字/每张图是从哪来的**（可追踪、可复现），
以及**历史审查结论的当前状态**（哪些已修、哪些仍存在、哪些移交后续实验）。

> 维护约定：图清单以 `python -c "from plot import registry; print(registry.describe())"`
> 的输出为唯一事实来源；本文数字（seed 数、图数、测试数）改动后请同步。

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
保证任务生成与优化两端都随 seed 变化、且同配置可复现。`run.py --seeds 42,43,44`
（支持区间/混写、去重保序）可覆盖 yaml 的 `seeds: [42]` 做多种子稳健性运行。

`metrics.json` 的 `cycles` 只装**代表 seed（首个）**；多 seed 运行时才附
`seed_summary`（mean±std）与 `per_seed`（每 seed 全量 cycles），单 seed 运行
`seed_summary=None`。因此默认单 seed 运行产 **14 张图**（S01/I01/P01–P11/R01），
注册表共 **16 张**——`seed_summary`（R02）仅多 seed 运行产出，`ab_compare`（R03）
需 ≥2 组对照配置。引用过程图时须注明"代表 seed"。

周期数不固定：`rolling.n_cycles=60` 只是安全上限，真实停止由
`stop_when_stable`（enabled，patience=4）收敛早停决定——稳态剩余即物理不可达/
淘汰任务，符合「任务不被完成只有物理约束被违反」原则。

---

## 2. 追踪表：配置段 → 消费代码 → 产物字段 → 用到的图

| 配置段 | 被谁读取 | 落到哪个字段 | 哪张图用它 |
|---|---|---|---|
| `problem`（机巢数/容量/机队规模/速度/航程） | `build.build_scenario` / `build_uavs` | `metrics.problem`、`nests`、`fleet` | `geo_overview`、`fleet_state` |
| `tasks`（规模/散布/收益/时间窗 `deadline_cycles_*`） | `build_task_config` → `build_arrivals` | `pool_counts.n_new`、`tasks[].latest`、`tasks[].arrival_time` | `task_dynamics` |
| `selection`（α、权重、边际收益、防饥饿 `force_after_seconds`） | `RollingHorizon` → `selection.run` | `cycles[].selection.*`（`n_after_prefilter`/`n_after_marginal`/`n_selected`） | `task_selection` |
| `muas`（λ、四层适应度权重、迟到折减窗、剔除开关） | `run_selective_muas` | `cycles[].muas.*`（`f1_reward`、`f2_cost`、`feasible`、`n_hard`、`coded_end_nests`、`n_evals`、`fitness_weights`、`final_*`） | `allocation`、`task_dynamics` |
| `constraints`（约束开关 + 硬/软分桶权重） | `MUASProblemConfig.constraints` → `check_feasible`；`feasibility.check_time_window=false`（默认）关闭第一阶段时间窗预筛 | `cycles[].muas.violations`、`feasible` | `constraint_state` |
| `solver`（种群/代数/ζ/δ） | `DMDEConfig` | `muas.n_evals`、`solution.cycles[].cost_history` | `dmde_search` |
| `cost`（DEM 步长、离地余量、爬升率） | `build_terrain` / `build_cost_estimator` | `run_meta.dem_terrain`、`solution.dem` | `geo_overview`（高程底图） |
| `rolling`（周期长/`complete_after_cycle`/`recharge_at_nest`/`stop_when_stable`/`faults`/`strict_capacity`/`event_replan`） | `HorizonConfig`、`EventTriggerConfig` | `cycles[].completed_ids`、`executed_ids`、`deferred_ids`、`expired_ids`、`infeasible_ids`、`released_by_fault`、`replan` | `task_dynamics`、`allocation` |
| `plot`（formats/dpi） | `run.main` → `plot.make_plots` → `plot.registry` | — | 见图清单 |

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

当前 16 张图（此表由 `python -c "from plot import registry; print(registry.describe())"`
生成，改动后请重跑覆盖）：

| 层 | 图 id | 说明 | 数据来源 |
|---|---|---|---|
| setup | `setup_params` | 实验设定总览：问题规模 / DMDE 超参 / 选择机制 / 约束开关 / 滚动设定 | `config.yaml 全段 + solution.json 的 nests / tasks / uavs` |
| initial | `geo_overview` | 初始态势：地形 · 机巢 · 初始机队 · 任务散布 | `solution.json 的 dem / nests / tasks / uavs` |
| process | `task_selection` | 动态任务选择：漏斗逐级收敛 / 保留率 / 能力上限 / 池守恒 | `metrics.json 的 cycles[].selection 全字段 / muas.n_selected` |
| process | `dmde_search` | DMDE 搜索机制：动态交叉率 CR（公式 3-9）/ 探索—开发平衡 / 收敛曲线 / 求解开销 | `solution.json 的 cycles[].cost_history（长度−1 即 S）+ config.yaml 的 solver.zeta` |
| process | `allocation` | 单周期分配结构：航迹条（线长 ∝ 航程）/ 逐周期空间演化（总览） | `solution.json 的 cycles[].tours + tasks[] / nests[] / dem` |
| process | `allocation_gantt` | 分配航迹条（从 allocation 拆出）：线长 ∝ 真实航程 · 方块=任务 · 两端起降巢 | `solution.json 的 cycles[].tours（est_cost）+ tasks[] / nests[]（线长按 est_cost 标定，段间距按三维几何）` |
| process | `allocation_map` | 逐周期空间演化（从 allocation 拆出）：DEM + 航次回放 1×N | `solution.json 的 cycles[].tours + tasks[] / nests[] / dem；周期里程 = 该周期 tours 的 est_cost 之和（纯 DEM 航程，不含惩罚）` |
| process | `allocation_anim` | 分配过程动画演变：滚动闭环航迹生长 / 任务点亮 + 当前周期 DMDE 收敛（GIF） | `solution.json 的 cycles[].tours + cost_history + tasks[] / nests[] / dem；左右两栏——地图下方的画布级状态行（周期/已到达/已完成百分比/里程，白底随文字自动贴合；置于图外因图内任何角落都会压住贴顶任务标号），右栏当前周期收敛曲线；完成进度只用百分比数字表达，不画进度条` |
| process | `allocation_status` | 机队再平衡：各机巢驻巢无人机数随周期演化 + 返航巢迁移弧线（GIF） | `solution.json 的 cycles[].tours（uav_id / start_nest_id / end_nest_id）+ nests（capacity）；驻巢数按连续巢归属链逐周期累计；迁移弧线=相邻周期 end_nest 变化（标 UAV 编号）` |
| process | `allocation_anim_map` | 分配过程动画（仅地图 · 高清大图）：DEM + 航迹回放 / 任务点亮（GIF） | `solution.json 的 cycles[].tours + tasks[] / nests[] / dem；allocation_anim 左栏地图的单独高清版（满幅满高铺底 DEM），不含右栏 DMDE 收敛与滚动进度条` |
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

配套分析脚本（只读 json、不重跑实验）：`analyze_longhorizon.py` / `report_longhorizon.py`
打印完成率、吞吐、正确执行率、废飞/淘汰与逐周期状态；`render_from_json.py`
从 json 补出图（GIF 需逐个 `--figure` 渲染，一次全渲会超时）。

---

## 3. 历史归档：2026-10-12 两组对照运行（数据已清理）

以下为旧协议（「迟到=废飞」硬闸门）时期的时间窗开关对照，`metrics_tw_off/on.json`
已随目录清理删除，**仅留方法学记录**；现行口径见 §4 各条状态与
`ABLATION_fitness_tiers_2026-10-18.md`。A 组 `constraints.time_window=false`；
B 组 `time_window=true` + `muas.w_violation=0.01`（5 seed × 3 周期）：

| 指标 | A：时间窗关闭 | B：时间窗开启 + 惩罚解耦 |
|---|---|---|
| 周期解可行率 | **15/15** | **12/15（0.80±0.16）** |
| 时间窗违约 | 0 | 4 次 / 4 个任务 |
| 完成率 | 0.852 ± 0.083 | 0.487 ± 0.050 |
| 总奖励 F1 | 95.61 ± 18.49 | 61.74 ± 17.27 |
| 总路径代价 | 313803 ± 95565 m | 129377 ± 52927 m |
| 平均等待 | 651.6 ± 28.9 s | 652.3 ± 44.8 s |
| 闸门拦截的违约交付 | 0 | 1.0 ± 0.89 个/次 |

读法：B 组完成率下降**不是**算法退化，而是时间窗真正生效后的权衡；旧协议把迟到
计为废飞，完成率回落到「诚实」水平——该语义已被「迟到不是废飞」新协议
（迟到=完成+奖励折减）取代。

---

## 4. 历史审查结论的当前状态

### 方法侧

| # | 问题 | 状态（2026-10-18） |
|---|---|---|
| M1 | 单一 `w_violation` 无法同时标定两类量纲 | **已解决**：四层准字典序适应度（硬违约/软违约/交付/路径分层，2026-10-18 消融定稿 A2） |
| M2 | `metrics.f2_cost` 与适应度口径不一致 | **已解决**：f2_cost 降为诊断项；适应度记账口径（搜索期 vs 交付解）见 report.tex §2.3，权重快照随周期落盘 |
| M3 | 完成指标缺少构成分解 | **已解决**：summary 含 `n_completed`/`n_eliminated`/`n_unreachable`/`n_infeasible_delivery`/`n_remaining_pool`，恒等式 `arrived=done+unreach+elim+remain` |
| M4 | `complete_after_cycle` 周期末瞬移，`avg_wait≈cycle_length` 是周期量化产物 | **仍存在（语义已声明）**：报告明确该语义；长程协议归 exp03 |
| M5 | `consume_range` 在 `est_cost=inf` 时把剩余航程打成 `-inf` | **已解决**：`max(0.0, …)` 兜底 |
| M6 | 第一阶段可达性用欧氏距离 | **已解决**：预筛改 DEM 三维往返口径（`2×travel > margin×remaining_range`，margin=1.0） |
| M7 | `end_nest_reachability` 与 `range` 双重计罚 | **已解决**：该标签已并入硬违约桶（`range_uav_*`/`tour_infeasible_*`） |
| M8 | 禁飞区未实现 | **挂起**（按决定） |
| M9 | 合并—删任务链路吞任务 | **已解决**：合并加结构性航程门 + 修复回流（2026-10-16），`test_repair_reflow.py` 对抗测试锁定 |

### 实验侧（exp01）

| # | 问题 | 状态（2026-10-18） |
|---|---|---|
| E1 | 关键约束在 exp01 里是惰性的（容量/航程/预筛未咬合） | **移交 exp02**：exp01 定位=冒烟+正确性验证台（report.tex §1.2）；压力场景（机多任务少反转）归 exp02 |
| E2 | 无基线、无消融 | **部分解决**：适应度消融已完成（`ABLATION_fitness_tiers_2026-10-18.md`）；基线对照归 exp05（未实现） |
| E3 | 参与机数被 SRP 编码限制为 `min(K, N-1)` | **仍存在（建模约束）**：论文声明；K≈N 对比归 exp02 |
| E4 | `w_violation=0.01` 拍定未做灵敏度 | **已解决**：被四层适应度 + 迟到折减窗 W=1800s 标定（拐点实测）取代 |
| E5 | 3 个周期太短 | **已缓解**：`stop_when_stable` 收敛早停 + 当前 8 周期口径；长程吞吐归 exp03 |
| E6 | 事件驱动从未真正触发 | **配置已开**（`event_replan: true`）；故障注入触发验证归 exp03 |
| E7 | 统计口径：单 seed、图只画代表 seed | **已缓解**：`--seeds 42,43,44` 三 seed 实测（report.tex 表 1）；`seed_summary`/R02 仅多 seed 运行产出 |
| E8 | `F3`（`w_time`）默认 0，论文 §5(3) 未获支撑 | **仍存在**：消融项，归 exp04 |

---

## 5. 校验与回归

- 常驻测试 **236 项全绿**（`task_allocation/tests/` + `tests/`），覆盖四层适应度
  （`test_fitness_tiers.py`）、终点机巢编码、完成闸门、约束开关、修复回流
  （`test_repair_reflow.py`）、端到端边界（`test_end_to_end_boundaries.py`）、
  交付解适应度对账（`test_lexicographic.py` N2 口径）等。
- **正确性审计门禁**：`run.py`/`run_chunked.py` 每次运行末尾自动执行
  `audit_correctness.py`（23 项不变量/口径，FAIL 终端红字报警不阻断；
  `--no-audit` 可关）。含权重快照对账（N1 搜索期自洽 / N2 交付解可复算）。
- 复现命令：
  - `cd task_allocation/experiments/exp01_smoke && python run.py [--seeds 42,43,44] [--pdf]`
  - 长程分块续跑（本环境 600s 前台超时是真 SIGTERM）：`python run_chunked.py --out <dir> --seed <s> --budget-seconds 500`，按周期原子存档、自动续跑且逐位一致
  - 补图不重跑：`python render_from_json.py png|gif|poster [--figure ID]`
