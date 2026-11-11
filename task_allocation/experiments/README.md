# experiments/ —— 实验目录约定

本文件是**实验层约定的唯一说明**：参数放在哪、产物怎么分工、每个实验目录
长什么样。`common/scenario.py`、`exp01/paths.py`、`exp01/metrics.py`、
`exp05/config.yaml` 等处注释里的「见 `experiments/README.md`」都指向这里。

> 补建：此前本文件被 11 处注释引用却不存在，约定只散落在各实验
> 的 `notes.md` 里，容易各写各的。这里只记**跨实验通用**的约定；
> 单个实验的口径、结果、标定史仍在各自 `notes.md`。

---

## 1. 独立性原则（口径约定）

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
├── build_tables.py  # 从 results/metrics.json 单源生成 CSV + LaTeX 三线表
├── plot/            # 可视化（registry.py 为图清单唯一事实来源）
├── notes.md         # 本实验口径、结果、标定史
└── results/         # 数值与图产物（gitignore）；figures/ 出图、tables/ 出表
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
| `exp01_smoke` | 主链路冒烟 + 主口径结果（滚动闭环、覆盖门禁）；11-10 用当前代码重跑正式档、26/26 PASS，图 8 件 + 表 3 件 | active |
| `exp02_selection_scale` | 选择层规模（N / 名额 / 能力上界）标定；11-10 切 guangzhou 当前口径重跑正式档，18/18 格闭合，图 12 件 + 表 4 件 | active |
| `exp03_event_rolling` | 事件触发重规划 | planned（仅 notes） |
| `exp04_ablation` | 消融矩阵收编 | planned（仅 notes） |
| `exp05_baselines` | 主张检验台 C1–C5（同一批解两把尺子、独立重评、配对精确 p）；v3 正式档 35 单元 | active |
| `exp06_joint_regime` | 联合域（机队规模 × 到达率 × 能力衰减） | active |
| `exp07_polish_accept` | 采纳判据统一 `f` + L3b 开/关对照 | active（定稿 §11.8/§11.9） |
| `exp08_gain_decomposition` | 交付后成本优化收益拆分 2×2 | active（§11.10） |
| `exp09_mcf_layers` | 终点机巢层 1 × 层 3 四档 | active（§11.11） |
| `exp10_capacity_pressure` | 容量压力四场景 | active（§11.12） |
| `ab_m21_patrol_writeback` | 机制诊断：段内序 Lamarckian 回写三臂 | 诊断证据链 |
| `ab_mechanism_3steps` | 机制诊断：基因型/表型、2×2、模块三步 + 选择错配探针 | 诊断证据链 |
| `ab_optimization_attribution` | 机制诊断：优化工作归属（进化 / 修复 / 精修） | 诊断证据链 |
| `ab_order_marginal` | 机制诊断：组内顺序的边际贡献 | 诊断证据链 |

planned 目录只放 `notes.md`（有意占位），**不要删**。四个 `ab_*` 目录是阶段二
重构的机制诊断证据链，被 `docs/stage2_final_scheme_2026-11-06.md` 等文档引用，
**不要因为名字不像 expXX 就当临时脚本删掉**。

## 7. 目录纪律与历史产物归档（2026-11-10 规整）

- 每个实验顶层只放 §4 约定的自持文件（`config.py/yaml`、`build.py`、`paths.py`、
  `run.py`、`metrics.py`、`notes.md`、`plot/`、`results/`）与少量结论性报告/图。
  **一次性探针脚本、运行日志、编译中间产物不得留在实验顶层**。
- 历史批次、一次性扫描结果与日志统一归到 `_archive/`（gitignore），命名规则
  **`_archive/<实验名>__<原目录名>`**，例如 `exp05_baselines__results_v3`、
  `ab_mechanism_3steps__results_s2_60`。`docs/` 与各实验 `notes.md` / 报告里出现的
  旧 `results_*` 相对路径，一律按此规则映射到 `_archive/` 下读取；
  **结论与数值未因归档改动**。
- 顶层日志留底在 `_archive/logs_2026-11-10/`，一次性脚本留底在
  `_archive/temp_scripts_2026-11-10/`；`results/` 内部的正式档日志原地保留。
- `config.yaml` 的 `output_dir` 是实验自变量，**不因归档改写**；重跑会在顶层
  重新生成同名产物目录，跑完按本节规则归档。
- 纯 `results/` 是各实验当前有效产物目录，**不归档**；归档的只是带后缀的历史批次。
- 结果表由各实验自持的 `build_tables.py` 从 `results/metrics.json` **单源生成**，
  产物落 `results/tables/`（CSV + LaTeX 三线表 `.tex` 片段）；**禁手写数字**。
  出图与出表都在求解完成后单独执行，不占求解进程（跑批纪律：求解时不出图）。

## 8. 运行

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

## 9. 代码口径变更与正式档重评（2026-11-10，**未完成**）

> 本节只记录「当前代码 ≠ 已交付结果档」的事实与处置纪律，**不宣称旧档作废**。
> 逐项实测数字见 `.workbuddy/memory/2026-11-10.md`。

同日两处**相互独立**的改动落在共用底座上。凡使用 `guangzhou` 并走 `DMDESolver`
的正式档，其绝对值口径都须在受控重评后再决定是否沿用。

| | 变更 A：DEM 采样与网格 | 变更 B：随机流单通道（远端 `6fee472`） |
|---|---|---|
| 位置 | `methods/utils/dem_geotiff.py`、`methods/muas/cost/dem_terrain.py`（本地未提交） | `methods/muas/representation/*`、`operators/extinction.py`、`solvers/dmde_solver.py`（已合入） |
| 实质 | nodata **插值前**掩码；`nx/ny` 由 `round` 改 `floor`；extent 内缩 1 像素；查询边界 `>=` 改 `>` | 删全局 `random.seed` / `np.random.seed`；初始种群与进化**合并为同一** `Generator`(PCG64)；灭绝重置开始消费该 `rng` |
| 实测 | 网格 25×25→**24×25**；NaN 108/625(17.28%)→**56/600(9.33%)**；有限高程 max 255→**52.851**；>75m 的 nodata 派生错误值 20→**0**；节点契约 600/600；1806 个 OD 代价全为有限正值 | 同 seed 两次 solve 逐位一致；`generate(seed=42)` 不再改写全局流；一次 tiny solve 内**无参熵源兜底 0 次**；6 代内灭绝触发 2 次（回归非空转） |
| 影响 | 唯一距离口径（垂直切面 DEM + 建筑包络三维有向航程） | 初始种群逐位值改变，且**后续所有世代的 F 值 / 交叉 / 灭绝判定序列整体平移**——不是只换初始种群 |

**受影响结果档**：明确受影响的是 `exp01_smoke`、`exp02_selection_scale`、
`exp07`–`exp10`（guangzhou + DMDESolver）。`exp05_baselines` v3 正式档用的是
**`lasa_chengguan`**（6 巢、无建筑）：RNG 变更影响需确认，DEM 变更是否影响
**须单独查该场景 DEM 有无 nodata，不得推定同病**。旧档一律归档不删、不覆盖；
跨场景禁直接比绝对值、禁配对 t 检验。各实验 `notes.md` 若引用旧绝对值，须加注本节。

**红线**：① 不能凭「存在污染格点」直接写「旧数字作废」——必须修前/修后
**同 seed 受控重评**量化差值，配对差是否抵消不得假设；② 两处变更已合并进
同一份代码，**差值不可归因到单因素**，若要归因须在当前代码内另做受控单因素消融；
③ 旧绝对值不直接进论文；墙钟只作观测、不可跨实验横向比较；`f` 与航程的
线性缩放不是两条独立证据；p 不显著须如实声明。

**重评计划（须单独发话执行，本轮未做）**：
1. 先做轻量定量：`exp01_smoke`（3 seed，约 19 分钟）修前/修后同 seed 配对重评，
   量化距离与完成/按时指标差值；
2. 据差值决定 `exp02`（约 3h34m）、`exp05` v3（约 1h56m）及 `exp07`–`exp10`
   是否重跑；
3. 重跑严格串行、求解期不出图、旧档先归档、失败 seed 不静默重采样。

**库层面残留纪律**：生产代码仍有 13 处 `rng=None → default_rng()` 熵源兜底
（只有 `repair_invalid` 为必填）；`DMDEConfig.seed` 默认 `None`，各实验
`config.yaml` 均显式给 seed 故实际实验可复现；`seed=` 兼容别名只发默认不可见的
`DeprecationWarning`。新增 `test_solve_never_falls_back_to_entropy_rng`
锁死主链路单通道，新增入口漏传 rng 立即失败。全量回归 **573 passed in 35.57s**。
