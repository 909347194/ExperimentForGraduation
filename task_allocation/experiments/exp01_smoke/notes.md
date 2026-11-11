# exp01_smoke

**status:** active（当前代码在 `guangzhou` 的正式档已跑完，2026-11-10；
结论与对账见 `docs/exp01_current_code_reconcile_2026-11-10.md` 与定稿 §11.14）

## 目的

- 验证**完整链路**在真实地理场景上可跑：机巢 CSV + DEM → 任务/机队 → 两阶段求解 → 滚动闭环 → 指标落盘 → 出图。
- 调用真实求解器（DMDE + 内层最小费用流），不是目录与配置联调。
- **不**作为算法性能结论（规模小、任务为合成）；性能对比见 exp05。
- 定位：冒烟 + 正确性验证台 + 口径标定台 + 回归基线。

## 目录结构

```
exp01_smoke/
├── run.py        # 编排调度(唯一入口):装配 → 求解 → 落盘 → 出图 → 审计
├── config.py     # config.yaml → 各模块配置对象(MUAS / DMDE / Horizon / …)
├── build.py      # 场景 / DEM 地形 / 机队 / 任务批次装配
├── metrics.py    # 指标汇总、运行元信息、分配结果、终端报表
├── audit_correctness.py  # 运行末尾自动执行:守恒/可行/口径自洽审计
├── paths.py      # 目录布局与 sys.path 引导
├── config.yaml   # 全部可调参数
├── plot/         # 可视化模块(代码)——按"看图目的"分三层(论文叙事线)
│   ├── __init__.py    # make_plots() 调度 + 从 results/ 直接重画
│   ├── registry.py    # ★ 图清单(唯一事实来源):5 张图,新增 = 加一行
│   ├── theme.py       # 学术绘图样式、中文字体、保存约定(唯一 import pyplot 处)
│   ├── base/          # 被图依赖但自己不是图的绘制件(7 个)
│   │   ├── scene.py      #   场景数据定位(config.yaml 的 data.*)唯一入口 + 本地坐标系/引导
│   │   ├── buildings.py  #   建筑背景层(shapefile 载入/裁剪/按高度着色)
│   │   ├── backdrop.py   #   背景判断只做一次:有建筑→建筑 / 仅地形→DEM / 皆无→纯底
│   │   ├── dem.py        #   DEM 取数/晕渲/分层设色
│   │   ├── geo.py        #   图标图例、UAV 配色与航段、行式排版(_row_figsize)
│   │   ├── declutter.py  #   标签去重叠(save_figure 调用)
│   │   └── audit.py      #   版面自检 bbox 溢出(check_bbox_overflow 调用)
│   ├── initial/       # ① 初始状态:地形/机巢/初始机队/首批任务(1 张:geo_overview)
│   ├── process/       # ② 求解过程:滚动闭环逐周期"发生了什么"(3 张:allocation / allocation_gantt / allocation_anim)
│   └── report/        # ③ 结果分析:总体结果(1 张:outcome_summary,可直接进论文)
└── results/      # 数值产物
    ├── metrics.json    # 数值指标(不含分配结构)
    ├── run_meta.json   # seed / 时间戳 / 配置路径 / DEM 网格
    ├── solution.json   # 任务表 + 机巢表 + 各周期航次与收敛轨迹 + dem + uavs
    └── figures/        # 图产物:5 张(I01/P03/P04/P06/R01)
```

**拆分原则**：`run.py` 只做编排，不堆细节。产物按「数值 vs 结构」分流：
`metrics.json` 只留数值指标，`tours` / `cost_history` 这类结构性数据进
`solution.json`，供绘图与论文取数。`plot/` **只负责绘制**——图存哪由
`experiment.figures_dir` 决定（约定在 `results/figures/`）。

## 运行

```bash
uv sync                                                     # 首次:建 .venv 装 numpy / pillow / pyyaml / matplotlib
.venv/Scripts/python.exe task_allocation/experiments/exp01_smoke/run.py
# 或不走 uv:python3 task_allocation/experiments/exp01_smoke/run.py
```

只想重画图（不重跑实验）：

```bash
python3 -c "import sys; sys.path.insert(0,'task_allocation/experiments/exp01_smoke'); \
from plot import render_from_results as r; r('task_allocation/experiments/exp01_smoke/results', 'task_allocation/experiments/exp01_smoke/plot/figures')"
```

## 口径（设计，与代码一致）

### 场景与任务

- 机巢 CSV + DEM（ASTER 瓦片裁剪重采样 ~150 m 格网）；任务合成撒点域
  `tasks.domain=dem_extent`（DEM 全域均匀撒点），时间窗周期相关
  （`latest = 到达时刻 + U(4.5,9.0)×600s`，≈2700~5400s）。
- **到达口径（fixed-60 冒烟）**：`n_cycles: 60` 只是
  安全上限，`arrivals.mode: fixed, counts: [5, 5]`（初始 20 + 周期 2/3 各
  5/5 = 共 30 个任务，用户由 20/10/20=50 调整为 20/5/5=30），周期 4 起
  有意无新到达。收敛早停（口径约定）为「池空」自然收敛：实测 3 seed 均于
  **周期 4** 自然停机（`stop_reason=pool_empty`，详见 config.yaml 注释）。
  长程「持续到达」的吞吐口径由 `exp06_joint_regime`（Poisson 流）承担；
  本实验是「链路正确 + 口径标定 + 回归基线」台，不是吞吐实验台。
  （`fixed` 的 counts 长度必须 = n_cycles−1；本配置故意只给 2 项。）

### 适应度（四层准字典序，生产口径）

```
f = W_hard·n_hard + w_exc·excess_m − m·F1_eff + w_late·L_tot + λ·path + w_time·F3
```

- T0 物理违约层压倒一切；`W=1800`（迟到 1800 s 归零）。
- 迟到不是废飞：时间窗违约是软违反（惩罚计入适应度、不致命），有效奖励
  线性折减 `eff = R × max(0, 1 − L/W)`；物理不可行（range/tour_infeasible/
  结构类）才弹回计废飞。
- `λ_cost=1e-1`（本实验当前配置值）、`w_violation=0.01`
  （惩罚以「奖励当量/秒」直接计入，不乘 λ）。
- **覆盖门禁双开关**（`coverage_gate` / `lexicographic_coverage`）在 `config.yaml`
  的 `muas:` 段显式登记，当前值均为 `false`（= 上述四层口径）。
  门禁开启属**待修项**，在缺口闭合前不进生产口径。

### 求解器

- DMDE：`population=50, max_iter=100`（主口径；500 代仅作敏感性对照）；
  算子对齐参考论文（Zhao Ming 等 2017）式 3-9~3-12；`delta=null` = 论文原式。
- 解码后局部搜索 polish（relocate / swap / position，四道门：航程 / 自身窗 /
  迟到数不增 / 成本严格降）；memetic M1 精英档案（搜索期内 polish）。
- 覆盖优先两段式（`coverage_first`）：先按覆盖目标装到最大可行集，再在不
  减少已装集合约束下做成本 polish。
- 任务 ID ≠ `self.tasks` 下标（滚动 t.id 全局计数，映射用 `self._task_ids`）。

### 指标口径（审查清理后）

exp01 的定位是**正确性验证台 + 口径标定台**（冒烟 + 审计 + 回归基线），
不产出论文主表数值（主表由 exp05 summary.csv 承担）。因此只保留口径严格
自洽的指标，近义冗余口径直接删除、不做兼容更名：

- 恒等式：`arrived = done + unreach + elim + remain`。
- **完成率只留 `completion_rate_final`**（η_final = 按时交付 ∪ 迟到交付 / 最终分母；
  最终分母 = 到达 − 物理不可达 × 折减系数）。早期 naive 的 `completion_rate`
  （分母含物理不可达）已删除——它系统性低估完成率且无对应论文口径。
- **按时率两个口径并存、各有名字**：
  - `correct_exec_rate`：逐周期未加权均值（正确执行率 = 按时出动 / 总出动，
    迟到计入完成、不计正确；物理不可行计废飞）——过程口径；
  - `on_time_rate_final`：交付集条件率（按时交付 / 全部交付，迟到交付计入分母
    不按时计入分子）——结果口径，与 exp05/exp06 同键对账。
  - 早期按出动池化的 `on_time_rate` 已删除（把「执行质量」与「交付结果」
    混为一谈，是审查发现的同名不同义陷阱）。
- `effective_completion_rate` 分母已与 `completion_rate_final` 对齐
  （同为最终分母），二者可直接相除比较；零奖励交付 `n_zero_reward_delivery`
  作为分子扣除项单列。
- `infeasible_wasted_path_m`：废飞（物理不可行航次）已飞航程，按航次任务数
  比例归属（`est_cost × n_bad/n_bound`）——B1 审查新增，量化「装上了但飞不到」
  的代价。
- `cand_hit_rate`：候选池命中率 = Σselection.n_selected / Σcapability_n_cand，
  验证「候选池该富」的 P14 声明（选择阶段保留率）。
- `max_backlog` 跨 seed 聚合取**峰值语义**（mean 位 = max、std = 0），不是
  逐周期池化的均值。

### 航程记账口径（论文引用声明）

1. 地形跟随航程是**有向**距离：d(A,B) ≠ d(B,A)（爬升规则自起点扫描）。这是
   模型固有性质，非缺陷；全链路（代价矩阵 / ETA / 记账 / 审计）均按**实际
   飞行方向**取值。
2. 建筑障碍包络为**离散**查询，路径微移（毫米级）若跨过建筑边界会使航程跳变
   米级。属离散化固有性质；记账与复算必须用**同一精度**的坐标（故落盘不再舍入）。

（修复过程与实测影响见 notes_archive.md「航程记账口径修正」。）

## 时序（任务执行顺序）约束（落地）

**背景（为什么之前等于没有）**：`muas.constraints.sequence` 开关自
起就是 `true`，但 `Task` 数据类**从未提供** `sequence_group` 字段 ⇒
`check_sequence_constraint` 恒返回 0。开关开着、判据跑着、约束从未生效。

**现在补齐的三件事**

1. **数据字段**：`Task.sequence_group`（链标识，`None` = 无顺序约束）+
   `Task.sequence_order`（链内序号）。带默认值追加在字段表末尾 ⇒ 既有
   构造与既有基线逐位不变。
2. **判据**：`check_sequence_constraint` 改为按 `sequence_order` 判序（旧
   实现拿**任务 id 大小**当顺序，与 id 分配方式耦合）。同一航次内同链任务
   必须按序号升序，违背量 = 逆序对个数，经 `ConstraintReport.add_seq` 进
   **硬违约桶**（与航程/续航同级判废，不是软惩罚）。
   - 顺带修掉一个潜伏 bug：`feasibility.py` 传的是**标签列表**而 `add_seq`
     按单字符串 `append` ⇒ `violations` 会出现嵌套列表 `[['seq_group_7']]`，
     下游按标签前缀归因会解析不到。因时序恒 0 从未触发。
3. **构造性修复**：`decode_repair._repair_sequence`（挂在
   `_reorder_for_deadlines` 之后、航程闸之前）+ `_best_order` 的候选序过滤
   + 门禁后终态兜底。只调序、不增删任务，链外任务相对位置不动（最小位移）。

**口径边界（论文引用须如实声明）**：**只判同一条航次内**。同一链的任务若被
拆给不同无人机或不同周期，本模型不判违约——跨机/跨周期须按绝对完成时刻比对，
尚未实现。注意（口径约定）「不判违约」不等于「允许拆开完成」：
滚动调度层另把同一链当不可拆单元，本周期要么整组一起完成、要么整组一起
暂缓（见 `methods/rolling/sequence_group.py`），两者层次不同、不互相替代。

**开关与数据**：`muas.constraints.sequence`（默认 true）+ `tasks.sequence_chain_ratio`
（默认 **0.0 = 关闭**，保证既有实验基线逐位不变）。开启后链内成员取「随机种子
+ 空间最近邻」，**不移动撒点**，只贴标签。

**验证**：`tests/test_sequence_constraint.py` 18 项（判据 / 硬桶 / 开关 / 修复 /
生成）全通过；端到端（`--set tasks.sequence_chain_ratio=0.3`，3 seed，小预算）
30 任务中 6 个带链、3 条链，出现 2 个「同航次含同链 ≥2 成员」的真实检验场景，
**交付解逆序 0**，完成 30/30。产物 `solution.json` 的 `tasks[]` 已补
`sequence_group` / `sequence_order`（原 `_task_row` 手工挑字段会丢，事后无法审计）。

**已知遗留**：小预算（pop20/gen30）下审计 J 项「有序 estimate 重算 = 记录
est_cost（容差 1m）」FAIL，差 1.4~2.5 m（0.03%~0.07%）。**非本次改动引入**——
同预算关闭时序链的对照组同样 FAIL，而改动前的主口径产物（gen200）J 项 PASS。
根因是 M 项已记录的「对称缓存 vs 有序」方向性差异，只是 J 用绝对 1 m 容差
比 M 的相对 0.1% 更严。**未擅自放宽判据**，待确认。


## 参数放哪里

| 内容 | 位置 | 理由 |
|---|---|---|
| 机巢经纬度、DEM | `task_allocation/data/<scenario>/` | 实测事实,跨实验复用 |
| 机巢个数、容量、机队规模、航程、速度 | `config.yaml` 的 `problem:` | 实验设定 |
| 任务规模、散布、收益、时间窗 | `config.yaml` 的 `tasks:` | 任务当前为合成,后续换 CSV 时参数可平移 |
| 第一阶段权重与阈值 | `config.yaml` 的 `selection:` | 做 α / 五项权重灵敏度时只改这里 |
| 第二阶段模型与剔除策略 | `config.yaml` 的 `muas:` | λ、选择性剔除、等待补偿、终点进编码、Lamarckian 回写、门禁开关 |
| DMDE 超参 | `config.yaml` 的 `solver:` | 种群、代数、ζ；`delta` 留空 = 论文式 3-12 原式 |
| 三维代价 / DEM 网格步长 | `config.yaml` 的 `cost:` | 网格步长直接决定一次性开销 |
| 滚动周期、到达、故障注入 | `config.yaml` 的 `rolling:` | `arrivals: {mode: poisson\|uniform\|fixed, …}` 随机到达流；含 `strict_capacity` 硬校验开关 |
| 多 seed 复现 | `config.yaml` 的 `experiment.seeds` | 多次随机重复取均值±标准差(论文稳健性证据);仅 1 个元素时退化为单 seed |
| 命令行覆盖(不改 yaml) | `run.py --seeds 42 43 44` / `42-46`(区间)/ `42,43`(逗号,可混用);`--no-plots`;`--set muas.xxx=yyy` | 多种子长跑的调试入口 |
| 图格式、分辨率 | `config.yaml` 的 `plot:` | 代码在 `plot/`,产物路径在 `experiment.figures_dir` |
| 约束开关与惩罚权重 | `config.yaml` 的 `constraints:` | 11 个开关 + 9 个权重,置 false 即该约束既不判可行也不计惩罚(消融/口径取舍) |
| 惩罚量纲解耦 | `config.yaml` 的 `muas.w_violation` | null = 惩罚随 F2 乘 λ;给数值则惩罚以「奖励当量/惩罚单位」直接计入 |
| 完成反馈闸门 | `config.yaml` 的 `rolling.completion_feasibility_gate` | true = 物理不可行任务不顺延完成、回池重选（迟到交付正常完成） |
| 迟到奖励折减 | `config.yaml` 的 `muas.late_reward_decay_seconds` | 晚到任务有效奖励 `eff = R × max(0, 1 − L/W)`；须与 `selection.feasibility.late_tolerance_seconds` 同值 |

**原则**：`methods/` 不读配置、不写死数值；所有数值由 `config.py` 从 yaml 取出后传入。

## 成功标准

1. `python run.py` 跑完不抛异常，写出 `results/metrics.json`、`results/run_meta.json`、`results/solution.json`。
2. `metrics.json` 中记录 8 个机巢的米制坐标 (x, y) 与 DEM 采样高程 z。
3. 每个周期的 `check_feasible` 无 violation。
4. `T_t^exec ⊆ T_t^sel ⊆ T_t`、`U_t^exec ⊆ U_t^avail` 恒成立。
5. 恒等式 `arrived = done + unreach + elim + remain` 每周期成立（审计自动核对）。
6. `make_plots: true` 时产出 5 张图（I01 / P03 / P04 / P06 / R01），无缺字告警。
7. 运行末尾 `audit_correctness.py` 全 PASS（N1 搜索期自洽 / N2 交付解可复算 / F 池水位守恒）。
8. `run_meta.json` 的 `config_fingerprint` 与本次生效配置一致（换 λ / 代数 /
   门禁开关后指纹必变，产物可按指纹回溯口径）。

> 出图约定：`plot.formats` 默认 `[png]`（矢量图需 `--pdf`；GIF 在图集含动画图时
> 自动补）。5 张图中 `allocation_anim`（P06）为动画图（需多周期 tours），条件不满足时
> 打印「跳过（数据不满足前置条件）」，属预期行为，不是失败。

## 图产物（results/figures/）

| 层 | 图 id | 内容 | 数据来源 |
|---|---|---|---|
| initial | `geo_overview`(I01) | 初始态势：背景(建筑/地形) · 机巢 · 初始机队 · 任务散布 | `solution.json` 的 dem / nests / tasks / uavs + `data/<scenario>/building/*.shp` |
| process | `allocation`(P03) | 分配结构与逐周期空间演化：甘特航迹条 + 背景底图逐周期演化（只画有真实航次的活跃周期） | `solution.json` 的 cycles[].tours + tasks[] / nests[] / dem |
| process | `allocation_gantt`(P04) | 分配航迹条：线长 ∝ 真实航程 · 方块=任务 · 两端起降巢 | `solution.json` 的 cycles[].tours + tasks[] / nests[] |
| process | `allocation_anim`(P06) | 分配过程动画演变：滚动闭环航迹生长 / 任务点亮 + 当前周期收敛 + 机巢驻巢数实时标注（GIF） | `solution.json` 的 cycles[].tours + cost_history + tasks[] / nests[] / dem |
| report | `outcome_summary`(R01) | 结果总览：完成率 CR 轨迹 / 未完成任务终局构成 / 双目标 / 关键指标 | `metrics.json` 的 cycles[] / summary + per_seed |

（图件沿革：裁剪 16 张 → 4 张，gif 只保留 P06、P04 甘特图追加保留；
P03 `allocation` 与 P04 并存接入注册表，共 5 张。被裁图件见 git 历史。）

每张图同时出 `png`（预览）与 `pdf`（矢量，直接进 LaTeX）；中文字体 Noto Sans CJK SC。


## 当前代码正式档重跑与论文数字对账（2026-11-10）

> 完整版见 `docs/exp01_current_code_reconcile_2026-11-10.md`、定稿 §11.14。
> 本节只留本实验取数要点；以下数值均为**当前 `results/` 批次**（3 seed）。

- 跑批：`python -u run.py --no-plots`，`FORMAL_RC=0`、17m57s、严格串行；
  3 seed 均周期 4 `pool_empty` 自然收敛；末尾自动审计 **26/26 PASS、无 WARN**。
  11-05 旧批次完整归档到
  `experiments/_archive/exp01_smoke_results_2026-11-05_pre_current_code/`（未删数据）。
- **当前基线**（`metrics.json` 的 `seed_summary`，不是顶层 `summary`）：
  完成 30.00±0.00 / 完成率 1.000±0.000 / 按时率 **0.9778±0.0157** /
  总航程 42953.63±2570.25 m / 每完成航程 1431.79±85.67 m /
  迟到 0.667±0.471 / 求解 342691±14910 ms；淘汰、硬违约、服务不可达全 0。
- **历史主表数字作废**：旧主报告 §2 的「按时 0.7695±0.0218、每完成航程
  24.22±0.64 km」属 8 巢 / 80 km / 60 任务口径，`55f4b5b` 切换口径后已删除；
  当前为 guangzhou 3 巢 / 10 km / 30 任务，**跨口径禁比、禁引用**。
- 与 11-05 同题批次（初始机队 / 任务 / 机巢 / `fitness_weights` 逐位一致）相比：
  航程 +41.3 / +692.1 / −2435.4 m，按时率 1.000→0.9667（2 seed 各 1 个迟到），
  求解耗时 +10~16%；终点机巢分配与解码期 polish 动作数明显变化。
  因 11-05 之后有**多处并发代码改动**，此差异**不能单因素归因**、
  **禁做跨版本配对 t 检验**、禁写「L3b/MCF 带来收益」。
- 口径提醒：`f = 0.1·path + 0.01·late_s`（9 周期残差 0，线性同源）；
  `total_f2 = path + penalty` 混加米与秒，**禁当航程**；
  `n_repair_failed`（24/21/34）是搜索期候选诊断计数，`delivery_refused` 全 false。
- **禁写「当前代码完成率恒 100%」**：exp05 v3 在 `lasa_chengguan` 的 seed 43
  仍有可完成任务遗留（早停判据待修）；本场景 3 seed 100% 不可外推。
- 本次 `--no-plots`，`results/` 无 `figures/`；旧图在归档目录、属旧批次，
  不可配新数字使用。

## 图、表产物（2026-11-10 重跑）

- 求解与出图/出表分离：跑批用 `--no-plots`；求解完成后单独执行
  `python render_from_json.py png,pdf` 补 `results/figures/`，
  `python build_tables.py` 生成 `results/tables/`。
- `build_tables.py` 是本实验自持的表格入口：从 `results/metrics.json` 的
  `seed_summary` / `per_seed` **单源生成** `exp01_main_results.csv`、
  `exp01_main_results.tex`（booktabs 三线表）、
  `exp01_per_seed.csv`（逐 seed 全指标明细）；脚本内不内置任何实测值。
- 主表不列 `total_f2`（米+秒混量纲，禁当航程）；完成率分母排除 UNREACHABLE；
  墙钟只作观测。**表格数值一律以本次重跑的 `metrics.json` 为准**，
  不沿用上一节旧批次数字；两批次若逐位一致属复现，若不一致须逐格说明、
  禁跨版本配对 t 检验。

## 机队配置与异构口径（2026-11-11 起）

- 机队改由 `uav_config.json`（`problem.fleet_file`）逐机装配：3 巢 6 机混编
  （2×25 km 远程 / 3×6 km 近程 / 1×12 km 中程），初始机巢逐机显式给定、
  满电出巢；不设 fleet_file 即回同构（n_uavs + uav_*），行为逐位不变。
  schema 与语义见 `docs/design_fleet_spec_2026-11-11.md`。
- ⚠️ **跨口径禁比**：10 km 同构基线的历史数字不适用于本机队（完成率/航程/
  迟到均受机队构成影响），对照须按本口径重跑基线。
- 审计 per-UAV 化：`audit_correctness.py` 的 D/J/J2 逐机取
  speed/max_range/max_time（`solution.json` 的 `uavs[]` 新增性能字段；
  旧产物缺字段时回退全局缺省，历史结果仍可审）。异构冒烟审计 26/26 PASS。

## 已知问题与待办

- **禁飞区未实现**(TODO)：`cost/vertical_section.py` 只做最小离地余量，无 no-fly 实体。
- 任务点是**合成**的；有实测数据后应补 `data/task_location_data.csv` 并替换
  `common.task_scenario.build_synthetic_tasks`（下游无需改动）。
- 事件触发(`rolling.events`)目前只**识别**事件并写进 metrics，尚未实现
  「仅对受影响 UAV 做局部重规划」，仍走全量重算（exp03 主题，见
  `exp03_event_rolling/notes.md`）。
- 故障注入链路(`rolling.faults`)已实现但未在默认配置启用。
- 休眠年龄未单独落盘：任务池已有 `dormant_time` 元数据，未做汇总指标
  （对账 C3 项，见 `docs/modification_plan_20261104_hetero_claims.md`）。

## 下一步

> 2026-11-11 更新：原「exp04 消融 / exp05 基线对照就位」两行已过期
> （exp04_ablation、exp05_baselines 均已建；exp05 v3 主张检验台已出正式档），
> 现对齐 `docs/modification_plan_20261104_hetero_claims.md` 的 WP-D：

- **exp03 落地**（D1）：run.py / config.yaml + E3 机巢不可用注入面。
- **exp05 终点巢策略臂**（D2）：固定返原巢 / 最近合法巢 / 贪心改派 / MCF 最优。
- **异构域扫描**（D3）：异构度（航程极差比）→ 完成率 / 选择行为 / 休眠率。
  机队配置走 `task_allocation/common/fleet_spec.py`（JSON）：
  设计见 `docs/design_fleet_spec_2026-11-11.md`。
- **同构回归对账**（D4）：exp01 3-seed vs 历史数据。
- 本实验长期定位不变：冒烟 + 正确性验证台 + 口径标定台 + 回归基线。

## 历史归档

过程性工作记录（配置清理审计、标定史、一次性脚本清理留档、航程记账修正、
M1 A/B、代码卫生收敛）已按时间迁至 **`notes_archive.md`**——只作回溯留档，
不作现行口径引用；现行口径以本文与 `PIPELINE.md` 为准。
