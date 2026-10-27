# exp01_smoke

**status:** active（设计 / 配置就绪，待运行）

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
│   ├── registry.py    # ★ 图清单(唯一事实来源):4 张图,新增 = 加一行
│   ├── theme.py       # 学术绘图样式、中文字体、保存约定(唯一 import pyplot 处)
│   ├── base/          # 被图依赖但自己不是图的绘制件
│   │   ├── dem.py     #   DEM 取数/晕渲/分层设色
│   │   └── geo.py     #   图标图例、UAV 配色与航段、行式排版(_row_figsize)
│   ├── initial/       # ① 初始状态:地形/机巢/初始机队/首批任务(与算法无关)
│   ├── process/       # ② 求解过程:滚动闭环逐周期"发生了什么"(1 张)
│   └── report/        # ③ 结果分析:总体结果(可直接进论文)
└── results/      # 数值产物
    ├── metrics.json    # 数值指标(不含分配结构)
    ├── run_meta.json   # seed / 时间戳 / 配置路径 / DEM 网格
    ├── solution.json   # 任务表 + 机巢表 + 各周期航次与收敛轨迹 + dem + uavs
    └── figures/        # 图产物:4 张(2026-10-26 图件裁剪后)
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
  安全上限，`arrivals.mode: fixed, counts: [10, 20]`（初始 30 + 周期 2/3 各
  10/20 = 共 60 个任务），周期 4 起有意无新到达，靠 `stop_when_stable`
  （patience=4）早停。
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

### 指标口径（2026-10-24 审查清理后）

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

## 参数放哪里

| 内容 | 位置 | 理由 |
|---|---|---|
| 机巢经纬度、DEM | `task_allocation/data/` | 实测事实,跨实验复用 |
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
6. `make_plots: true` 时产出 4 张图（I01 / P04 / P06 / R01），无缺字告警。
7. 运行末尾 `audit_correctness.py` 全 PASS（N1 搜索期自洽 / N2 交付解可复算 / F 池水位守恒）。
8. `run_meta.json` 的 `config_fingerprint` 与本次生效配置一致（换 λ / 代数 /
   门禁开关后指纹必变，产物可按指纹回溯口径）。

> 出图约定：`plot.formats` 默认 `[png]`（矢量图需 `--pdf`；GIF 在图集含动画图时
> 自动补）。4 张图中 `allocation_anim`（P06）为动画图（需多周期 tours），条件不满足时
> 打印「跳过（数据不满足前置条件）」，属预期行为，不是失败。

## 图产物（results/figures/）

| 层 | 图 id | 内容 | 数据来源 |
|---|---|---|---|
| initial | `geo_overview` | 初始态势：地形 · 机巢 · 初始机队 · 任务散布 | `solution.json` 的 dem / nests / tasks / uavs |
| process | `allocation_gantt` | 分配航迹条：线长 ∝ 真实航程 · 方块=任务 · 两端起降巢 | `solution.json` 的 cycles[].tours + tasks[] / nests[] |
| process | `allocation_anim` | 分配过程动画演变：滚动闭环航迹生长 / 任务点亮 + 当前周期收敛 + 机巢驻巢数实时标注（GIF） | `solution.json` 的 cycles[].tours + cost_history + tasks[] / nests[] / dem |
| report | `outcome_summary` | 结果总览：完成率 CR 轨迹 / 未完成任务终局构成 / 双目标 / 关键指标 | `metrics.json` 的 cycles[] / summary + per_seed |

（2026-10-26 图件裁剪：16 张 → 4 张，gif 只保留 P06，P04 甘特图追加保留；被裁图件见 git 历史。）

每张图同时出 `png`（预览）与 `pdf`（矢量，直接进 LaTeX）；中文字体 Noto Sans CJK SC。

## 已知问题与待办

- **禁飞区未实现**(TODO)：`cost/vertical_section.py` 只做最小离地余量，无 no-fly 实体。
- 任务点是**合成**的；有实测数据后应补 `data/task_location_data.csv` 并替换
  `common.task_scenario.build_synthetic_tasks`（下游无需改动）。
- 事件触发(`rolling.events`)目前只**识别**事件并写进 metrics，尚未实现
  「仅对受影响 UAV 做局部重规划」，仍走全量重算（exp03 主题）。
- 故障注入链路(`rolling.faults`)已实现但未在默认配置启用。

## 下一步

- **exp04 消融**：`event_replan` / `w_time` 两个创新点各跑一组开关对照。
- **exp05 基线对照**：对照方法(baselines)就位后，本实验可扩成 exp05 的对比入口。
- 对照方法就位后，本实验只保留冒烟 + 正确性 + 回归基线职责。

---

## 配置清理与审计（2026-10-27）

> 目的：config.yaml 只保留**参与装配且影响行为**的键；死键删除、历史标定史
> 迁出到本文件归档。验证：清理后 exp01 --seeds 42 复跑 `solution.json`
> 与清理前 bit-exact（行为逐位不变）；`config_fingerprint` 会因键字面变化
> 而改变（语义正确：配置字面确实变了，但行为口径未变）。

### 删除的死键 / 冗余键清单

| 键 | 判定 | 依据 |
|---|---|---|
| `method:` 整段（selection/muas/rolling 3 键） | 死键（纯元信息） | config.py 与全部代码均不读该段；真正的开关在 selection/muas/rolling 各段 |
| `rolling.enabled` | 死键 | 无任何代码消费；滚动闭环是唯一运行形态，周期数由 n_cycles + stop_when_stable 控制 |
| `solver.seed` | 冗余键 | run.py 逐 seed 覆盖 `solver.seed = experiment.seeds[i]`（`seed_cfg.setdefault("solver",{})["seed"]=seed`）；直接装配时也回退到 experiment.seed |
| `muas.w_nest_capacity` | 冗余键（死字段） | 巢容量惩罚的唯一生效处是 `constraints.w_nest_capacity`（feasibility.py 只读 cons.w_nest_capacity）；MUASProblemConfig.w_nest_capacity 仅作旧 yaml 的回退源 |
| `tasks.source` | 死键 | 只有 data.source 被消费（build.py:78）；任务合成与否由生成函数与 domain 决定 |
| `tasks.anchor_to_nests` | 冗余键（遗留别名） | 仅 domain=nest_anchored 时参与（task_scenario._resolve_domain/_anchors）；本实验 domain=dem_extent 下不生效 |

### 保留但已失效/仅消融的键（勿误删）

- `muas.lexicographic`：非门禁 big-M 路径移除后唯一残余作用 = 经济剔除总闸
  之一（economic_prune = prune_unprofitable ∧ ¬coverage_first ∧ ¬lexicographic）；
  当前 prune_unprofitable=false ⇒ 无行为影响。
- `muas.prune_*`（margin/max_rounds/wait_bonus/wait_scale）：仅 prune 开启时
  参与；wait_bonus/wait_scale 还参与 evaluator 的 _eff_reward（恒算）。
- `selection.capability.max_expand_factor`：仅 mode=primary 生效。

### 时间窗标定史（自 config.yaml 迁出）

任务时间窗 = 到达时刻 + U(deadline_cycles_min, deadline_cycles_max) × cycle_length；
标定原则：窗口尺度与「典型任务航时」同量级——太紧则时间窗不是筛选而是在把
多数任务判成物理不可能，太松则时间窗失效、失去选择性压力。

1. **domain=dem_extent 后标定（大区域时代）**：全域撒点后到最近机巢中位
   17.8 km @15 m/s ≈ 1190 s ≈ 2 周期，p90 29.5 km ≈ 1970 s ≈ 3.3 周期。
   5 seed 扫描（results/scan_deadline_cycles/）：
   - 1.5~4.0 周期 → 完成率 27.4%±6.3%，淘汰 100% miss_time_window；
   - 2.5~5.0 → 43.2%±7.9%；
   - 3.5~7.0 → 61.3%±6.2%（当时默认：仍有选择性压力）；
   - 5.0~10.0 → 66.7%±7.1%（接近锚定机巢基线 68.2%，时间窗基本失效；
     68.2% 是「人为缩小问题」的上限，不作为目标值）。
2. **统一 DEM 三维航程口径后重标定（2026-10-13）**：距离口径统一后旧窗口
   3.5~7.0 完成率跌至 75%（20 任务直飞全部来得及、物理不可达=0，但 ETA 变长
   使多任务航次后段任务迟到回池，下周期被预筛淘汰——链式死亡）。按实测航时
   重算（seed=42，最近机巢直飞 DEM 航程 @15 m/s）：中位 1377 s ≈ 2.29 周期、
   p90 ≈ 3.2、最大 2274 s ≈ 3.79（T10）；下界 = (最大直飞航时 + 服务 300 s) × 1.05
   ≈ 2703 s ≈ 4.5 周期，上界保持 2:1 → 9.0。扫描：3.5~7.0 → 75%；
   4.2~8.4 → 95%；4.5~9.0 → 95%（当时默认，平台期任取）；
   5.0~10.0 → 100% 但零淘汰（时间窗失效），代价 +16%、求解 +57%，不取。
3. **城关区口径重标定（2026-10-26，当前值）**：旧 4.5~9.0 按旧大区域最大直飞
   2274 s 标定，在城关区（最大单程 ~12.4 km ≈ 870 s @15 m/s）明显过松。
   新下界 = (870 s + 300 s) × 1.05 ≈ 1230 s ≈ 2.0 周期；上界保持 2:1 → 4.0。
   扫描留档 scan_wc*.log / results_scan_wc/。

### 求解预算标定史（自 config.yaml 迁出）

max_generations 主口径 100（2026-10-21 定，500 代为敏感性对照）。早期注释
曾写「由 100 上调至 500（依据 Ming 2017 标定）」：依据是原文 pop50/gen1000、
Fig.10 自述约 150 代收敛，本实现 temperature = 1 − gen/Gen、随机匹配预算
0.9×Gen ⇒ 100 代仅抵原文 900 代，探索预算差一个量级；实测 500 代确有余量
（同实例同种子 path 485 → 381 km）。但 2026-10-21 T9 诊断推翻了「加代数即可
治 miss」：miss 是编码顺序的人为依赖（SRP 反映射锁归属 + Lamarckian 只回写
起始基因 ⇒ 归属逐代复现），与搜索预算无关；100/500 代对照中 100 代占优
（500 代收益不稳，单周期耗时约 ×5，exp01 单 seed 全程 80 s → 约 7 min）。
故主口径回到 100，500 代仅在敏感性分析里出现。
