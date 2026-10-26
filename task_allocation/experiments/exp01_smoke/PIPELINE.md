# exp01_smoke：从实验配置到结果绘制的全链路记录

本文回答：**一次实验的每个数字/每张图是从哪来的**（可追踪、可复现）。

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
`seed_summary=None`。因此默认单 seed 运行产 14 张图（S01/I01/P01–P11/R01），
注册表共 **16 张**——`seed_summary`（R02）仅多 seed 运行产出，`ab_compare`（R03）
需 ≥2 组对照配置。引用过程图时须注明"代表 seed"。

周期数不固定：`rolling.n_cycles` 只是安全上限，真实停止由
`stop_when_stable`（enabled，patience=4）收敛早停决定——稳态剩余即物理不可达/
淘汰任务，符合「任务不被完成只有物理约束被违反」原则。

---

## 2. 追踪表：配置段 → 消费代码 → 产物字段 → 用到的图

| 配置段 | 被谁读取 | 落到哪个字段 | 哪张图用它 |
|---|---|---|---|
| `problem`（机巢数/容量/机队规模/速度/航程） | `build.build_scenario` / `build_uavs` | `metrics.problem`、`nests`、`fleet` | `geo_overview`、`fleet_state` |
| `tasks`（规模/散布/收益/时间窗 `deadline_cycles_*`） | `build_task_config` → `build_arrivals` | `pool_counts.n_new`、`tasks[].latest`、`tasks[].arrival_time` | `task_dynamics` |
| `selection`（α、权重、边际收益、防饥饿 `force_after_seconds`） | `RollingHorizon` → `selection.run` | `cycles[].selection.*`（`n_after_prefilter`/`n_after_marginal`/`n_selected`） | `task_selection` |
| `muas`（λ、四层适应度权重、迟到折减窗、剔除开关、覆盖门禁开关） | `run_selective_muas` | `cycles[].muas.*`（`f1_reward`、`f2_cost`、`feasible`、`n_hard`、`coded_end_nests`、`n_evals`、`fitness_weights`、`final_*`） | `allocation`、`task_dynamics` |
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
| process | `allocation_gantt` | 分配航迹条（从 allocation 拆出）：线长 ∝ 真实航程 · 方块=任务 · 两端起降巢 | `solution.json 的 cycles[].tours（est_cost）+ tasks[] / nests[]` |
| process | `allocation_map` | 逐周期空间演化（从 allocation 拆出）：DEM + 航次回放 1×N | `solution.json 的 cycles[].tours + tasks[] / nests[] / dem` |
| process | `allocation_anim` | 分配过程动画演变：滚动闭环航迹生长 / 任务点亮 + 当前周期 DMDE 收敛 + 机巢驻巢数实时标注（GIF） | `solution.json 的 cycles[].tours + cost_history + tasks[] / nests[] / dem` |
| process | `allocation_status` | 机队再平衡：各机巢驻巢无人机数随周期演化 + 返航巢迁移弧线（GIF） | `solution.json 的 cycles[].tours（uav_id / start_nest_id / end_nest_id）+ nests` |
| process | `allocation_anim_map` | 分配过程动画（仅地图 · 高清大图）：DEM + 航迹回放 / 任务点亮 + 机巢驻巢数实时标注（GIF） | `solution.json 的 cycles[].tours + tasks[] / nests[] / dem` |
| process | `task_dynamics` | 任务命运：池水位 / 流量 / 状态矩阵 / 等待与滞留 / 地理生命周期 | `metrics.json 的 cycles[].pool_counts / completed_ids / deferred_ids + solution.json 的 tasks[]` |
| process | `fleet_state` | 机队与机巢：三口径出动 / 占用热力图 / 选巢归因 / 机队迁移 | `metrics.json 的 cycles[].muas / nest_occupancy + solution.json 的 tours / nests` |
| process | `constraint_state` | 约束与时限：预筛归因 / 约束状态 / 罚项量纲 / 时间窗满足度 | `metrics.json 的 cycles[].selection.filter_reasons / muas.{violations,penalty,infeasible_tags} + tasks[].{earliest,latest,completed_at}` |
| report | `outcome_summary` | 结果总览：最终完成率 η_final 轨迹 / 未完成任务终局构成 / 双目标 / 关键指标 | `metrics.json 的 cycles[].pool_counts / completed_ids / summary + per_seed` |
| report | `seed_summary` | 跨 seed 稳健性：三指标均值±σ / 分布箱线 / 逐 seed 演化 / 明细表 | `metrics.json 的 per_seed / seed_summary / seeds（仅多 seed 运行）` |
| report | `ab_compare` | 两组配置对照：最终完成率 / 收益—代价 / 权衡 / Δ% | `results/metrics_<tag>.json 的 per_seed / seed_summary（需 ≥2 组对照）` |

**改图不必重跑**：`plot.render_from_results(results_dir)` 直接读已落盘的
`metrics.json` + `solution.json` 重绘。只想出一部分图（例如凑论文插图）：

```bash
# 只出结论层（跨 seed 稳健性）
python -c "from task_allocation.experiments.exp01_smoke.plot import \
render_from_results as r; r('task_allocation/experiments/exp01_smoke/results', \
groups=['report'])"
# 或跑实验时限定：python run.py --group report
#   ⚠️ 分层名必须与 plot/registry.py 的 FigureSpec.group 一致，只有
#      setup / initial / process / report 四个。旧文档写的 scene 不存在，
#      resolve 会抛 ValueError（2026-10-24 修复 P0-3）。
#   单张图：python run.py --figure geo_overview
```

---

## 3. 校验与回归

- 常驻测试全绿（`.venv/Scripts/python.exe -m pytest task_allocation/tests tests -q`），
  覆盖四层适应度（`test_fitness_tiers.py`）、覆盖门禁（`test_coverage_gate.py`）、
  覆盖优先两段式（`test_coverage_first.py`）、构造式解码（`test_constructive_decode.py`）、
  终点机巢编码、完成闸门、约束开关、修复回流（`test_repair_reflow.py`）、
  端到端边界（`test_end_to_end_boundaries.py`）、交付解适应度对账
  （`test_lexicographic.py` N2 口径）等。
- **正确性审计门禁**：`run.py` 每次运行末尾自动执行 `audit_correctness.py`
  （守恒/可行/口径自约不变量，FAIL 终端红字报警不阻断；`--no-audit` 可关）。
  含权重快照对账（N1 搜索期自洽 / N2 交付解可复算）。
- 复现命令：
  - `cd task_allocation/experiments/exp01_smoke && python run.py [--seeds 42,43,44] [--pdf]`
  - 补图不重跑：`python render_from_json.py png|gif|poster [--figure ID]`
