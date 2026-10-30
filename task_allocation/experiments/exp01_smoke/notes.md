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

（图件沿革：2026-10-26 裁剪 16 张 → 4 张，gif 只保留 P06、P04 甘特图追加保留；
2026-10-29 P03 `allocation` 与 P04 并存接入注册表，共 5 张。被裁图件见 git 历史。）

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

## 2026-10-29 临时脚本清理 + DE vs 随机同预算裁决留档

一次性脚本清理（`git rm`；需要时从 git 历史恢复）：

- `compare_gen100_200_seeds.py` / `plot_convergence_compare.py` / `plot_seed_robustness.py`
  及两张一次性对照图——gen100/200 三种子对照的产出，结论已固化在 config.yaml
  `solver.max_iter` 注释与「求解预算标定史」；
- `probe_de_vs_random.py` 及 4 份 trace json（final 与 _partial 逐字节相同，纯重复）；
- `scan_wc.sh`（写死本机绝对路径的一次性时间窗扫描脚本；扫描留档见「时间窗标定史」）。

DE vs 随机同预算裁决（seed 42，删除前从 trace 提取留档；两臂同评估预算、同
解码+覆盖门禁+polish 管线，完整滚动链路 4 周期）：

| 周期 | DE 首→末 f | 随机同预算最优 f | DE 末 vs 随机 |
|------|-----------|------------------|---------------|
| 0 | 16679.6 → 13759.6 | 14616.3 | −5.9% |
| 1 | 13972.2 → 11841.9 | 12690.2 | −6.7% |
| 2 | 17910.8 → 16123.9 | 16181.7 | −0.4% |
| 3 | 8998.8 → 8781.8 | 8884.1 | −1.2% |

裁决：**搜索有效**——每周期 DE 均持续改进（首→末），同预算下不劣于随机（3/4 周期
明显更优）；但优势幅度中等（周期 2 基本持平），与「λ 牵引小 ⇒ 增益有限」的担忧
一致。论文叙事宜写「真实优化、增益稳健但不悬殊」，勿夸大。

---

## 时序（任务执行顺序）约束（2026-10-29 落地）

**背景（为什么之前等于没有）**：`muas.constraints.sequence` 开关自 2026-10-18
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
尚未实现。

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
比 M 的相对 0.1% 更严。**未擅自放宽判据**，待裁定。

---

## 航程记账口径修正（2026-10-29，审计 J 项由假 FAIL 转真 PASS）

**J 项（逐腿有序重算 = 记录 est_cost，容差 1 m）长期 FAIL，差 1.4~2.5 m
（0.03%~0.07%），且方向不定**（seed_42 记账偏大、seed_43 偏小）。此前被记为
「低危口径差异 / 待裁定」。本轮定位为**三处独立实现缺陷**，全部修复；
**判据一字未动**，现为严格 PASS（25/25）。

### ① `CostProvider.cost` 的两条静默回退（最严重）
`methods/muas/cost/provider.py`：正向键缺失时先查**反向键** `(b,a)` 直接返回，
再缺失则退**欧氏直线** `_euclid`。
- 垂直切面航程**方向相关**（`_generate_flight_path` 自起点逐点递推爬升），
  反向值是**另一个飞行方向**的数 ⇒ 拿它给 `a→b` 记账是口径错误，不是近似；
- 退欧氏则直接丢掉 DEM 三维航程，爬升段凭空消失、系统性低估。
- 两者都**静默**：不报错不告警，既污染上报航程（论文指标）又污染 `λ·path`
  目标项（搜索方向）。
- 修复：缺键一律按 **a→b 正向**现算，有 DEM 走垂直切面，无 DEM 才退欧氏。

### ② `make_distance_fn` 缓存键对称化
`methods/muas/cost/vertical_section.py`：键被写成 `(ka,kb) if ka<=kb else (kb,ka)`，
于是同一条腿反向查询**直接命中正向值** ⇒ ETA / 航迹时间用的是错误飞行方向的
航程。修复：DEM 分支改**有序键**（欧氏分支天然对称，保留对称化省内存）。

### ③ 落盘坐标 round 到 3 位（J 项差异的直接来源）
`horizon._tour_row` / `metrics._nest_rows` / `metrics._task_row` 把 xyz round
到 3 位，而运行端 pairwise 用**精确**坐标。两端仅差 0.0002~0.0004 m，但
**建筑障碍包络是离散查询**（采样点是否落在某建筑多边形内）⇒ 微小偏移可让某
采样点跨过建筑边界 ⇒ 障碍高度跳变几十米 ⇒ **单腿航程跳变 1.2 m**。
- 这解释了差异「方向不定」（取决于采样点落在边界哪侧），也解释了旧 gen200
  产物恰好 PASS——那条腿的采样点没落在边界上，**纯属运气**。
- 实证：cycle2/uav0 两腿，运行端 1623.2329 / 1630.9426（合计 3254.1755 =
  记录值），审计端用 round 坐标得 1622.003 / 1629.712（合计 3251.715）。
- 修复：三处坐标落盘**不做 round**，保留完整精度。

### 附带修复
`make_distance_fn` 的坐标量化（0.1 m）取消：量化坐标与估算器直算的原始坐标
不是同一个点，剖面采样位置偏移 ⇒ 同一条边两种调用路径给出不同值。取消后
命中率不变（节点坐标在周期内固定），而**距离值唯一确定**。

### 验证
- 全量测试 **369 passed**（新增 4 项：方向感知缓存、回退不复用反向值、
  回退走 DEM 不退欧氏、命中路径不受影响）。
- 端到端（pop20/gen30，3 seed）审计 **25/25 全通过**：J / M / O / D / K / L 全 PASS，
  M 项由「WARN 级已知差异」升格为**回归守卫**（ETA 与代价矩阵均有序，恒 0 差异）。
- **主口径结果已重跑刷新**（pop50/gen200，3 seed）审计同样 **25/25 全通过**。
  距离口径改变会改变数值，旧 `results/` 归档至
  `experiments/_archive/exp01_pre_directionfix_20261029/`，**不得再用于论文取数**。

### 对实验数字的实际影响（主口径重跑实测）

| seed | 旧航程 total_path_m | 新航程 | 变化 | 旧按时率 | 新按时率 |
|---|---|---|---|---|---|
| 42 | 44909.0 | 46293.7 | +3.08% | 0.867 | **1.000** |
| 43 | 39619.8 | 41725.8 | +5.32% | 0.933 | **1.000** |
| 44 | 37781.4 | 42543.4 | **+12.60%** | 0.600 | **1.000** |

- **航程系统性被低估 3%~13%**：退欧氏直线会凭空抹掉爬升段，拿反向值则用了
  另一个方向的数——两者都让记账航程偏小。
- **按时率由 0.867/0.933/0.600 升到全 1.000**。机理：ETA 此前用被低估的航程
  ⇒ 时间窗把「其实来不及」的任务判成来得及（`test_distance_unification.py`
  文档开头就写明这个失效模式）；修正后判据恢复真实，求解器不再做纸面可行的
  承诺，迟到随之归零。**结论方向不变（方法依然全完成），但旧数字不可引用。**
- 完成率恒 1.000（正确性不变量）不受影响。
- **波及面**：exp02 / exp05 / exp06 共用同一套 `methods/` 代价口径，其既有
  `results/` 同样是缺陷口径下的产物，**论文主表（exp05）须重跑**再取数。

### 论文口径声明要点
1. 地形跟随航程是**有向**距离：d(A,B) ≠ d(B,A)（爬升规则自起点扫描）。这是
   模型固有性质，非缺陷；全链路（代价矩阵 / ETA / 记账 / 审计）均按**实际
   飞行方向**取值。
2. 建筑障碍包络为**离散**查询，路径微移（毫米级）若跨过建筑边界会使航程跳变
   米级。属离散化固有性质；记账与复算必须用**同一精度**的坐标（故落盘不再舍入）。
