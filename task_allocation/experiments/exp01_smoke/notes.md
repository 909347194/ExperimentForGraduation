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
├── plot/         # 可视化模块(代码)
│   ├── __init__.py    # make_plots() 调度 + 从 results/ 直接重画
│   ├── theme.py       # 全局样式、中文字体、保存约定
│   ├── cycles.py      # 图1 周期指标面板
│   ├── selection.py   # 图2 选择漏斗
│   ├── assignment.py  # 图3 分配地图
│   ├── convergence.py # 图4 DMDE 收敛
│   └── figures/       # 图产物(入库)
└── results/      # 数值产物(入库)
    ├── metrics.json    # 数值指标(不含分配结构)
    ├── run_meta.json   # seed / 时间戳 / 配置路径 / DEM 网格
    └── solution.json   # 任务表 + 机巢表 + 各周期航次与收敛轨迹
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

```
场景:8 机巢,容量 4,DEM 已接入,frame=(29.522202, 91.479331)
任务:共 20 个(周期1=10, 周期2=5, 周期3=5)
周期 1 @t= 600.0s | T_t=10(remain 0/new 10/release 0) K_avail=24 | T_sel=10 → T_exec=7(保留 3) | F1=41.67 F2=201071m | 0.97s
周期 2 @t=1200.0s | T_t= 8(remain 3/new  5/release 0) K_avail=24 | T_sel= 8 → T_exec=5(保留 3) | F1=24.04 F2=107132m | 0.84s
周期 3 @t=1800.0s | T_t= 8(remain 3/new  5/release 0) K_avail=24 | T_sel= 8 → T_exec=4(保留 4) | F1=34.06 F2= 56643m | 0.63s

汇总:完成 16/20(80.0%),平均等待 638s,总代价 365 km,求解 2443ms,池内剩余 4
```

- 三个周期 `check_feasible` **均无 violation、penalty = 0**
- 机巢占用始终 ≤ capacity(4),无超容(见图 1(c) 热力图)
- 8 周期长跑:完成率 84%–90%,池内剩余回落至 0(滞留任务被逐步消化)

### 图产物(results/figures/)

| 文件 | 内容 |
|---|---|
| `cycle_metrics` | 2×2:任务池水位与流转 / 双目标值 / 机巢泊位热力图 / 求解开销 |
| `selection_funnel` | 逐级筛选任务数 + 各级保留率(T_t → T_sel → T_exec) |
| `allocation_gantt` | **分配甘特图**:谁做什么 · 什么顺序 · 降哪个巢(异巢终止标橙) |
| `assignment_map` | **空间分配图**:每周期一行,有向箭头 + 任务按承运 UAV 着色 |
| `convergence` | DMDE 每代最优适应度 + 归一化收敛 |

每张图同时出 `png`(预览)与 `pdf`(矢量,直接进 LaTeX);中文字体 Noto Sans CJK SC。

## 参数放哪里

| 内容 | 位置 | 理由 |
|---|---|---|
| 机巢经纬度、DEM | `task_allocation/data/` | 实测事实,跨实验复用 |
| 机巢个数、容量、机队规模、航程、速度 | `config.yaml` 的 `problem:` | 实验设定 |
| 任务规模、散布、收益、时间窗 | `config.yaml` 的 `tasks:` | 任务当前为合成,后续换 CSV 时参数可平移 |
| 第一阶段权重与阈值 | `config.yaml` 的 `selection:` | 做 α / 五项权重灵敏度时只改这里 |
| 第二阶段模型与剔除策略 | `config.yaml` 的 `muas:` | λ、选择性剔除、等待补偿、终点进编码、Lamarckian 回写 |
| DMDE 超参 | `config.yaml` 的 `solver:` | 种群、代数、ζ、δ |
| 三维代价 / DEM 网格步长 | `config.yaml` 的 `cost:` | 网格步长直接决定一次性开销 |
| 滚动周期、到达、故障注入 | `config.yaml` 的 `rolling:` | 含 `strict_capacity` 硬校验开关 |
| 图格式、分辨率 | `config.yaml` 的 `plot:` | 代码在 `plot/`,产物路径在 `experiment.plot_dir` |

**原则**:`methods/` 不读配置、不写死数值;所有数值由 `config.py` 从 yaml 取出后传入。

## 成功标准

1. `python run.py` 跑完不抛异常,写出 `results/metrics.json`、`results/run_meta.json`、`results/solution.json`。✅
2. 目录结构符合 `experiments/README.md`。✅
3. `metrics.json` 中记录 8 个机巢的米制坐标 (x, y) 与 DEM 采样高程 z。✅
4. 每个周期的 `check_feasible` 无 violation(当前满足)。✅
5. `T_t^exec ⊆ T_t^sel ⊆ T_t`、`U_t^exec ⊆ U_t^avail` 恒成立。✅
6. `make_plots: true` 时产出 5 张图 × 2 种格式,无缺字告警。✅
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

## 已知问题与待办

- 任务点是**合成**的;有实测数据后应补 `data/task_location_data.csv` 并替换
  `common.task_scenario.build_synthetic_tasks`(下游无需改动)。
- 事件触发(`rolling.events`)目前只**识别**事件并写进 metrics,尚未实现
  「仅对受影响 UAV 做局部重规划」,仍走全量重算。
- 故障注入链路(`rolling.faults`)已实现但未在默认配置启用;验证 `T_t^release`
  时填 `faults: {2: [3]}` 即可(第 2 周期让 UAV 3 故障)。
- 环境需 `uv sync`:`pyproject.toml` 已补 `numpy` / `pillow` / `matplotlib`
  (DEM 栅格解码为惰性依赖),系统 Python 未装这些包。

## 下一步

- **容量灵敏度**:`problem.n_uavs` 调到 32(= 8 巢 × 容量 4,满泊位)即可压出容量约束;
  配合 `rolling.strict_capacity: true` 可验证落地期硬校验不误杀可行解。
- **λ 灵敏度**:`muas.lambda_cost` 决定收益与代价的折算比,直接体现为完成率。
- 对照方法(baselines)就位后,本实验可扩成 exp05 的对比入口。
