# experiments/ —— 实验目录约定

本文件是**实验层约定的唯一说明**：参数放在哪、产物怎么分工、每个实验目录
长什么样。`common/scenario.py`、`exp01/paths.py`、`exp01/metrics.py`、
`exp05/config.yaml` 等处注释里的「见 `experiments/README.md`」都指向这里。

> 2026-10-29 补建：此前本文件被 11 处注释引用却不存在，约定只散落在各实验
> 的 `notes.md` 里，容易各写各的。这里只记**跨实验通用**的约定；
> 单个实验的口径、结果、标定史仍在各自 `notes.md`。

---

## 1. 独立性原则（2026-10-29 用户裁定）

**每个实验尽量独立，不做跨实验共享抽取。**

- 各实验自带 `config.py` / `build.py` / `paths.py` / `metrics.py` / `plot/`，
  允许存在相似代码——这是**有意的选择**，代价是重复，收益是改一个实验不会
  悄悄动到另一个实验的口径。
- 因此**不要**为了消重去建 `experiments/_shared/` 或把绘图基建抽成公共包：
  实验之间一旦共享，A 实验的"顺手改一下"会改掉 B 实验的结果，而这类污染
  在数值上很难发现。
- 真正需要共享的底层能力（场景构建、任务撒点、DEM、代价）在 **`common/`**
  与 **`methods/`**，那里的复用是被要求的；实验层的重复则允许。

## 2. 参数放置原则

| 放哪 | 什么参数 | 判据 |
|---|---|---|
| `config.yaml` | 一切**实验自变量**（规模、航程、周期、权重、开关） | 改了会改变实验结论 |
| `build.py` | yaml → 对象的**装配**逻辑（含路径解析、默认值兜底） | 只搬运，不发明数值 |
| `methods/` 数据类默认值 | 机制的历史默认值 | 仅在 yaml 未给出时生效 |
| 代码常量 | 物理/数值常量（容差、迭代上限的内部默认） | 不属于实验自变量 |

**不要在 `build.py` 里写数值**：默认值一律走数据类（如 `SyntheticTaskConfig`），
yaml 显式覆盖。`build.py` 只做 `t.get("key", <数据类默认值>)`。

**`--set` 覆盖必须写全嵌套路径**（血泪教训）：选择配额的真实键是
`selection.marginal.max_ratio`，写平铺的 `selection.max_ratio` 是**死键**——
`effective_config.json` 会记下来，但运行时完全不生效，静默退回 yaml 默认值，
整批扫描实验会长得一模一样。写 `--set` 前先 grep 代码里实际读取的键路径。
可用 `--dry-run` 先验键（只打印生效值、不求解）。

## 3. 三个产物的分工

| 产物 | 内容 | 消费方 |
|---|---|---|
| `metrics.json` | 数值指标：逐周期 + 汇总 + 多种子聚合 | 论文表格、结果图 |
| `solution.json` | 分配结构：任务表 / 机巢表 / 各周期航次与收敛轨迹 | 过程图、动画、审计 |
| `run_meta.json` | 运行元数据：seed / 时间戳 / 配置路径 / DEM 网格 | 复现核对 |
| `effective_config.json` | 本次**真正生效**的配置（含 `--set` 覆盖结果） | 复现核对、口径审计 |

**指标不重复落盘**：结构只在 `solution.json`，数值只在 `metrics.json`；
两者可互相校验（`metrics` 的航程应与 `solution` 各周期航次代价之和一致）。

## 4. 目录布局（每个实验同构）

```
expXX_<name>/
├── config.yaml      # 实验自变量（唯一入口，可被 --set 覆盖）
├── config.py        # yaml 加载 + 深合并 + CLI 覆盖解析
├── build.py         # yaml → 场景 / 任务 / 求解器对象的装配
├── paths.py         # 目录定位与 sys.path 引导
├── run.py           # 主编排：建场景 → 滚动求解 → 落盘 → 出图 → 审计
├── metrics.py       # 指标计算（逐周期 / 汇总 / 多种子聚合）
├── audit_correctness.py  # 方法正确性审计（run.py 末尾自动调用，是门禁不是装饰）
├── plot/            # 可视化（registry.py 为图清单唯一事实来源）
├── notes.md         # 本实验口径、结果、标定史
└── results/         # 数值与图产物（gitignore）
```

`render_from_json.py` 是**补图入口**：已有 `results/` 时可不重跑求解直接重画。

## 5. 绘图约定

- 图清单的唯一事实来源是各实验 `plot/registry.py`，增图 = 加一行。
- 查当前有哪些图：`python -c "from plot import registry; print(registry.describe())"`。
- 带地图的图一律走背景层规则：**有建筑优先画建筑 → 只有地形才画 DEM →
  都没有就是纯底**（`plot/base/backdrop.py`）。场景数据定位只在
  `plot/base/scene.py`，不各自去找 DEM / 建筑文件。

## 6. 实验清单与状态

| 目录 | 目的 | 状态 |
|---|---|---|
| `exp01_smoke` | 主链路冒烟 + 主口径结果（滚动闭环、覆盖门禁） | active |
| `exp02_selection_scale` | 选择层规模（N / 名额 / 能力上界）标定 | active |
| `exp03_event_rolling` | 事件触发重规划 | planned（仅 notes） |
| `exp04_ablation` | 消融矩阵收编 | planned（仅 notes） |
| `exp05_baselines` | 与基线方法对照（M0/D0/B1/B2/B2e/B3） | active |
| `exp06_joint_regime` | 联合域（机队规模 × 到达率 × 能力衰减） | active |

planned 目录只放 `notes.md`（有意占位），**不要删**。历史数据与一次性扫描
结果归到 `_archive/`（gitignore），不占实验顶层。

## 7. 运行

```bash
# 从仓库根，用项目虚拟环境
.venv/Scripts/python.exe task_allocation/experiments/exp01_smoke/run.py

# 常用开关
--set dotted.path=value   # 覆盖任意配置节点（可多次）
--dry-run                 # 只打印生效配置，不求解（验证 --set 写对了没）
--seeds 42,43,44          # 多种子
--no-plots                # 跳过出图（多种子长跑）
```

**严禁多实验并行跑**：耗时是上报指标，并行会污染墙钟。
