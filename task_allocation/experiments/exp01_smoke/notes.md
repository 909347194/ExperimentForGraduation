# exp01_smoke

**status:** active（端到端已跑通）

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
├── plot/         # 可视化模块(代码)——按"看图目的"分四层(论文叙事线)
│   ├── __init__.py    # make_plots() 调度 + 从 results/ 直接重画
│   ├── registry.py    # ★ 图清单(唯一事实来源):16 张图,新增 = 加一行
│   ├── theme.py       # 学术绘图样式、中文字体、保存约定(唯一 import pyplot 处)
│   ├── base/          # 被图依赖但自己不是图的绘制件
│   │   ├── dem.py     #   DEM 取数/晕渲/分层设色
│   │   └── geo.py     #   图标图例、UAV 配色与航段、行式排版(_row_figsize)
│   ├── setup/         # ① 实验设置:问题规模/算法超参/约束开关(参数即图)
│   ├── initial/       # ② 初始状态:地形/机巢/初始机队/首批任务(与算法无关)
│   ├── process/       # ③ 求解过程:滚动闭环逐周期"发生了什么"(6 张)
│   └── report/        # ④ 结果分析:跨 seed 结论与配置对照(可直接进论文)
└── results/      # 数值产物
    ├── metrics.json    # 数值指标(不含分配结构)
    ├── run_meta.json   # seed / 时间戳 / 配置路径 / DEM 网格
    ├── solution.json   # 任务表 + 机巢表 + 各周期航次与收敛轨迹 + dem + uavs
    └── figures/        # 图产物:16 张 × {png, pdf}
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

## 口径（最终版，与代码一致）

### 场景与任务

- 机巢 CSV + DEM（ASTER 瓦片裁剪重采样 ~150 m 格网）；任务合成撒点域
  `tasks.domain=dem_extent`（DEM 全域均匀撒点），时间窗周期相关
  （`latest = 到达时刻 + U(4.5,9.0)×600s`，≈2700~5400s；依据统一 DEM
  三维航程口径后的重标定，见 `config.yaml` 的 `tasks:` 段实测扫描）。
- **到达口径（2026-10-22 用户裁定：fixed-60 冒烟）**：`n_cycles: 60` 只是
  安全上限，`arrivals.mode: fixed, counts: [10, 20]`（初始 30 + 周期 2/3 各
  10/20 = 共 60 个任务），周期 4 起有意无新到达，靠 `stop_when_stable`
  （patience=4）早停 —— **实测约 7 周期收敛**。
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
- `λ_cost=1e-2`（2026-10-22 用户裁定，由 1e-4 上调）、`w_violation=0.01`
  （惩罚不再乘 λ，直接以奖励当量计入）。
- **覆盖门禁双开关已显式登记在 `config.yaml` 的 `muas:` 段**（2026-10-23），
  当前值均为 `false`（= 本段上述四层口径）：
  `coverage_gate` 终态构造性修复 / `lexicographic_coverage` 罚层+审计分账；
  完整门禁口径 = 两者同开，此时 `f` 去掉 `−m·F1`，`F1` 只作事后指标
  （文档/论文主口径，见 `docs/method_audit_vs_refs_2026-10-23.md` 与
  `experiments/OBJECTIVE_REFRAME_2026-10-22.md`）。
  ⚠️ 门禁开启的实测代价（E1/E2，2026-10-23）：完成 60/60 → 57.0±2.45、淘汰
  0 → 3，根因是可行域被成本压满后丧失装载余量；故门禁属**待修缺口**，
  在缺口闭合前不进生产口径。

### 求解器

- DMDE：`population=50, max_iter=100`（主口径，500 代仅作敏感性对照）；
  算子对齐参考论文（Zhao Ming 等 2017）式 3-9~3-12；`delta=null` = 论文原式。
  ⚠️ 代数不是「越大越好」：2026-10-21 的 T9 诊断显示 miss 是编码顺序的
  人为依赖（SRP 反映射锁归属 + Lamarckian 只回写起始基因），与搜索预算无关，
  100/500 代对照中 **100 代占优**（500 代单周期耗时 ×5，收益不稳）。
  另注：CR 与温度的调度都以总代数 S 为分母，S 由原文的 1000 压到 100 是
  **搜索预算的量级变化**，论文须作为标定项显式声明（详见
  `docs/method_audit_vs_refs_2026-10-23.md` 的 F8/F9）。
- 解码后局部搜索 polish（relocate / swap / position，四道门：航程 / 自身窗 /
  迟到数不增 / 成本严格降）；memetic M1 精英档案（搜索期内 polish，默认关）。
- 覆盖优先两段式（`coverage_first`）：先按覆盖目标装到最大可行集，再在不
  减少已装集合约束下做成本 polish。
- 任务 ID ≠ `self.tasks` 下标（滚动 t.id 全局计数，映射用 `self._task_ids`）。

### 指标口径

- 恒等式：`arrived = done + unreach + elim + remain`。
- `correct_exec_rate` 是逐周期未加权均值；论文按时率用池化 `on_time_rate`。
- 正确执行率 = 按时出动 / 总出动（迟到计入完成、不计正确；物理不可行计废飞）。

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
| 命令行覆盖(不改 yaml) | `run.py --seeds 42 43 44` / `42-46`(区间)/ `42,43`(逗号,可混用);`--no-plots` | 多种子长跑的调试入口 |
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
6. `make_plots: true` 时产出 16 张图 × 2 种格式，无缺字告警。
7. 运行末尾 `audit_correctness.py` 全 PASS（N1 搜索期自洽 / N2 交付解可复算 / F 池水位守恒）。
8. `run_meta.json` 的 `config_fingerprint` 与本次生效配置一致（换 λ / 代数 /
   门禁开关后指纹必变，产物可按指纹回溯口径）。

> 出图补充（2026-10-23 实测）：`plot.formats` 默认 `[png]`（矢量图需 `--pdf`；
> GIF 在本次图集含动画图时自动补）。16 张图中有 5 张带前置条件 —— 3 张动画图
> 需多周期 tours，`seed_summary` 需多 seed，`ab_compare` 需 ≥2 组对照 json；
> 条件不满足时打印「跳过（数据不满足前置条件）」，属预期行为，不是失败。

## 图产物（results/figures/）

| 层 | 图 id | 内容 | 数据来源 |
|---|---|---|---|
| setup | `setup_params` | 实验设定总览：问题规模 / DMDE 超参 / 选择机制 / 约束开关 / 滚动设定 | `config.yaml` 全段 + `solution.json` 的 nests / tasks / uavs |
| initial | `geo_overview` | 初始态势：地形 · 机巢 · 初始机队 · 任务散布 | `solution.json` 的 dem / nests / tasks / uavs |
| process | `task_selection` | 动态任务选择：漏斗逐级收敛 / 保留率 / 能力上限 / 池守恒 | `metrics.json` 的 cycles[].selection 全字段 / muas.n_selected |
| process | `dmde_search` | DMDE 搜索机制：动态交叉率 CR / 探索—开发平衡 / 收敛曲线 / 求解开销 | `solution.json` 的 cycles[].cost_history + `config.yaml` 的 solver.zeta |
| process | `allocation` | 单周期分配结构：航迹条（线长 ∝ 航程）/ 逐周期空间演化 | `solution.json` 的 cycles[].tours + tasks[] / nests[] / dem |
| process | `allocation_gantt` | 分配航迹条：线长 ∝ 真实航程 · 方块=任务 · 两端起降巢 | `solution.json` 的 cycles[].tours + tasks[] / nests[] |
| process | `allocation_map` | 逐周期空间演化：DEM + 航次回放 1×N | `solution.json` 的 cycles[].tours + tasks[] / nests[] / dem |
| process | `allocation_anim` | 分配过程动画演变：滚动闭环航迹生长 / 任务点亮 + 当前周期收敛（GIF） | `solution.json` 的 cycles[].tours + cost_history + tasks[] / nests[] / dem |
| process | `allocation_status` | 机队再平衡：各巢驻巢无人机数演化 + 返航巢迁移弧线（GIF） | `solution.json` 的 cycles[].tours（uav_id / start_nest_id / end_nest_id）+ nests |
| process | `allocation_anim_map` | 分配过程动画（仅地图 · 高清大图，GIF） | `solution.json` 的 cycles[].tours + tasks[] / nests[] / dem |
| process | `task_dynamics` | 任务命运：池水位 / 流量 / 状态矩阵 / 等待与滞留 / 地理生命周期 | `metrics.json` 的 cycles[].pool_counts / completed_ids / deferred_ids + `solution.json` 的 tasks[] |
| process | `fleet_state` | 机队与机巢：三口径出动 / 占用热力 / 选巢归因 / 机队迁移 | `metrics.json` 的 cycles[].muas / nest_occupancy + `solution.json` 的 tours / nests |
| process | `constraint_state` | 约束与时限：预筛归因 / 约束状态 / 罚项量纲 / 时间窗 | `metrics.json` 的 cycles[].selection.filter_reasons / muas.{violations,penalty,infeasible_tags} + `solution.json` 的 tasks[] |
| report | `outcome_summary` | 结果总览：完成率 CR 轨迹 / 未完成任务终局构成 / 双目标 / 关键指标 | `metrics.json` 的 cycles[] / summary + per_seed |
| report | `seed_summary` | 跨 seed 稳健性：三指标均值±σ / 分布箱线 / 逐 seed 演化 / 明细表 | `metrics.json` 的 per_seed / seed_summary / seeds(仅多 seed 运行) |
| report | `ab_compare` | 两组配置对照：完成率 / 收益—代价 / 权衡 / Δ% | `results/metrics_<tag>.json`(需 ≥2 组对照) |

每张图同时出 `png`（预览）与 `pdf`（矢量，直接进 LaTeX）；中文字体 Noto Sans CJK SC。

## 已知问题与待办

- **禁飞区未实现**(TODO)：`cost/vertical_section.py` 只做最小离地余量，无 no-fly 实体。
- 任务点是**合成**的；有实测数据后应补 `data/task_location_data.csv` 并替换
  `common.task_scenario.build_synthetic_tasks`（下游无需改动）。
- 事件触发(`rolling.events`)目前只**识别**事件并写进 metrics，尚未实现
  「仅对受影响 UAV 做局部重规划」，仍走全量重算（exp03 主题）。
- 故障注入链路(`rolling.faults`)已实现但未在默认配置启用。

## 变更记录

### 2026-10-23「代码不对应」审查与修复

旧结果已整体清空（旧数据产自 λ=1e-4 的旧四层口径，与新口径不可比），
本次同步把实验与当前代码对齐。四类不对应及其处置：

| # | 不对应 | 性质 | 处置 |
|---|---|---|---|
| 1 | `muas.lexicographic` / `muas.big_m` 写在 yaml 里，但 `config.py` 从未传给 `MUASProblemConfig`；而 `plot/setup/setup_params.py` 直接读 yaml 标注「字典序/加权和」 | **图会说谎**（改 yaml 算法不动、图却变） | `config.py` 显式透传（取值与 dataclass 默认一致 ⇒ 行为零改变，仅消灭分裂） |
| 2 | `solver.max_iter` 注释写「上调至 500」，取值 100，且未纳入 2026-10-21 反向结论 | 文档漂移 | 注释改写为主口径 100 + T9 miss 机制依据，500 降为敏感性对照 |
| 3 | `muas:` 段缺 2026-10-22/23 新增的门禁三开关（`coverage_gate` / `lexicographic_coverage` / `coverage_first`），消融只能靠 `--set` | 配置不再是唯一事实来源 | 显式登记（值 false = 当前生产口径），并写明门禁与 `prune_unprofitable` 的冲突 |
| 4 | `method:` 段无人读取；`rolling` 段注释讲「24 周期 + Poisson」而取值是 fixed-60 | 死段 / 注释与取值不自洽 | `method:` 标注为元信息；`rolling` 注释改为 fixed-60 冒烟口径并说明长程归 exp06 |
| 5 | `run_meta.json` 只有 `config_path`，同目录产物无法区分配置版本（曾因口径混淆整体清空过一次） | 可追溯性缺口 | 加 `config_fingerprint`（与 exp05 同口径 sha256[:16]） |
| 6 | 本文（notes）口径段：n_cycles 24+poisson、population 100、时间窗 3.5~7.0、图 16×2 格式 | 文档漂移 | 全部对齐 yaml 实际值与实测行为 |
| 7 | `plot/__init__.py` 与 `plot/base/__init__.py` 的包内导入被批量替换脚本误改成三点/四点相对导入（`from ...plot import registry`、`from ....plot.base import dem`） | **任何一次出图必 ImportError**（`beyond top-level package`） | 改回单点相对导入（`from . import ...`），与同文件其它相对导入一致，两种导入身份（顶层 `plot` / 绝对 `task_allocation...plot`）均验证通过 |

配套：`experiment.seeds` 由调试态单 seed `[42]` 改为正式 3 seed `[42, 43, 44]`。

### 本次运行结果（2026-10-23，3 seed 42/43/44，`config_fingerprint=b689c69716f4e121`）

| 指标 | 值 |
|---|---|
| 完成 / 到达 | 60.00±0.00 / 60（100%±0%） |
| 淘汰 / 不可达 / 池内剩余 | 0 / 0 / 0 |
| 按时率（池化） | 0.9278±0.0283 |
| 迟到交付 | 4.33±1.70 个，均 679 s（代表 seed） |
| 总航程 | 1611 km（代表 seed），path/完成 26.9 km |
| 求解耗时 | 593 s/seed（≈10 min），早停于周期 7~10（安全上限 60） |
| 审计 | 22/23 PASS，1 WARN（M 段长方向性差异 ~0.5%，已知低危） |

与历史 1e-4 口径数据（航程 1693±64 km）不可直接比：λ 已改 1e-2、门禁指标
新增、`s_coverage_rate` 等新字段入表 —— 本次为**新口径首份 3 seed 基线**。
（该份数据仍在 `results/`，指纹 `b689c69716f4e121`，λ=1e-2。）

### 2026-10-24 代码逻辑审查与修复（详见 `docs/exp01_code_audit_2026-10-24.md`）

用户裁定 `muas.lambda_cost: 1e-2 → 1e-1`（本实验口径切换），并对审查发现的
缺陷做修复。λ 三档不可混用：1e-4（1693.0 km / 28.22 km每完成 / 0.917）、
1e-2（1672.1±44.1 / 27.87 / 0.9278）、1e-1（本次，`results_lambda1e-1/`）。

| # | 缺陷 | 性质 | 处置 |
|---|---|---|---|
| 1 | **多 seed 只审计 1/3**：`audit_correctness.py` 只读顶层 `cycles` + 代表 seed 的 `solution.json`，从不读 `per_seed`；而 `per_seed` 无 tours ⇒ D/J/K/M/N2 对其余 seed 结构性不可审计 | 门禁失效（3 seed 只有 1 份带正确性凭据） | `run.py` 逐 seed 落 `results/seed_{id}/{metrics,solution}.json`；审计 `_discover_targets` 遍历 + `_merge` 合并同名项（须全 PASS），详情标注失败 seed |
| 2 | 审计判据吃不到 `--set`：`w_decay`/`max_ratio`/`service` 读 `config.yaml` 原文，而 `run_meta` 指纹含 `--set` | 判据与指纹分裂（10-23 只修了门禁判据一处） | `run.py` 落 `effective_config.json`；审计 `_load_effective_config` 优先读它、缺则回退 yaml |
| 3 | `--group` 合法值 `scene/process/report`，而图集实际分组是 `setup/initial/process/report`；`resolve` 对未知分层**抛 ValueError** | **照 PIPELINE.md 示例执行会在求解后崩溃、审计被跳过** | choices 改为四个真分组名；`make_plots` 空图集打印警告；`PIPELINE.md:102` 示例同步修正 |
| 4 | `deferred_delivery_ratio` 分子含废飞（`executed ⊋ completed`），分母是 `completed` | 废飞非 0 时虚高甚至 >1（本跑废飞=0 未暴露） | 分子限定 `completed_ids` |
| 5 | `total_cost` 从 f2 反解（`f2 − w_penalty·penalty`）而非直读 `final_path_m` | 现测逐位相等（差 0.000000）**不是 bug**，但依赖未写进代码的隐含前提 | 改直读；反解保留作交叉校验，残差落盘 `path_split_residual_m` |
| 6 | `setup_params` 图仍读 yaml 原文标「双目标口径 / 罚权」 | 图会说谎（第二次，10-23 只修算法侧） | `metrics.problem` 补 `lambda_cost/w_violation/w_penalty/w_time` 自描述；图优先读 metrics、缺则回退 |
| 7 | `wall_seconds` 事后回填（`build_metrics` 时恒 0） | 顺序一改退回历史 bug I7 | 移入 `_run_once` 内在 `build_metrics` 之前写入 |
| 8 | `config_fingerprint` 含 `experiment.seed`/`seeds`/`solver.seed` | 同口径换 seed 指纹不同，无法按指纹归类 | 指纹剔除 seed 类字段 |
| 9 | `--seeds 1-3-5` 抛裸 `ValueError` | 用户只见 traceback | 转 `argparse.ArgumentTypeError` 并给示例 |
| 10 | 文档：称「23 项」（实际 22~24 浮动）、图写 `plot/figures/`（实际 `results/figures`） | 文档漂移 | 改为「20+ 项，条数随数据形态浮动」+ 目录改正 |

修复后：全量测试 305 passed；2 seed 冒烟验证逐目录审计合并（22/23 + 1 WARN，
详情标注 `seed_43`）；`--seeds` 非法输入给出友好报错。

### 2026-10-24 交付后成本 polish（诊断报告 §9 排序 1+2，详见 `docs/fitness_path_optimization_diagnosis_2026-10-24.md` §10）

用户裁定「覆盖优先 + 航程可优化」。实施 `post_cost_polish`：DMDE 收敛后对
**交付解**做组内 2-opt/Or-opt 重排，四道门（航程 / 迟到不增 / 执行集由
构造不变 / 成本严格降），默认开启（`muas.cost_polish_enabled: true`，
另两个参数 `cost_polish_max_rounds: 30` / `cost_polish_eps: 1.0`）。

**冒烟 A/B 结论：两臂输出逐位相同（零接受）。** 逐航程调试显示交付顺序
已是 2-opt 局部最优（最佳反转候选全部更差）；与生产 `polish_assignment`
的活动量互相印证——`position`（机内换序）在所有周期零接受，余量在
`relocate`（归属维度，每周期接受 4~10 次）。即：**exp01 口径下（每机
1.6~2 个任务）顺序维度无余量，该 polish 表现为恒等操作**；价值在长航程
口径（积压域）才显现。正式 3 seed A/B 见 `results_lambda1e-1_cp/` vs
`results_lambda1e-1/`。

## 下一步

- **exp04 消融**：`event_replan` / `w_time` 两个创新点各跑一组开关对照。
- **exp05 基线对照**：对照方法(baselines)就位后，本实验可扩成 exp05 的对比入口。
- 对照方法就位后，本实验只保留冒烟 + 正确性 + 回归基线职责。
