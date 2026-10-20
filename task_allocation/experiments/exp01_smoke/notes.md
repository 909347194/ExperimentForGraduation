# exp01_smoke

**status:** active(端到端已跑通)

## 目的

- 验证**完整链路**在真实地理场景上可跑:机巢 CSV + DEM → 任务/机队 → 两阶段求解 → 滚动闭环 → 指标落盘 → 出图。
- 不再只是目录与配置联调:`methods/` 已落地,本实验调用真实求解器(DMDE)。
- **不**作为算法性能结论(规模小、迭代少、任务为合成);性能对比见后续 exp05。

## 目录结构

```
exp01_smoke/
├── run.py        # 编排调度(唯一入口):装配 → 求解 → 落盘 → 出图
├── config.py     # config.yaml → 各模块配置对象(MUAS / DMDE / Horizon / …)
├── build.py      # 场景 / DEM 地形 / 机队 / 任务批次装配
├── metrics.py    # 指标汇总、运行元信息、分配结果、终端报表
├── paths.py      # 目录布局与 sys.path 引导
├── config.yaml   # 全部可调参数
├── plot/         # 可视化模块(代码)——按“看图目的”分四层(论文叙事线)
│   ├── __init__.py    # make_plots() 调度 + 从 results/ 直接重画
│   ├── registry.py    # ★ 图清单(唯一事实来源):11 张图,新增 = 加一行
│   ├── theme.py       # 学术绘图样式、中文字体、保存约定(唯一 import pyplot 处)
│   ├── base/          # 被图依赖但自己不是图的绘制件
│   │   ├── dem.py     #   DEM 取数/晕渲/分层设色
│   │   └── geo.py     #   图标图例、UAV 配色与航段、行式排版(_row_figsize)
│   ├── setup/         # ① 实验设置:问题规模/算法超参/约束开关(参数即图)
│   │   └── setup_params.py        # 实验设定总览(5 联)
│   ├── initial/       # ② 初始状态:地形/机巢/初始机队/首批任务(与算法无关)
│   │   └── geo_overview.py        # 2D DEM 初始态势
│   ├── process/       # ③ 求解过程:滚动闭环逐周期“发生了什么”(6 张)
│   │   ├── task_selection.py       # 动态任务选择漏斗/保留率/预选咬合/池守恒
│   │   ├── dmde_search.py          # DMDE:动态交叉率 CR/探索—开发/收敛/开销
│   │   ├── allocation.py           # 单周期分配:甘特 + 空间分配 + 逐周期 DEM 演化
│   │   ├── task_dynamics.py        # 任务命运:存量/流量/状态矩阵/等待与滞留
│   │   ├── fleet_state.py          # 机队与机巢:三口径出动/占用热力/选巢归因
│   │   └── constraint_state.py     # 约束与时限:预筛归因/约束状态/罚项量纲/时间窗
│   └── report/        # ④ 结果分析:跨 seed 结论与配置对照(可直接进论文)
│       ├── outcome_summary.py      # 结果总览(完成率轨迹/终局构成/双目标)
│       ├── seed_summary.py         # 多 seed 复现一致性(mean±σ)
│       └── ab_compare.py           # 两组配置对照(time_window 开/关等)
└── results/      # 数值产物(入库)
    ├── metrics.json    # 数值指标(不含分配结构)
    ├── metrics_tw_{off,on}.json    # A/B 两组配置的完整指标(供 ab_compare)
    ├── run_meta.json   # seed / 时间戳 / 配置路径 / DEM 网格
    ├── solution.json   # 任务表 + 机巢表 + 各周期航次与收敛轨迹 + 地理底图(dem) + 初始无人机(uavs)
    └── figures/        # 图产物:11 张 × {png, pdf}(pdf 为矢量, --pdf 或显式 formats 产出)
```

**拆分原则**:`run.py` 只做编排,不堆细节;任何文件都不超过 ~250 行。
产物按「数值 vs 结构」分流:`metrics.json` 只留数值指标,
`tours` / `cost_history` 这类结构性数据进 `solution.json`,供绘图与论文取数。
`plot/` **只负责绘制**——图存哪由 `experiment.figures_dir` 决定(约定在 `results/figures/`)。

## 运行

```bash
uv sync                                                     # 首次:建 .venv 装 numpy / pillow / pyyaml / matplotlib
.venv/Scripts/python.exe task_allocation/experiments/exp01_smoke/run.py
# 或不走 uv:python3 task_allocation/experiments/exp01_smoke/run.py
```

只想重画图(不重跑实验):

```bash
python3 -c "import sys; sys.path.insert(0,'task_allocation/experiments/exp01_smoke'); \
from plot import render_from_results as r; r('task_allocation/experiments/exp01_smoke/results', 'task_allocation/experiments/exp01_smoke/plot/figures')"
```

## 当前结果(默认 config.yaml)

> 默认已开启 `experiment.seeds: [42,123,777,2024,9093]` 五 seed 复现。
> 下面「代表运行」取 seed=42,「多 seed 汇总」为 5 次均值±标准差(ddof=0)。

```
场景:8 机巢,容量 4,10 架机,DEM 已接入,frame=(29.522202, 91.479331)
任务到达:初始 10 个 + Poisson(λ=5)/周期(第 2 周期起),代表运行实际到达 18 个
撒点域:tasks.domain=dem_extent(DEM 全域均匀撒点,约 97.5×110.9 km)
时间窗:周期相关 latest = 到达时刻 + U(3.5,7.0)×600s(≈2100~4200s)

代表运行( seed=42 ):
周期 1 @t= 600s | T_t=10(remain0/new10) K_avail=10 | T_sel=9 → T_exec=5(保留4) | F1=41.75 F2=138798m
周期 2 @t=1200s | T_t=10(remain5/new 5) K_avail=10 | T_sel=8 → T_exec=3(保留5) | F1=23.42 F2= 97350m
周期 3 @t=1800s | T_t=10(remain7/new 3) K_avail=10 | T_sel=7 → T_exec=3(保留4) | F1=15.33 F2= 84254m

代表汇总:完成 11/18(61.1%),平均等待 690s,总收益 67.1
多 seed 汇总(5 次):完成 11.6±2.0/18(61.3%±6.2%),平均等待 674±48s,
              总收益 79.6,总代价 353±59 km,求解 22738±5546ms
```

> 上一版(锚定机巢 12 km + 1.5~4.0 周期窗口 + 24 机)的 71.3% 已归档到
> `results/archive_nest_anchored/`(同口径重跑为 68.2%±5.8%)。两者不可直接比较:
> 见 §11「任务撒点域改为 DEM 全域」。

- 机巢占用始终 ≤ capacity(4),无超容(`capacity_violations` 为空,见 `fleet_state` 图(c) 占用热力图)
- 时间窗约束**真实生效**:远处/短窗任务被第一阶段可行性预筛正确丢弃,闭环内偶有
  `tw_task_*` 软违反(惩罚计入适应度,不致命),体现真实选择性压力
- 多 seed 的 ±σ 来自到达 Poisson 抖动 + 求解器随机性,是论文稳健性证据(见 `seed_summary` 图)

### 图产物(results/figures/)

| 层 | 图 id | 内容 | 数据来源 |
|---|---|---|---|
| setup | `setup_params` | 实验设定总览：问题规模 / DMDE 超参 / 选择机制 / 约束开关 / 滚动设定 | `config.yaml` 全段 + `solution.json` 的 nests / tasks / uavs |
| initial | `geo_overview` | 初始态势：地形 · 机巢 · 初始机队 · 任务散布 | `solution.json` 的 dem / nests / tasks / uavs |
| process | `task_selection` | 动态任务选择：漏斗逐级收敛 / 保留率 / 能力上限 / 池守恒 | `metrics.json` 的 cycles[].selection 全字段 / muas.n_selected |
| process | `dmde_search` | DMDE 搜索机制：动态交叉率 CR(公式 3-9) / 探索—开发平衡 / 收敛曲线 / 求解开销 | `solution.json` 的 cycles[].cost_history + `config.yaml` 的 solver.zeta |
| process | `allocation` | 单周期分配结构：UAV—任务甘特 / 空间分配图 / 逐周期 DEM 演化 | `solution.json` 的 cycles[].tours + tasks[] / nests[] / dem |
| process | `task_dynamics` | 任务命运：池水位 / 流量 / 状态矩阵 / 等待与滞留 | `metrics.json` 的 cycles[].pool_counts / completed_ids / deferred_ids + `solution.json` 的 tasks[] |
| process | `fleet_state` | 机队与机巢：三口径出动 / 占用热力图 / 选巢归因 | `metrics.json` 的 cycles[].muas / nest_occupancy + `solution.json` 的 tours / nests |
| process | `constraint_state` | 约束与时限：预筛归因 / 约束状态 / 罚项量纲 / 时间窗满足度 | `metrics.json` 的 cycles[].selection.filter_reasons / muas + `solution.json` 的 tasks[] |
| report | `outcome_summary` | 结果总览：完成率 CR 轨迹 / 未完成任务终局构成 / 双目标 / 关键指标 | `metrics.json` 的 cycles[] / summary + per_seed |
| report | `seed_summary` | 跨 seed 稳健性：三指标均值±σ / 分布箱线 / 逐 seed 演化 | `metrics.json` 的 per_seed / seed_summary(仅多 seed 运行) |
| report | `ab_compare` | 两组配置对照：完成率 / 收益—代价 / 权衡 / Δ% | `results/metrics_<tag>.json`(需 ≥2 组对照) |

每张图同时出 `png`(预览)与 `pdf`(矢量,直接进 LaTeX);中文字体 Noto Sans CJK SC。

## 参数放哪里

| 内容 | 位置 | 理由 |
|---|---|---|
| 机巢经纬度、DEM | `task_allocation/data/` | 实测事实,跨实验复用 |
| 机巢个数、容量、机队规模、航程、速度 | `config.yaml` 的 `problem:` | 实验设定 |
| 任务规模、散布、收益、时间窗 | `config.yaml` 的 `tasks:` | 任务当前为合成,后续换 CSV 时参数可平移;`deadline_cycles_min/max` 把绝对秒数时间窗改为周期相关 |
| 第一阶段权重与阈值 | `config.yaml` 的 `selection:` | 做 α / 五项权重灵敏度时只改这里 |
| 第二阶段模型与剔除策略 | `config.yaml` 的 `muas:` | λ、选择性剔除、等待补偿、终点进编码、Lamarckian 回写 |
| DMDE 超参 | `config.yaml` 的 `solver:` | 种群、代数、ζ;`delta` 留空 = 论文式 3-12 原式。代数当前 500（2026-10-14 按 Ming 2017 收敛标定由 100 上调，依据见 yaml 注释） |
| 三维代价 / DEM 网格步长 | `config.yaml` 的 `cost:` | 网格步长直接决定一次性开销 |
| 滚动周期、到达、故障注入 | `config.yaml` 的 `rolling:` | `arrivals: {mode: poisson\|uniform\|fixed, …}` 随机到达流;含 `strict_capacity` 硬校验开关。**长程协议（2026-10-14）**：`n_cycles: 24` + `arrivals.mode: poisson, lambda: 5.0`——完成率不是目标，主指标为正确执行吞吐/正确执行率（见 metrics summary 新字段）。注意 `fixed` 的 counts 长度必须 = n_cycles−1，否则延长周期只补 0 到达空转 |
| 多 seed 复现 | `config.yaml` 的 `experiment.seeds` | 多次随机重复取均值±标准差(论文稳健性证据);仅 1 个元素时退化为单 seed |
| 命令行覆盖(不改 yaml) | `run.py --seeds 42 43 44` / `42-46`(区间)/ `42,43`(逗号,可混用);`--no-plots` | 多种子长跑的调试入口:覆盖 seeds、跳过图表(只落 json);区间展开 `--seeds 42-51` 即 10 seed |
| 图格式、分辨率 | `config.yaml` 的 `plot:` | 代码在 `plot/`,产物路径在 `experiment.figures_dir` |
| 约束开关与惩罚权重 | `config.yaml` 的 `constraints:` | 11 个开关 + 9 个权重,置 false 即该约束既不判可行也不计惩罚(消融/口径取舍) |
| 惩罚量纲解耦 | `config.yaml` 的 `muas.w_violation` | null = 历史口径(惩罚随 F2 乘 λ);给数值则惩罚以「奖励当量/惩罚单位」直接计入 |
| 完成反馈闸门 | `config.yaml` 的 `rolling.completion_feasibility_gate` | true = **物理不可行**任务不顺延完成、回池重选（迟到交付正常完成，2026-10-15「迟到不是废飞」协议）;false = 历史行为（一律标完成） |
| 迟到奖励折减 | `config.yaml` 的 `muas.late_reward_decay_seconds` | 晚到任务有效奖励 `eff = R × max(0, 1 − L/W)`；须与 `selection.feasibility.late_tolerance_seconds` 同值；null = 关闭（历史口径） |

**原则**:`methods/` 不读配置、不写死数值;所有数值由 `config.py` 从 yaml 取出后传入。

## 成功标准

1. `python run.py` 跑完不抛异常,写出 `results/metrics.json`、`results/run_meta.json`、`results/solution.json`。✅
2. 目录结构符合 `experiments/README.md`。✅
3. `metrics.json` 中记录 8 个机巢的米制坐标 (x, y) 与 DEM 采样高程 z。✅
4. 每个周期的 `check_feasible` 无 violation(当前满足)。✅
5. `T_t^exec ⊆ T_t^sel ⊆ T_t`、`U_t^exec ⊆ U_t^avail` 恒成立。✅
6. `make_plots: true` 时产出 6 张图 × 2 种格式,无缺字告警。✅
7. 图产物在 `results/figures/`,`plot/` 保持纯代码。✅

## 本次修订(2026-10-10)

### 1. 修 bug:任务到达批次错位(影响完成率口径)

`rolling.arrivals_per_cycle: [5, 5]` 的语义是**第 2 周期起每周期的新到达任务数**
(见 config.yaml 注释),但 `common.task_scenario.build_task_batches` 把
`per_cycle` 的**下标 0 映射到周期 1**。原 `run.py` 直接传 `per_cycle=[5, 5]`,导致:

- 下标 0 的 5 个任务落到周期 1,又与 `tasks.n_tasks` 的初始任务共用 id 段,
  最后被 `if c >= 2` 过滤掉 → **静默丢弃**
- 周期 3 的 `n_new` 变成 0(本应 5)
- `n_arrived` 按 `len(initial) + sum(later)` 算成 20,而实际进池只有 15
  → **完成率分母虚高**,旧记录 `13/20 = 65%` 实为 `13/15 = 86.7%`

**修法**:`build.build_arrivals` 在 `per_cycle` 前补一个 0 对齐到周期 2;
`n_arrived` 改为按 `TaskBatch.all_tasks()`(真正进入闭环的任务集合)计数。
修后周期 1/2/3 = 10/5/5,`n_arrived = 20` 与实际一致,完成率 16/20 = 80%。

### 2. 拆分臃肿的 run.py

原 `run.py` 387 行,混杂配置装配、实体构建、指标计算、落盘。现拆为
`config.py` / `build.py` / `metrics.py` / `paths.py`,`run.py` 只剩编排调度(~160 行)。

### 3. 补上可视化(原 TODO 阶段 6)

`plot/` 从空目录变成绘图模块,`make_plots` 落地;图只读已落盘的 `results/` 产物,
**改图不必重跑实验**(`plot.render_from_results`)。

### 3.1 图产物归位 + 分配图重画(2026-10-10 二轮)

- **图产物从 `plot/figures/` 迁到 `results/figures/`**:`plot/` 只负责绘制,
  不再夹带产物;配置项 `experiment.plot_dir` → `experiment.figures_dir`。
- **原 `assignment_map` 体现不出分配**:全场景 90 km × 87 km,航次只是巢附近
  15–50 km 的短线且七成是单任务往返,三个周期横排各 4.6 英寸宽。重画方案:
  - 新增 **`allocation_gantt`**(分配甘特图):行=UAV 航次、列=任务顺位、
    右侧三列(起止机巢/航程/航次收益)。分配结构一眼可读。
  - **`assignment_map` 重画**:改竖排三行(11 英寸宽)、有向箭头标出执行顺序、
    任务按**承运 UAV** 着色(一眼看出分组)、终点机巢套同色圆环标异巢终止、
    标签加白底衬底 + 按 id 轮转偏移避让、每周期独立小图例。
  - 注：`allocation_gantt` 与 `assignment_map` 在 2026-10-12 的 20→11 重组中已
    合并为单张 `process/allocation.py`（真甘特 + 逐周期空间演化 1×N 横排），
    并去掉了右侧三列（口径改由 footnote 说明）。
- **修掉一处版式撞车**:甘特图右侧标注原放轴外 `x=1.02`,色条贴轴右缘,
  导致部分行起始机巢被色条遮住(PDF 文本完整但视觉不可读)。
  改为右侧三列画在**轴内留白区**、色条留轴外,彻底不重叠。

**已知待打磨的空间图问题**(不影响分配语义):
巢7 一带任务密集时圆点仍有部分重叠(需局部放大 inset 才能根治);
极短航次的箭头方向辨识度仍偏低。

### 4. methods 侧的最小扩展

`rolling.horizon.CycleRecord` 增加 `tours` 与 `cost_history` 两个字段
(不进 `as_dict()`/`metrics.json`,由实验编排写入 `solution.json`),
使分配图与收敛图有数据可取。算法逻辑未改动。

### 6. 适应度函数新增最坏机瓶颈时间项 F3（论文 §5(3) 负载均衡）

此前 DMDE 的标量适应度只有双目标：`f = -F1 + λ·F2`（奖励最大化 + 路径/惩罚
最小化）。对照 Ming et al. 2017 Eq.1 的 `a·max(Σt)` 最坏机飞行时间项，本轮回填
被声明但未接线的 `w_time`，把**负载均衡/公平性**纳入评价：

```
f(s) = -F1 + λ·F2 + w_time · F3 ,   F3 = max_k T_k
```

- `T_k`：UAV k 的航迹时间（含飞行 + 等待 + 服务 + 返巢），游标语义与
  `coop_constraints.check_time_window_constraint` 一致；`tour_time()` 实现。
- `F3 = max_k T_k`：所有航次中最大的单架机任务时间，即瓶颈机。最小化该项
  使任务在 UAV 间更均衡，避免单架机成为执行瓶颈（与论文 a·max(Σt) 同义）。
- `w_time` 默认 `0.0`：**关闭时退化为原双目标形式，行为与重构前逐字节一致**
  （已用 `test_constraints.py::test_scalar_fitness_bottleneck_term` 锁定增量恰为
  `w_time·F3`）。
- 接线：`muas.w_time`（`config.yaml`/`exp02 problem:` 区块）→ `MUASProblemConfig`
  → `problem.scalar_fitness`。`evaluate` 把 `bottleneck_time` 写入 `solution.meta`。

**启用方式**：把 `muas.w_time` 调到 `1e-3 ~ 1e-2` 量级（与 `λ·F2` 同量纲），
即可在不改变 F1/F2 的前提下压低最坏机时间；配合论文 §5 的 λ 灵敏度实验可观察
完成率 vs 均衡度的权衡曲线。

**与论文差异（仍保留）**：论文主项为最小化加权距离、约束为单 Σc_k；我们主项为
最大化任务奖励 R_j、约束按族模块化（本轮已建），并额外引入多机巢容量与滚动
选择性（y_j=0 可延期）。瓶颈时间项是少数可近乎原样移植的项之一，故优先落地。

### 5. 任务生成可信度提升(周期相关窗口 + 随机到达 + 多 seed)

实验任务原为「固定批次 + 绝对秒数时间窗」:窗口 6~24 周期形同虚设、到达为常数流、
单 seed 无随机性证据。本轮做三处最小改动,立刻提升论文可信度:

1. **时间窗对齐时域**(`common/task_scenario.py`)
   `SyntheticTaskConfig` 新增 `deadline_cycles_min/max` 与 `cycle_length`;
   `latest = 到达时刻 + U(cycles_min, cycles_max) × cycle_length`。
   默认 `cycle_length=600s`、窗口 `(1.5, 4.0)` → 900~2400s(≈0.5~1.3× 总时域 1800s),
   远处/短窗任务被第一阶段可行性预筛正确丢弃,体现真实选择性压力。
   未设 `cycle_length` 时退回绝对秒数 `deadline_min/max`(向后兼容旧配置)。

2. **到达随机化**(`build.py` `build_arrivals`)
   `rolling.arrivals` 支持三种模式:
   - `mode: poisson`  → 每周期 Poisson(λ) 个(默认,真实随机流)
   - `mode: uniform`  → 每周期 U[low, high] 个
   - `mode: fixed`    → 固定列表(向后兼容旧 `arrivals_per_cycle` 写法)
   随机流以 `experiment.seeds` 中对应 seed 为种子,同配置可复现。
   第 2 周期起每周期新到达数自动补 0 对齐周期下标(修掉此前批次错位 bug)。

3. **多 seed 复现**(`run.py` + `metrics.py`)
   `experiment.seeds: [42,123,777,2024,9093]`;列表仅 1 个元素时退化为单 seed。
   每 seed 重建批次/机队/闭环并同步 solver seed;`metrics.build_seed_summary` 聚合
   `mean±std`(ddof=0)。`plot/seed_summary.py` 新增 3 面板误差棒图(完成率/总代价/总收益)。
   `metrics.json` 多 seed 时含 `multi_seed/seeds/seed_summary/per_seed` 字段。

**窗口校准**:扫描 `(0.5,2.0)=15%`、`(1.0,3.0)=40%`、`(1.5,4.0)=71%`、
`(2.0,5.0)=80%`、`(3.0,6.0)=86%`,选 `(1.5,4.0)` 在真实压力与可行解间取平衡。

### 7. DMDE 放大到 100 种群 / 500 代 + 2D DEM 地理示意图

对齐论文 §5 算法 3.1 的推荐量级,把求解器规模从默认 `(pop=20, gen=40)` 提到
`(population=100, max_iter=500)`(`config.yaml` 的 `solver:` 区块)。代价是单次实验
求解时间显著上升(见下方结果),但收敛更稳定、多 seed 下标准差更小,更适合作为
论文主实验量级。

**新增 2D 地理示意图**(当时写在 `plot/geo_map.py`,现已按“看图目的”拆到
`initial/geo_overview.py` 与 `process/allocation.py`（含逐周期 DEM 演化面板）等,公共件进 `plot/base/dem.py`
+ `plot/base/geo.py`;读 `solution.json` 的 `dem`/`uavs`)：

- `geo_overview`(**图5a 初始态势**):DEM **彩色晕渲**底图(高程配色 + 山体阴影)
  + 机巢(**三角图标**,面积∝容量,标签左右交替防重叠,并标注初始无人机数)
  + 全部任务(圆点,灰显)。回答「场景地形什么样、无人机一开始在哪」。
- `allocation` 的 **(b) 逐周期 DEM 演化**面板:每个滚动周期一列(共享坐标,1×N 横排),DEM 底图上叠加
  本周期航次(起点**四旋翼图标**→任务→终点机巢,有向箭头,任务按承运 UAV 着色)、
  背景任务灰显、终点机巢套同色圆环。UAV 图例合并到全图底部一行,不再压地图。
  直观呈现「每一周期后的变化」。

**现代图标与图例规范**(2026-10-10 定稿,四张 geo 图统一)：

- **矢量字形图标**(`_house_path`/`_drone_path`/`_dot_path`,matplotlib `Path` 多子路径):
  机巢=正三角(克制不花哨,2026-10-12 由房屋改三角,函数名 `_house_path` 沿用)、
  无人机=四旋翼(机身+四旋翼+十字机臂)、任务=圆点;地图点与图例
  共用同一份字形,语义直观。
- **图标化图例**:`_IconPatch`(携带字形的 `Patch` 代理) + `_IconHandler`
  (注册 `handler_map`,在图例框内按原字形绘制),图例不再用抽象几何 marker;
  UAV 色卡也画成四旋翼小图标。
- **图例面板**:白底圆角、淡边框(`fancybox=True, framealpha=0.96`);overview 的
  图例挂图内右上角,多行图的图例统一 `outside lower center`。
- **行标题画进地图内部**(`_panel_title`,左上角白底圆角贴纸样式),替代外置长标题。
- **画幅自适应**(`_row_figsize`):按底图宽高比×行高算 figsize,地图填满每行,
  消除多行图两侧大片留白;fleet 图无色条故 `extra_w` 更窄。

**DEM 晕渲规范**(同样适用于另外两张 geo 图,2026-10-10 定稿)：

- **底图直接采样真实 ASTER 瓦片**( `data/ASTGTMV003_N29E091_dem.tif`,
  `_sample_real_dem`):按场景裁剪 + ~150 m 格网(最长边 ≤1000 px)重采样,
  不再是 solution.json 里 ≤128² 的分块降采样——山脊线细节与真值 tif
  相关系数 0.96,渲染分辨率提升约 40 倍。
- **视图裁剪到瓦片实际覆盖范围**(外扩 ~2.5 km 保证边缘要素完整):本场景
  纬度 28.90–30.04°N,而 N29E091 瓦片仅覆盖 29–30°N/91–92°E,此前整幅
  渲染导致约 1/4 画面落在瓦片外(填最低值出现死色边框/条带伪影)。
  裁剪后只画真实地形;瓦片外残余窄条用最近邻延拓 + 3×3 盒滤波平滑。
- 弃用 `terrain`/`gist_earth` cmap——低段"水蓝/鲜绿"在 3500–5700 m 高原
  会被误读成水系/植被,且高饱和谷地喧宾夺主。改用**低饱和分层设色**
  (自定义 hypsometric `_HYPSO`:灰橄榄谷地 → 卡其 → 赭石 → 棕 → 近白峰顶)。
- `LightSource`(azdeg=315, altdeg=45) 做**山体阴影**(`blend_mode="soft"`,
  `vert_exag=1.8`,dx/dy 取与高程同单位的米制格距——vert_exag 由此反映
  真实坡度,做法参照 matplotlib 官方 *topographic hillshading* 示例)。
- 高程按 **2%–98% 分位拉伸**,避免极值压扁色带;关闭全局网格线(`ax.grid(False)`)
  避免网格压在地形上;视图范围与 DEM 图像共用同一缓存
  (`_dem_view_extent`),严格满幅铺底不露底色。
- 色条 `shrink≈0.5–0.75`,行标题紧凑单行(过长会被右侧色条截断)。
- 真实瓦片采样不可用时(tif 缺失 / 无 local_frame)自动回退 solution.json
  的降采样 dem,图仍成立。

**数据落盘补充**(`metrics.py build_solution`):`solution.json` 现额外写入
`dem`(DEM 高程**分块降采样**到 ≤128×128 + 包围盒,JSON 体积可控)与 `uavs`
(全量 UAV 初始位置 `x/y/z/nest_id/available`)。图因此与数值产物同源、可独立复现
(`plot.render_from_results` 重画无需重跑)。DEM 缺失时底图自动跳过,图仍成立。

### 8. 差分进化算子对齐参考论文(Zhao Ming 等 2017 式 3-9~3-12)

逐条比对本实现与参考论文的算子公式,修正了一处**实质性偏差**:

| 算子 | 论文公式 | 修正前 | 修正后 |
|---|---|---|---|
| 动态交叉率(式 3-9) | `CR = 1-(log t/log S)^h`, h=ζ=3 | 一致 | 一致 |
| 混合差分(式 3-10) | `CR` 驱动 `rand/1` ↔ `best/2` | 一致 | 一致 |
| **缩放因子 F(式 3-11)** | `F = CR` (rand≤CR);`F = (1-CR)/2` (rand>CR) | ❌ `2*CR` / `(2-CR)/2` | ✅ 已改回论文原式 |
| **灭绝率(式 3-12)** | `GMR = 1 - CR`(无 δ 门限) | ⚠️ 多了 `δ` 门限 | ✅ 默认 `δ=None` = 论文原式 |

- **F 的偏差修正**(`operators/scale_factor.py`):原实现第一支多乘 2、第二支分子写成
  `2-CR`,使 F 取值域被放大到 `[0,2]`(高 CR 期 F≈2.0,步长偏激进)。现 F ∈ `[0,1]`。
  scalar 与 batch 两个入口统一按原文判据 `rand ≤ CR`。
- **GMR 的 δ 门限降级为可选**(`operators/extinction.py`、`dmde_solver.DMDEConfig.delta`
  默认改 `None`、`config.yaml` 的 `solver.delta` 改 `null`):论文式 3-12 无 δ,
  现默认行为即原文 `GMR = 1 - CR`;若要"把灭绝推迟到后期"的工程行为,
  在 `solver.delta` 填数值(如 0.3)即可恢复,属显式可选项而非隐式偏离。
- **新增保真度回归测试** `tests/test_operators_fidelity.py`:按论文公式逐条核对
  CR 轨迹/单调性/ζ 语义、F 的取值域与两分支判据、混合差分行为、GMR 单调性与
  早晚期灭绝率分布,共 25 项断言,全过。

### 9. 收敛曲线呈现规范(现并入 process/dmde_search.py)

**现象**:首批结果里周期 2/3 的 `cost_history` 501 代全程恒定(`n_unique=1`),
周期 1 也只在前 ~19 代改进 4 次,若把 500 代全画进 x 轴,有效信息挤在最左侧几个像素,
曲线观感如同"坏掉的平线"。

**定量诊断**(metrics.json 各周期 `muas.n_evals` = 评估器缓存未命中数):

- 任务多的周期(8~9 任务/7~8 架参与)有真实搜索:`n_evals = 2.2万~5.3万`;
- 任务少的周期(≤6 任务/≤5 架)`n_evals = 1~820`,即种群在最初一两代就把
  小规模离散分配空间(数百个解量级)基本覆盖,初始种群最优解此后 500 代无法被超越,
  曲线平线是**诚实的收敛结果**,不是记录 bug(滚动层 `record.cost_history` 原样
  落盘 DMDE 的 best-so-far,链路已核对)。多 seed 交叉验证:平线总是出现在小规模
  周期(如 seed=777 周期 3 仅 1 任务/1 架,`n_evals=1`),与任务规模严格相关。
- 灭绝重启(GMR)救不了小空间:随机重置个体经 `inverse_phi` 量化后仍映射到
  已见过的少数分配上。

**呈现约定**(只改绘图,不改算法/数据):

1. x 轴截断到**有效改进窗口** `max(10, 最后改进代 × 1.3 + 2)`,平尾以
   图内注释"展示至第 N 代;其后至第 500 代各周期均无改进"说明,数据不删减;
2. best-so-far 语义用 `steps-post` 阶梯绘制,改进代加白边圆点标记;
3. 归一化面板中初代即最优(全程 span≈0)的周期不画 0% 平线,改为注释
   "初代即达最优(候选规模小,此后无改进)";右面板图例省略(与左面板共用颜色)。

### 10. 约束开关化 + 惩罚量纲解耦 + 完成反馈闸门

审查「15 个滚动周期仅 2 个最终解可行」时发现两个独立缺陷,本次一并修掉,
并把每类约束做成实验可配置参数。

**(1) 约束开关化(`constraints:` 段)**

`MUASProblemConfig` 新增 `ConstraintConfig`:11 个开关(结构类 / 航程 / 续航 /
时间窗 / 时序 / 同步 / 机巢容量 / 机巢可用性 / 终点机巢可达性)+ 9 个 `w_*`
权重,`check_feasible` 逐类门控。置 false 时该约束既不参与可行性判定、也不
计入惩罚。关闭 `time_window` 时,第一阶段 `selection.feasibility.check_time_window`
同步置 false,保证两阶段口径一致。

顺带补齐一个真实缺口:`end_nest_reachability` 此前只在 `muas_stage` 的改派兜底
里,`check_feasible` 并未校验——现已正式进入可行性判定(末任务→机巢代价非有限
即 `end_nest_unreachable_{nid}`)。

**(2) 惩罚量纲解耦(`muas.w_violation`)**

原 `scalar_fitness = -F1 + λ·F2`,而 `F2 = 路径代价(米) + 惩罚(秒)`。λ=1e-4
是为把米级代价折成奖励当量而设,顺带把秒级惩罚一起压没:迟到 1000 s 只值
0.1 个适应度单位,而单任务奖励 5~10 → 求解器必然「用违约换收益」,这正是
13/15 周期不可行的直接原因。

新增 `w_violation`:给数值时惩罚**不再乘 λ**,直接以「奖励当量 / 惩罚单位」
计入(`f = -F1 + λ·path + w_violation·penalty + w_time·F3`);null 沿用历史口径
以保既有行为与测试不变。0.01 意味着「迟到 300 s ≈ 3 点奖励」。

**(3) 完成反馈闸门(`rolling.completion_feasibility_gate`)**

原 `_advance_execution` 对所有绑定任务无条件 `pool.complete()`,既不读
`solution.feasible` 也不看违约标签 → 迟到任务照样计入完成率。现按违约标签
归因到任务(`tw_task_*` 任务级、`range_uav_*`/`time_uav_*` UAV 级、
`nest_*`/`end_nest_unreachable_*` 机巢级、`tour.feasible=False` 兜底)。

**2026-10-15 协议纠正「迟到不是废飞」**(用户裁定):时间窗违约(`tw_task_*`)
不再是硬违约——它本来就是 `notes §98` 定义的「软违反(惩罚计入适应度,不致命)」,
硬闸门把软违反变成了致命,且与被弹回任务下周期可再选、再赚一次 F1 的奖励口径
自相矛盾。现闸门**只拦物理不可行**(`range_uav_*`/`time_uav_*`/`tour_infeasible_*`/
`unknown_task_*`/机巢与结构类):

- 迟到交付:正常 `pool.complete()`,计入完成与吞吐;迟到秒数记入
  `task.meta["lateness_seconds"]`、`record.late_ids`/`lateness_by_task`;
- 有效奖励按迟到线性折减 `eff = R × max(0, 1 − L/W)`
  (`muas.late_reward_decay_seconds`,W=600s),与 `w_violation` 同向压实按时服务;
- 选择期预筛同步放宽(`selection.feasibility.late_tolerance_seconds`,同值 W):
  只拦「最近机直飞到达 > latest + W」的结构性不可达,「紧但可能迟到」的任务
  交给适应度裁决;
- 物理不可行交付:仍 `fleet.unbind_tasks` + `pool.release` 回池,下周期按
  RELEASE 重新参与选择;记入 `record.infeasible_ids`/`infeasible_tags`,
  汇入 metrics 的 `summary.n_infeasible_delivery`(**废飞只剩这一类**);
- 同一任务同时带 tw 与硬性标签时,硬性优先(仍弹回)。

置 false 即回到历史行为(一律标完成),用于消融对比。

**配套**:`metrics.py` 的 `summary` 新增 `n_late`/`on_time_rate`/`avg_lateness_s`/
`max_lateness_s`(均进 `_SEED_AGG_KEYS`);`correct_exec_rate` 口径改为
「按时出动/总出动」(迟到与物理不可行都不算正确,但迟到计入完成);
`check_time_window_constraint` 返回三元组 `(penalty, tags, lateness)`;
`ConstraintReport.as_details()` 透出 `lateness` 表;新增
`tests/test_late_reward_and_prefilter.py`(折减数值/预筛容忍边界),
`tests/test_completion_gate.py` 翻转旧语义并加混合标签用例。

### 11. 任务撒点域改为 DEM 全域(2026-10-11)

**动机**:任务需求不由机巢位置决定。旧实现把任务锚在机巢 12 km 内,
只覆盖约 26% 的 DEM 面积,盘内任务几乎一定可达 ⇒ 可行性预筛与任务选择
机制没有压力可测,等于人为缩小问题。全域撒点才对应"需求在空间上不确定",
落在能力约束之外的任务本就该被预筛判掉。

**改动**:
- `common/task_scenario.py`:新增 `SyntheticTaskConfig.domain`
  (`dem_extent` 全域 / `nest_anchored` 旧行为,后者保留作消融对照)、
  `dem_inset_m`、`max_reject_trials`;新增 `dem_planar_extent()`
  (DEM 四角 → 局部平面包络)与拒绝重采 `_reject_outside_dem()`
  (经纬度矩形投到 ENU 是梯形,包络约 0.59% 落在 DEM 外)。
  `build_task_batches` 未动,下游零改动。
- `build.py::build_terrain`:网格 extent 改为「机巢包围盒 ∪ 任务域包络 + margin」。
  旧口径只按机巢包围盒,DEM 相对它东向多出 3.0 km ⇒ 约 2.9% 的 DEM 内任务
  会落到网格外、三维代价退化到边界值。`dem_margin_m` 由 20000 降到 2000
  (全域域已由 DEM 包络界定),网格反而从 108×127 km 缩到 102×115 km。
- `methods/selection/pipeline.py`:`diagnostics` 增 `filter_reasons` 淘汰原因
  直方图,用于把「物理不可达」与「算法主动放弃」分开统计。

**实证(5 seed)**:

| 配置 | 完成率 | 收益 | 总代价 |
|---|---|---|---|
| nest_anchored(旧,归档) | 68.2%±5.8% | 81.0 | 193 km |
| dem_extent + 1.5~4.0 周期窗 | 27.4%±6.3% | 38.1 | 129 km |
| dem_extent + 2.5~5.0 | 43.2%±7.9% | 62.4 | 242 km |
| **dem_extent + 3.5~7.0(当前)** | **61.3%±6.2%** | **79.6** | **353 km** |
| dem_extent + 5.0~10.0 | 66.7%±7.1% | 83.0 | 374 km |

- 航程约束**从不 binding**:全域到最近机巢最大 41.7 km,单任务往返 83.4 km
  < 120 km 满电航程 ⇒ 淘汰原因 100% 是 `miss_time_window`,无一例
  `exceed_remaining_range`。
- 1.5~4.0 周期窗(900~2400 s)下 60~90% 的任务被判不可达,这不是"任务难",
  而是**窗口尺度标定错误**:全域典型航时中位 17.8 km/15 m/s ≈ 1190 s ≈ 2 个周期,
  窗口必须 ≥ 典型航时才谈得上"选择性"。故重标定为 3.5~7.0 周期(2100~4200 s),
  此时每周期仅 1~3 个任务因时间窗被判掉,压力真实但不泛滥。
- 产物:旧口径 `results/archive_nest_anchored/`;窗口扫描
  `results/scan_deadline_cycles/scan_dc_*.json`。

## 已知问题与待办

- **禁飞区未实现**(TODO):`cost/vertical_section.py` 只做最小离地余量,
  无 no-fly 实体;`end_nest_reachability` 目前只能反映地形遮挡导致的不可达。
- 任务点是**合成**的;有实测数据后应补 `data/task_location_data.csv` 并替换
  `common.task_scenario.build_synthetic_tasks`(下游无需改动)。
- 事件触发(`rolling.events`)目前只**识别**事件并写进 metrics,尚未实现
  「仅对受影响 UAV 做局部重规划」,仍走全量重算。
- 故障注入链路(`rolling.faults`)已实现但未在默认配置启用;验证 `T_t^release`
  时填 `faults: {2: [3]}` 即可(第 2 周期让 UAV 3 故障)。
- 环境需 `uv sync`:`pyproject.toml` 已补 `numpy` / `pillow` / `matplotlib`
  (DEM 栅格解码为惰性依赖),系统 Python 未装这些包。

## 下一步

- **全链路记录与严格审查**:见同目录 `PIPELINE.md`(配置 → 代码 → 产物 → 图的
  追踪表、两组对照运行结果、方法侧 8 项 + 实验侧 8 项审查结论)。
- **压力场景**(审查 E1,最高优先):当前 24 架机 / 32 泊位下容量、航程、
  Top-αK 预筛**全是惰性的**——`n_uavs=32` + 关 `recharge_at_nest` +
  放大 `spread_m`,让关键约束真正咬合。
- **exp04 消融**(审查 E2):`event_replan` / `w_time` 两个创新点各跑一组开关对照
  (`end_nest_in_encoding` 旋钮已随方案 B 移至 pro 分支);目前无基线,尚不能支撑"优越性"论断。
- **`w_violation` 灵敏度**(审查 E4):0 / 0.002 / 0.01 / 0.05 出「完成率—违约率」
  权衡曲线;并解决单一系数无法同时标定容量罚(1e4/单位)与时间窗罚(秒)的问题。
- **容量灵敏度**:`problem.n_uavs` 调到 32(= 8 巢 × 容量 4,满泊位)即可压出容量约束;
  配合 `rolling.strict_capacity: true` 可验证落地期硬校验不误杀可行解。
- **λ 灵敏度**:`muas.lambda_cost` 决定收益与代价的折算比,直接体现为完成率。
- 对照方法(baselines)就位后,本实验可扩成 exp05 的对比入口。

## 「迟到不是废飞」新协议（2026-10-15 用户裁定 + 2×2 复跑完成）

- 三项决策落地：迟到=完成+奖励折减（W=600s，`problem.py::f1_reward` 读 lateness）；
  物理不可行（range/tour_infeasible/结构）才弹回计废飞（`horizon._advance_execution`
  按 `_HARD_TAGS` 分流）；预筛放宽到 `eta > latest + W`（`late_tolerance_seconds`
  强制与 W 同值）。回归 195 passed。
- 2×2 复跑结论（详见 `ANALYSIS_longhorizon_2026-10-15.md` 附节）：
  废飞 15/9/13/10 → **全 0**；完成 +3~4、淘汰 −3、吞吐 +1.7~3.1%；
  收益 −13~15%（迟到折减 + 放弃弹回重试）；旧协议"100 代全面占优"主要是
  硬闸门假象，新协议下 500 vs 100 打平。
- 新遗留：T52 仍超期淘汰（防饥饿兜底够不着）；迟到压哨现象明显
  （预筛阈值=衰减窗，求解器贴边压哨），W 需做敏感性扫描。

## 物理口径修正（2026-10-15 深夜，用户原则：「任务不被完成只有物理约束被违反」）

- 全路径审计：预筛单程航程漏返程、时间预筛=非物理拒绝（违规）、超期淘汰对
  物理不可达任务=分类错误。T52 实证 DEM 往返 81,096m > 80km 真不可达，
  但旧系统空转 8 周期记「淘汰」。
- 实施（211 passed）：① 预筛往返口径 + margin 默认 1.0（与硬闸门同口径，
  0.95 会误杀 T105/T119——往返 79.9/77.8km 仅剩 135m/2.2km 余量）；
  ② 时间窗预筛默认关（迟到由适应度定价）；③ static_unreachable +
  mark_unreachable + horizon._screen_unreachable；④ metrics n_unreachable，
  恒等式 arrived=done+unreach+elim+remain。
- 结果：淘汰 1/2→0、不可达 1(T52)、g100 完成 132→133；g500 周期 23 出现
  2 次废飞（UAV1 航次 80,986m 越限 985m，闸门拦下弹回、周期 24 完成，
  净损为零；暴露修复链对边际越界航次的缺口）。
- max_iter 结论强化：100 代全面占优（按时率/迟到深度/废飞/等待/求解），
  完成持平。建议论文主口径 max_iter=100，500 代作敏感性对照。
- 详见 ANALYSIS_longhorizon_2026-10-15.md 附二；复跑目录 results_phys、
  results_phys_g100；compare_protocols.py 现含 10 组对比。

## 四层适应度生产化 + 多 seed 稳健性（2026-10-18）

- 四层准字典序适应度定生产（A2，W=1800s）：零奖励交付 7→0、奖励产出率
  88.0%→94.4%、按时率 80.0%→81.7%、航程 −2.2%；完成/废飞/淘汰不变。
  详见 ABLATION_fitness_tiers_2026-10-18.md。
- 审计门禁增强（audit_correctness.py，run.py/run_chunked.py 每次运行末尾自动执行）：
  ① 权重快照 fitness_weights 随周期落盘（治 auto big_m 漂移）；
  ② N 项拆双口径——N1 搜索期自洽（cost_history[-1]==best_fitness）、
  N2 交付解可复算（final_fitness ≡ 成分 + 快照权重重算，rel<0.1%）。
  口径分离原因：Lamarckian 回写只改写起始基因通道、巡游基因保留未修复原值，
  回写后基因解码重建的交付解与搜索期被评价解本不可比（曾经的「记录
  best_fitness 与重算差 6.5~13.6」是两类差异的混合）。实测 3 seed 全部
  活跃周期 N2 通过；交付解适应度不差于曲线终点（+0~+13.7%）。
- 多 seed（42/43/44，各 8 周期/60 任务）：完成 60/60/60、废飞/淘汰/不可达
  全 0、按时率 81.7/83.3/86.7%、正确执行率 0.858/0.871/0.897、奖励产出率
  94.4/95.7/95.9%。seed44 有 2 个迟到 >W 的零奖励交付（最大迟到 2441.8s），
  有效完成率 96.7%——「迟到不是废飞」协议下迟到加深的已知代价。
- 定位（report.tex §1.2）：本实验 = 冒烟 + 正确性验证台 + 口径标定台 +
  回归基线；不承担性能对照（exp05）、长程吞吐（专门滚动实验）、
  选择层规模压缩（exp02）。

## 解码后局部搜索 polish（2026-10-19）

**动机**：DMDE 收敛曲线早平（见下「早收敛五因」），次级目标（路径/迟到）优化动力不足。
用户在 2026-10-18 裁定落地 A 级改进：解码后局部搜索。

**实现**（`methods/muas/solvers/local_search.py::polish_assignment`）：
- 三类移动：relocate（改派任务归属）/ swap（交换两任务归属）/ position（插入位）；
- 每个候选解经与搜索期**同一**修复链（`build_solution`）重建，仅当四层适应度
  **严格改进**（delta > eps=1e-6）才接受；
- `MUASStageConfig` 新增 `polish_enabled / polish_moves / polish_max_rounds=30 /
  polish_max_evals=2000 / polish_eps`；yaml + config.py 同步灌入；
- `solve()` 在 `build_solution(best_assignment)` 后调用，diagnostics 增 `final_*` 与
  扁平 `polish_*` 字段（`polish_rounds/candidates/evals/accepted/gain/
  fitness_before/after/converged/budget_hit`），metrics.py 透传。

**实测（3 seed 复跑 + 审计全 PASS）**：
| seed | 参与周期 | 接受(relocate/swap/position) | 增益(×10⁶) | 收敛 | 预算命中 |
|---|---|---|---|---|---|
| 42 | 3 | 13 / 0 / 0 | 2.56 | 是(3/3) | 0 |
| 43 | 3 | 24 / 1 / 0 | 3.41 | 是(3/3) | 0 |
| 44 | 7(有效3) | 16 / 1 / 0 | 2.34 | 是(7/7) | 0 |
| 合计 | 13 | 53 / 2 / 0 | 8.30 | 全收敛 | 0 |

- relocate 绝对主力（51/53）：SRP 首轮预抢 ~96% 奖励并冻结归属，少数任务落次优机位，
  polish 在解空间级改派到更近 UAV/槽位；
- position 0 次：被修复链 EDD 重排中和（插入位后重排恢复原序）→ 印证「插入位」由重排自动处理；
- 增益集中在周期 1（初始 30 任务批量到达），seed42 单周期增益 1.18×10⁶；
- 所有移动过修复链，未引入物理违约，审计 N2 仍全通过；
- 247 tests 全绿（236 既有 + 11 `test_polish_local_search.py`）。

**早收敛五因**（report.tex §2.6 已写）：① big-M 阶梯函数主导（步长 ~2.8e5 vs 路径 ~173）；
② SRP 编码首轮预抢 ~96% 奖励；③ Lamarckian 回写冻结归属（闲置槽=死维度）；
④ 差分算子对翻盘收益（λ×27.9km≈2.8 单位）盲；⑤ 尾部毫厘震荡（极差 0.008%）。
**分工结论**：DMDE 负责「对」（可行交付结构），polish 负责「好」（次级目标精修）。

## memetic M1 精英档案（2026-10-19）

**动机**：post-hoc polish 把路由改进留到 DMDE 收敛后；用户裁定「让 DMDE 搜索期内也优化路由」，
落地 M1（精英档案）：把精英 polish 前移进 DE 进化循环。

**实现**：
- `dmde_solver.py`：`DMDEConfig` 加通用代际钩子 `gen_callback(gen, best_individual)`，
  每代收敛记录后调用；默认 None ⇒ 纯 DMDE 逐位不变；钩子异常被吞（不破坏确定性）。
- `muas_stage.py::run_selective_muas`：开 `polish_in_loop` 后每 `memetic_K` 代对当代精英跑一次
  polish（从紧预算 `memetic_max_rounds=6 / memetic_max_evals=500`，first-improvement 早停+缓存，
  实测仅 +7% 耗时），维护 best-polished 档案（assignment/fitness/solution）；
  轨迹按代记录（长度 == len(cost_history)，供「搜索期内路由在降」出图）。
  **不改基因型**（M1 无反映射）；交付解取「收敛后 polish」与「档案最优」的更优者。
- 开关默认全关（基线不动）；`memetic_K=20`、`polish_in_loop` 经 yaml/config.py 灌入。

**关键不变量（已测）**：同种子下开/关的 DMDE 曲线 `cost_history` **逐位相同**——钩子 RNG-free、
不改群体，故纯 DMDE 搜索期轨迹不变（`test_memetic_inloop.py` 锁死）。

**三 seed 实测（memetic vs 基线 post-polish；审计全 PASS：22/23、22/23、22/24）**：
| seed | 完成 | 按时率 | 迟到 | 总奖励 | memetic 接受移动 | 档案增益 | 末次改进代 |
|---|---|---|---|---|---|---|---|
| 42 | 60→60 | 88.3→91.7% | 7→5 | 324.23→328.35 | 79 | 8.09e5 | 60/60/100/20 |
| 43 | 60→60 | 96.7→98.3% | 2→1 | 350.46→351.13 | 79 | 3.40e5 | 60/60/40 |
| 44 | 60→60 | 90.0→90.0% | 6→6 | 279.57→279.57 | 73 | 6.63e4 | 40/60/20×5 |

- 均值：按时率 91.7→93.3%（+1.7pp）、迟到 15→12、奖励 954.3→959.0；完成/废飞/淘汰/不可达全不变。
- **「搜索期内路由在降」证据**：档案末次改进代多为 40–100（K=20 的 polish 点），非仅 gen100，
  即 DE 循环中段档案已被持续压低——对冲「收敛曲线早平」。
- seed44 无 headline 变化：周期 4–7 每周期仅 1 任务（单任务归属已最优，无路由空间），
  档案增益（6.6e4）主要是降路径不改按时/奖励；交付解与基线等价（择优未更优）。
- 255 tests 全绿（247 + 8 `test_memetic_inloop.py`）。

**M2（Lamarckian 反射写回）**：让路由改进回流 DE 群体（真 memetic）。卡在 SRP 编码未必能表示
任意 polished assignment 的反映射问题，改动大、回归风险高，**本次不碰**，写入报告「讨论与展望/后续工作」。

## 当前 commit 全新多 seed 复跑（2026-10-20 深夜，惰性表优化后 consolidated）

**动机**：10-18/10-19 的阶梯数据产出后，管线又经两项改动（① 惰性代价表 LazyPairwiseCost，
② memetic 修复 28835c1），需确认既有数值仍有效、并补齐缺失格（旧 N 格只有 s42）。
9 格全部以**当前 commit** 重跑（`run_chunked.py --seed 42/43/44`，逐周期原子存档续跑），
新目录 `results_ms_{nopolish,prod,memetic}_s{42,43,44}`。

**Bit-exact 对照（7/9 格有旧值可比）**：

| 格 | 旧值(10-18/19) | 新值(now) | 结论 |
|---|---|---|---|
| N s42 | 81.7% / late 11 / 313.1 / 1734.5km | 同 | **逐位一致** ✔ |
| P s42/s43/s44 | 88.3 / 96.7 / 90.0% | 同 | **逐位一致** ✔ |
| M s42/s43/s44 | 91.7 / 98.3 / 90.0% | 同 | **逐位一致** ✔ |
| N s43/s44 | （旧缺） | 83.3 / 86.7% | **补缺** |

即：惰性表改动在全规模端到端确认 bit-exact（与 exp02 子集验证互为独立证据）；
memetic 修复 28835c1 对成功路径是 no-op。旧数据全部有效，无需作废。

**多 seed 阶梯总表（n=3，mean±std；完成/废飞/淘汰/不可达全部格恒 60/0/0/0）**：

| 指标 | N 无抛光 | P 生产(polish) | M memetic | P−N | M−P |
|---|---|---|---|---|---|
| 按时率 % | 83.9±2.1 | 91.7±3.6 | 93.3±3.6 | **+7.8** | +1.7 |
| 迟到任务数 | 9.7±1.2 | 5.0±2.2 | 4.0±2.2 | −4.7 | −1.0 |
| 平均迟到深度 s | 736±142 | 732±20 | 575±133 | −4 | **−158** |
| 总奖励 | 309.8±24.1 | 318.1±29.3 | 319.7±29.8 | +8.3 | +1.6 |
| 总航程 km | 1723.7±31.0 | 1693.0±52.6 | 1662.9±34.4 | −30.7 | −30.1 |
| 求解耗时 s | 639±25 | 595±23 | 642±32 | −44 | +47 |

**每 seed 增益（方向一致性检验）**：

| seed | polish Δontime | memetic Δontime | 备注 |
|---|---|---|---|
| 42 | +6.7pp | +3.3pp | 两级增益均显著 |
| 43 | +13.3pp | +1.7pp | polish 增益最大格 |
| 44 | +3.3pp | **+0.0pp** | memetic 零增益，见下 |

- polish 增益方向 3/3 一致但幅度差 4 倍（+3.3~+13.3pp）：后验抛光能修多少取决于
  DMDE 收敛解离局部最优多远，seed 间方差大是预期内的（单 seed 结论不可外推）。
- **memetic 零增益异常（s44）复现确认**：M s44 与 P s44 **逐位相同**
  （90.0% / late 6 / 279.6 / 1624.8km）。机制：s44 周期 4–7 每周期仅 1 任务，
  单任务归属已最优、无路由改进空间，精英档案从未收到优于「收敛后 polish」的解
  （档案增益 6.6e4 只降路径不改按时/奖励，择优时被持平规则拒收）。
  即 M1 增益是 seed-dependent 的，论文应报「+0~+3.3pp，均值 +1.7pp」而非单点数字。
- **吞吐口径备注**：`throughput_per_cycle=done/总周期数` 受收尾惯例与排程曲线形状影响
  （run.py 8 周期 vs run_chunked 7 周期；polish 使消耗前置：s42/s43 活跃周期 4→3，
  s44 因到达分布分散尾周期拉长至 11）。跨格比吞吐应用**每活跃周期**口径
  （N 15.0 / P 8.6~20 / M 同 P）或只报完成数；本实验所有格 done=60/60=100%，
  吞吐差异纯属簿记，非方法能力差异。
- 求解耗时三格相当（~595~660s）：polish/memetic 的额外评估被更早收敛部分抵消，
  M 格 +47s 相对 P 是档案维护与紧预算 polish 的开销（memetic_K=20）。

**结论**：优化后管线数值稳定性确认（7 格逐位复现 + 2 格补缺），
N→P→M 阶梯在 3 seed 上方向一致（按时率 +7.8/+1.7pp、迟到 −4.7/−1.0、航程 −31/−30km），
可作为论文生产口径的多 seed 证据；memetic 的 seed 依赖性（含零增益机制）已定性。

