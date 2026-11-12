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
`seed_summary=None`。因此默认单 seed 运行产 4 张静态图（I01/P03/P04/R01）+ 1 张动图（P06）。
图件裁剪到 4 张（gif 只保留 P06、P04 甘特图追加保留）；P03 `allocation`
与 P04 并存接入注册表，现共 **5 张**。引用过程图时须注明"代表 seed"。

周期数不固定：`rolling.n_cycles` 只是**硬安全上限**，真实停止由池空自然收敛判据
决定——到达全部注入后，**池中不存在「能被完成却没被完成」的任务**（静态几何不可达
已出池标记 `UNREACHABLE`，服务暂缓 `DORMANT`（原称「休眠」，更名——
「休眠」表意不清）仍在池中、继续等条件恢复唤醒）即自然
停机；`stop_when_stable`（patience=4）降为兜底：仅当池非空、池内无服务暂缓任务且连续该数个
周期零进展（无完成/唤醒/新暂缓/新出池）才提前停。⚠ 兜底只覆盖无服务暂缓任务的
僵死池（如能力短缺反复装不下）；池内有服务暂缓任务时兜底禁用（正在等条件
恢复 ≠ 零进展），「时变能力永不恢复」（=永久暂缓）不在兜底覆盖面内，
由 `n_cycles` 硬上限止损（审查 M1 如实化）。新判据下真实周期数由本规则决定，与旧「池大小连续不变即稳态」口径
不同：**实跑 3 seed 均于周期 4 自然收敛**（`stop_reason=pool_empty`），
旧「约 7 周期」实测值不可引用（见 config.yaml 与本文 §4）。

绘图周期轴（口径约定「哪周期结束就绘制到哪个周期；不要绘制空周期了」）：
周期 4 是零出动的收敛判定周期，`plot.make_plots` 在分发前把**绘图视图**裁到最后一个
有活动周期（止于 C3），收敛空周期不绘制；落盘 `metrics.json` / `solution.json` 与
`summary.n_cycles`（=4）保持完整，审计链不丢。故「图上周期数」与「落盘记录周期数」
可能差 1，属口径约定而非数据不一致。**吞吐分母已统一**：=
**运营周期数**（有活动的周期数 = 绘图周期轴长，即落盘周期裁掉尾部收敛空周期；
判据单源 `methods/rolling/horizon.cycle_record_has_activity`，与绘图裁剪同则），
结果总览 (a) 面板累计吞吐曲线终点、`(d)` 表与 `metrics.json` 的
`throughput_per_cycle` 同源同值；`summary.n_oper_cycles` 落盘可查，
`n_cycles` 仍为全周期数供审计（统一前 (a) 按绘制周期数、(d) 按落盘全周期数，
分母双值、不要求相等——已废）。

---

## 2. 追踪表：配置段 → 消费代码 → 产物字段 → 用到的图

| 配置段 | 被谁读取 | 落到哪个字段 | 哪张图用它 |
|---|---|---|---|
| `problem`（机巢数/容量/机队规模/速度/航程） | `build.build_scenario` / `build_uavs` | `metrics.problem`、`nests`、`fleet` | `geo_overview` |
| `tasks`（规模/散布/收益/时间窗 `deadline_cycles_*`） | `build_task_config` → `build_arrivals` | `pool_counts.n_new`、`tasks[].latest`、`tasks[].arrival_time` | `allocation_anim`、`outcome_summary` |
| `selection`（α、权重、边际收益、防饥饿 `force_after_seconds`） | `RollingHorizon` → `selection.run` | `cycles[].selection.*`（`n_after_prefilter`/`n_after_marginal`/`n_selected`） | —（原 `task_selection` 图已裁剪） |
| `muas`（λ、四层适应度权重、迟到折减窗、剔除开关、覆盖门禁开关） | `run_selective_muas` | `cycles[].muas.*`（`f1_reward`、`f2_cost`、`feasible`、`n_hard`、`coded_end_nests`、`n_evals`、`fitness_weights`、`final_*`） | `allocation_anim` |
| `constraints`（约束开关 + 硬/软分桶权重） | `MUASProblemConfig.constraints` → `check_feasible`；`feasibility.check_time_window=false`（默认）关闭第一阶段时间窗预筛 | `cycles[].muas.violations`、`feasible` | —（原 `constraint_state` 图已裁剪） |
| `solver`（种群/代数/ζ/δ） | `DMDEConfig` | `muas.n_evals`、`solution.cycles[].cost_history` | `allocation_anim`（右栏收敛曲线） |
| `cost`（DEM 步长、离地余量、爬升率） | `build_terrain` / `build_cost_estimator` | `run_meta.dem_terrain`、`solution.dem` | `geo_overview`（高程底图）、`allocation_anim` |
| `rolling`（周期长/`complete_after_cycle`/`recharge_at_nest`/`stop_when_stable`/`faults`/`strict_capacity`/`event_replan`） | `HorizonConfig`、`EventTriggerConfig` | `cycles[].completed_ids`、`executed_ids`、`deferred_ids`、`expired_ids`、`infeasible_ids`、`released_by_fault`、`replan` | `allocation_anim`、`outcome_summary` |
| `plot`（formats/dpi） | `run.main` → `plot.make_plots` → `plot.registry` | — | 见图清单 |

### 2.1 图的分层与查找

图的唯一事实来源是 `plot/registry.py`（`FIGURES`）——**新增一张图 = 在表里加一行**。
图按「**看图目的**」分三层（论文叙事线），而不是按实现技术：

| 层 | 回答什么问题 | 特性 |
|---|---|---|
| `initial` | 实验开始前世界长什么样？（地形 / 机巢 / 初始机队 / 首批任务） | 一次性静态，**与求解无关** |
| `process` | 这一轮怎么跑的？（滚动闭环的过程观测） | 随周期演化，看的是单次运行 |
| `report` | 实验说明什么？（可直接进论文的结论图） | 常跨多 seed / 多组配置汇总 |

命名约定（**四者同名**，见 `registry.py` 顶部）：

```
模块名 ≡ 图 id ≡ 输出文件名 ≡ 绘制函数名去掉 plot_
plot/initial/geo_overview.py  →  plot_geo_overview  →  geo_overview.png
```

层内另有 `topic` 二级标签（config / allocation / outcome）用于再归类，但**不建目录**——避免层级深过头。

当前 5 张图（此表由 `python -c "from plot import registry; print(registry.describe())"`
生成，改动后请重跑覆盖）：

| 层 | 图 id | 说明 | 数据来源 |
|---|---|---|---|
| initial | `geo_overview` | 初始态势：背景（建筑 / 地形）· 机巢 · 初始机队 · 任务散布 | `solution.json 的 dem / nests / tasks / uavs + data/<scenario>/building/*.shp（背景层规则：有建筑优先画建筑，只有地形才画 DEM）` |
| process | `allocation` | 分配结构与逐周期空间演化：甘特航迹条 + 背景底图逐周期演化 | `solution.json 的 cycles[].tours（est_cost）+ tasks[] / nests[] / dem（背景层：有建筑画建筑、只有地形画 DEM；逐周期面板只画有真实航次的活跃周期，未执行任务只标已到达者，与 allocation_anim 口径一致）` |
| process | `allocation_gantt` | 分配航迹条：线长 ∝ 真实航程 · 方块=任务 · 两端起降巢 | `solution.json 的 cycles[].tours（est_cost）+ tasks[] / nests[]（线长按 est_cost 标定，段间距按三维几何）` |
| process | `allocation_anim` | 分配过程动画演变：滚动闭环航迹生长 / 任务点亮 + 当前周期 DMDE 收敛（GIF） | `solution.json 的 cycles[].tours + cost_history + tasks[] / nests[] / dem（地图背景：有建筑画建筑、只有地形画 DEM）；左右两栏——地图下方的画布级状态行（周期/已到达/已完成百分比/里程，白底随文字自动贴合；置于图外因图内任何角落都会压住贴顶任务标号），右栏当前周期收敛曲线；完成进度只用百分比数字表达，不画进度条` |
| report | `outcome_summary` | 结果总览：完成率 CR 轨迹 / 未完成任务终局构成 / 双目标 / 关键指标 | `metrics.json 的 cycles[].pool_counts / completed_ids / summary + per_seed` |

### 2.2 地图背景层：建筑优先，地形兜底

所有带地图的图（当前 `geo_overview` / `allocation_anim`）都不自己 import
`dem` 或 `buildings`，一律走 `plot/base/backdrop.py`（**判断只做一次**）：

```python
from ..base.backdrop import _backdrop_extent, _draw_backdrop

bd = _draw_backdrop(ax, solution, nests=nests, tasks=tasks)   # 返回 Backdrop
cb.set_label(bd.label)                                        # "建筑高度 (m)" / "高程 (m)"
_set_scene_lim(ax, nests, tasks, uavs, dem, extent=_backdrop_extent(solution, nests, tasks, uavs))
```

| 场景 | 背景 | 色条 | 取景 |
|---|---|---|---|
| 有建筑（广州，`data.buildings` 指向 shp 目录） | 建筑轮廓按屋顶高度着色（色深 ∝ 高度） | 建筑高度 (m)，0 → 98 分位 | 建筑包络 ∪ 要素包络 + 3% |
| 只有地形（拉萨城关，`data.buildings: null`） | ASTER 瓦片 + 山体阴影晕渲 | 高程 (m) | DEM 视图范围（瓦片包络） |
| 两者都没有 | 纯底（不画背景） | 无色条 | 要素并集 + 留白 |

兼容回退（任一命中即退回 DEM，**不抛异常**）：`data.buildings` 未声明 / 目录无
`.shp`（合法，静默）；solution 无 `local_frame`、项目根不在 `sys.path`、shapefile
解析失败（各报警一次）；建筑包围盒与场景要素不同域（判为拿错场景数据）。
需要「建筑 + 地形」同框时用 `_draw_backdrop(..., dem_under_buildings=True)`。

**改图不必重跑**：`plot.render_from_results(results_dir)` 直接读已落盘的
`metrics.json` + `solution.json` 重绘。只想出一部分图（例如凑论文插图）：

```bash
# 只出结论层（跨 seed 稳健性）
python -c "from task_allocation.experiments.exp01_smoke.plot import \
render_from_results as r; r('task_allocation/experiments/exp01_smoke/results', \
groups=['report'])"
# 或跑实验时限定：python run.py --group report
#   ⚠️ 分层名必须与 plot/registry.py 的 FigureSpec.group 一致，只有
# initial / process / report 三个（图件裁剪后）。旧文档写的
# scene 不存在，resolve 会抛 ValueError（修复 P0-3）。
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

---

## 4. 机队航程标定与跨批次口径红线（2026-11-12）

### 4.1 研究区尺度与当前机队

`data/guangzhou/region.geojson` 实测 4846 m × 4792 m（对角 6815 m，约
5 km × 5 km）；落盘 30 个任务点跨 4634 × 4450 m；3 个机巢两两相距
2.4–2.8 km；任务到最近机巢的往返三维直线距离最大 4379 m、中位 2228 m。
`problem.fleet_file: uav_config.json` 的满电航程据此标定为
**10 km（id0/id1，20 m/s）/ 8 km（id5，15 m/s）/ 6 km（id2–id4，15 m/s）**；
`max_time_s` 一律留 `null`，由航程/速度派生（500 / 533.3 / 400 s），
不设「显式值宽于派生值」的永不绑定空约束。机队最强 10 km ≥ 最远单任务
往返的直线下界，不引入真几何不可达；同时装不下「把 20 个任务串成单航次」
的 22.5 km 路径（25 km 远程机曾给出该解）。

> 历史留档：原 `notes.md` / `notes_archive.md` / `figure_notes.md` 已按裁定
> 删除，标定史与清理记录不再入库；各批次可复现数值保留在
> `experiments/_archive/` 的对应结果档。本文与 `config.yaml` 注释是当前口径
> 的唯一入口。`metrics.problem.uav_max_range_km` 只是同构回退的回显缺省，
> 设 `fleet_file` 后不参与装配；逐机生效值只在 `solution.json` 的 `uavs` 段。

### 4.2 三批结果不可直接比较

| 批次 | 机队 | 结果档 |
|---|---|---|
| 11-10 | 同构 6 机 × 10 km @ 15 m/s | `_archive/exp01_smoke__results_2026-11-10_homogeneous/` |
| 11-11 | 异构 25 / 12 / 6 km | `_archive/exp01_smoke__results_2026-11-11_hetero25km/` |
| 11-12（当前） | 异构 10 / 8 / 6 km | `results/` |

三批的适应度权重（`λ=0.1`、`w_violation=0.01`、`w_penalty=1.0`、
`coverage_gate=True`、`deadline_cycles=2.0~4.0`、`range_margin=1.0`）**完全相同**，
只有机队逐机参数不同。但航程 / 速度 / 续航是整体更换，属多因素同改：
跨批差异**禁止配对 t 检验、禁止归因**为任何单因素或「方法变差」。
要解释批次间差异，必须在当前代码内另做受控单因素消融（至少拆开
`max_range` / `speed` / `w_violation`）。

### 4.3 口径红线（写论文时逐条遵守）

- 完成是可完成任务的硬约束、成本才是目标；完成率不作成绩排序项。
  恒等式 `arrived = done + unreachable + remain`；不可达分「真几何不可达」
  与「机队服务不可达」两类。
- 不存在「超期淘汰」；出现 eliminating（非不可达）＝缺陷，须修到 0。
- `total_f2 = path + penalty` 含惩罚项，**不是航程**。唯一航程口径是垂直切面
  DEM 有向三维航程（含返巢）；`f` 与航程线性同源，**不是两条独立证据**。
- 设计汇率 `1 f ≈ 10 m 航程 ≈ 100 s 迟到`（`λ=0.1`、`w_late=0.01/s`）。
  该标定使按时约束几乎不咬合（20 m/s 下 10 m 仅 0.5 s 飞行），
  `w_violation` 重标定仍列待办、**本批未修改**；禁止用这个跨批常量解释
  批次间按时率差异。
- p 不显著须如实声明；墙钟只作观测、不可跨实验横向比较。

### 4.4 本批正式档结果（2026-11-12，异构 10 / 8 / 6 km）

复现命令（正式档单独跑批，`-u` 挂起、求解期不出图）：

```
.venv/Scripts/python.exe -u task_allocation/experiments/exp01_smoke/run.py --no-plots
.venv/Scripts/python.exe -u task_allocation/experiments/exp01_smoke/build_tables.py
.venv/Scripts/python.exe -u task_allocation/experiments/exp01_smoke/render_from_json.py png,pdf
```

`FORMAL_RC=0`，3 seed（42/43/44）全跑，20m54s，**26/26 项审计 PASS**；
表与图由 `results/metrics.json` 单源生成，未手写数字。

| 指标（`seed_summary`，均值±标准差） | 本批 |
|---|---|
| 完成率 | 1.0000 ± 0.0000 |
| 按时率 | 1.0000 ± 0.0000 |
| 迟到任务数 / 平均迟到 | 0 ± 0 / 0 ± 0 s |
| 总三维航程 | 42371.93 ± 1821.15 m |
| 单位任务航程 | 1412.40 ± 60.70 m |
| 求解耗时 | 393.31 ± 10.31 s |

恒等式 `30 = 30 + 0 + 0`（残差 0）；真几何不可达、机队服务不可达、
eliminating、硬违约、服务暂缓、滞留池中全部为 0；3 seed 均在周期 4
池空自然收敛（`stop_reason=pool_empty`）。

逐周期航次（代表 seed 42）：C1 **3 个航次**（U0：4 任务 / 6435.5 m；
U1：9 任务 / 9456.9 m；U5：7 任务 / 7428.9 m），C2 2 个、C3 3 个，
每个航次都不超过该机满电航程与派生最大飞行时间（审计 J / J2 通过）。
与 25 km 档「C1 单机串 20 任务 / 22506.8 m」形成对照，但**这是并列观测，
不是单因素效应**。

> `total_f2 = 42371.93` 与本批 `total_path_m` 数值相同，**仅因本批迟到与
> 惩罚项均为 0**；`total_f2 = path + penalty` 仍含惩罚项，不得据此把 f2
> 当作航程。本批 `f = 0.1·path`（L=0），与航程线性同源。

三批并列（**只列观测，禁配对 t、禁归因**）：

| 批次 | 完成率 | 按时率 | 总三维航程 (m) | 单位任务航程 (m) |
|---|---|---|---|---|
| 11-10 同构 10 km | 1.0000 | 0.9778 ± 0.0157 | 42953.63 ± 2570.25 | 1431.79 ± 85.67 |
| 11-11 异构 25/12/6 km | 1.0000 | 0.7889 ± 0.0157 | 40882.53 ± 1605.78 | 1362.75 ± 53.53 |
| 11-12 异构 10/8/6 km | 1.0000 | 1.0000 ± 0 | 42371.93 ± 1821.15 | 1412.40 ± 60.70 |

本批按时率回到 1.0000，**不得**写成「方法变好」「机队航程缩小带来按时率
提升」或任何性能结论：三批是机队航程 / 速度 / 续航整体更换的多因素同改，
且适应度权重与时间窗未变。要解释批次间差异，须在当前代码内另做受控
单因素消融（至少拆开 `max_range` / `speed` / `w_violation`）。
