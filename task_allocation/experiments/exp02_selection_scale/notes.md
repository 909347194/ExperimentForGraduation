# exp02_selection_scale —— 单周期任务规模可扩展性台

**status:** active（2026-11-12 收窄职责；2026-11-13 正式档已跑完并验收，见 §6）

---

## 1. 唯一职责

在**固定机队 K=6、固定场景 guangzhou、固定求解预算**下，把**单周期**任务规模
`N` 扫到 10/50/100，观测三件事：

1. 第一阶段「任务选择」把进入二阶段 MUAS 的任务数压到多少，是否随 `N` 解耦；
2. 分段墙钟（`hint / selection / build / muas`）与惰性表现算边数随 `N` 的变化；
3. 「不做选择、直接对全量任务求解」的**同口径参照臂**单周期耗时量级。

产物是一条规模—耗时曲线、一张任务去向分账表、一组分段计时图，**全部描述性**。
对应论文 `s04_experiments.tex` §4.5「可扩展性：任务规模 20→100+ 的单周期
求解时间曲线，说明分层框架 + 选择层裁剪对搜索规模的压缩效果」。

## 2. 不负责（边界）

| 不做什么 | 归谁 |
|---|---|
| 方法 / 档位优劣、质量高低、「哪一档更好」 | exp04（消融）、exp05（主张检验台）、exp06（联合域） |
| **完成约束验收**、完成率、按时率 | exp01_smoke（滚动闭环，26/26 PASS） |
| 标定口径选型（`quota_*` vs `cap_x*`） | exp04 / exp06；本实验的五档只是扫描自变量的水平 |
| 滚动到达、机队余航程衰减下的 `cap(t)` | exp06（联合域） |
| 跨实验 / 跨场景绝对值比较、配对 t 检验、显著性 | 无；本实验不产 p 值 |
| 机制归因（"省下的时间来自哪里"）、耗时倍数 | 无；墙钟只作观测 |

**与相邻实验的分工**：exp01 是「机多任务少 + 滚动闭环」，负责完成是约束；
exp02 是「任务远多于机 + 单周期快照」，负责搜索规模压缩；exp06 补两者的
交界（滚动到达 + 联合域）。三者不互相替代，也不互相给结论背书。

## 3. 口径红线

### 3.1 单周期快照 ≠ 完成约束

本实验**没有后续周期**。被选择层刷掉的任务既不叫「暂缓」也不叫「顺延」，
一律记 `remain`（未完成）；选中但未交付的也记 `remain`。因此：

- **不宣称完成约束被满足**，不写完成率，不写「可完成任务都完成了」；
- 分账的目的是**可审计**：任何一格都不允许出现「任务凭空消失」。
  收窄前的旧档把 `full_baseline` 的 `n_deferred` 写死为 0，而 N=100 只
  交付 64 个任务，36 个任务没有去向——这是硬缺陷，已修。

### 3.2 任务去向分账（`accounting.py`）

主线恒等式 `arrived = done + unreachable + remain`。本实验是单周期静态快照，
**没有几何不可达判定**，故 `unreachable` 记为 `null`（不可观测）而不是伪装成 0，
闭合式退化为 `arrived = done + remain`，并显式带 `unreachable_not_observed=True`。

每格落 `accounting`：`n_arrived / n_selected / n_not_selected / n_done /
n_deferred_in_selected / n_remain / identity_ok / selection_active`。
`run.py` 在写 `metrics.json` 前检查全格 `identity_ok_all`，不闭合直接抛错——
门禁不是装饰。`selection_active` 标出该格是否真触发了截断（N=10 时五档
逐位相同，属「未压缩」参照点，不是档位差异的证据）。

### 3.3 F1 不是质量判据

`f = W_hard·n_hard + w_exc·excess_m + W_skip·n_skip + w_late·L_tot + λ·path`，
**F1 只作事后指标、不进 f**（`methods/muas/problem.py` 门禁口径）。收窄前
R01 把 `F1 / 同 N 全量` 称作「收益比值」并据此判断「更快且不损失」，
等于用一个**不是目标的量**评判方法，且该比值主要度量的是「选了多少任务」
而非解的好坏。现已撤下：F1 原始值仍落盘为 `muas.f1_reward_descriptive`，
仅供核对，**不进论文主表、不作任何优劣结论**。

图注里旧写「按迟到折减后口径」也与实现不符：门禁模式下 `late_reward_decay`
被置空，`f1_reward` 计**全额奖励不折减**（`Chapter3_README.md` §口径表）。

### 3.4 两臂只差一个自变量

收窄前选择臂走 `make_lazy_pairwise` + `pairwise_source=lazy`（惰性活视图），
参照臂走 `build_pairwise_costs` 全量 eager 建表 ⇒ 计时差里混了
「要不要选择」和「建表策略」两个自变量。现在两臂都先建惰性表、都用同一
`distance_fn`、都通过 `pairwise_source` 建矩阵、共用同一
`problem_model / stage_cfg / solver_cfg`；**唯一差别是是否调用选择层**。
参照臂的 `t_hint_ms = t_selection_ms = 0` 是这一自变量的直接体现。

### 3.5 墙钟只作观测

单进程、单 seed（42）、单次 `perf_counter`，无重复、无置信区间；`distance_fn`
带记忆化且**跨格共享**，先跑的格承担冷启动、后跑的格命中缓存，存在系统性
先后偏倚；`polish_evals` 随收敛浮动（162–800），config 里旧写「全格同预算
横向可比」并不成立。因此：

- 只报告量级与曲线形状，**不作机制归因、不报告耗时倍数、不给 p 值**；
- 与 exp01 / exp05 / exp06 的墙钟**禁横向比较**；
- 旧档 N=100 参照臂单格 `t_muas ≈ 3.16 h`，是整批 3h34m 的主要来源，
  属观测事实，不是结论。

## 4. 实验设置

- **场景**：`guangzhou`（3 机巢 × 容量 2，DEM + 建筑垂直切面包络三维有向航程）。
  **机队同构 6 架 10 km @ 15 m/s**；`max_time` 由 `航程/速度` 派生。
  注意 exp01_smoke 自 2026-11-11 起改用异构 10/8/6 km 清单 ⇒
  **两实验机队口径不同，禁比绝对值**。
- **任务**：`domain=region`，广州研究区多边形内均匀撒点；每规模 seed = 42 + N。
- **规模**：`N ∈ {10, 50, 100}`，K 恒为 6。
- **档位**（仅扫描水平，不作选型）：`quota_a2.5` / `quota_a5`（intersect）、
  `cap_x0.25` / `cap_x0.5` / `cap_x1`（primary, budget_scale 阶梯），
  外加每规模一个 `full_baseline` 参照臂 ⇒ 18 格。
- **选择封顶**：`selection.marginal.max_ratio: 2.0` ⇒ `⌈2.0×6⌉=12`。
  该键在 2026-11-12 前是「死键」（写在 yaml 里静默不生效，实际吃
  `MarginalConfig` 代码默认），现已显式接线并写入 `effective_config.json`。
- **求解预算**：DMDE pop=20 × 40 代 + polish ≤800 评估（全格同配置）。

## 5. 产物与复现链

| 文件 | 内容 |
|---|---|
| `results/metrics.json` | 全格数值 + `scope` 职责声明 + `accounting_summary` |
| `results/effective_config.json` | `--set` 覆盖后的**全量**配置 + 派生口径（K/场景/预算/封顶） |
| `results/run_meta.json` | seed / 场景 / 规模 / 档位 / 求解预算 / 分账汇总 |
| `results/tables/` | `build_tables.py` 从 metrics 单源生成的 CSV + LaTeX 三线表 |
| `results/figures/` | `plot/registry.py` 登记的 6 张图（PNG/PDF） |
| `results/figures/PAPER_scalability.png/.pdf` | **论文主图**：`make_paper_figure.py` 从 metrics 单源生成；(a) 到达规模—墙钟曲线、(b) 同一批点按进入二阶段任务数重排。不注册进 6 图清单，批量出图不会覆盖 |

```bash
# 先验键（不建场景、不求解、不落盘）
.venv/Scripts/python.exe task_allocation/experiments/exp02_selection_scale/run.py --dry-run

# 正式档（须单独发话；求解期不出图、旧档先归档、严格串行）
.venv/Scripts/python.exe task_allocation/experiments/exp02_selection_scale/run.py --no-plots
.venv/Scripts/python.exe task_allocation/experiments/exp02_selection_scale/build_tables.py
.venv/Scripts/python.exe task_allocation/experiments/exp02_selection_scale/render_from_json.py png,pdf
```

**跑批纪律**：严禁多实验并行；正式档单独发话；求解时不出图；失败 seed 不静默
重采样；`--set` 必须写全嵌套路径（平铺是死键，会被 `effective_config.json`
记下却静默不生效）。

## 6. 结果档状态（2026-11-13 正式档）

11-10 收窄前旧档（无 `accounting`、F1 键名不同、对照组 eager 建表）已按
`experiments/README.md` §7 归档到
`_archive/exp02_selection_scale__results_2026-11-10_pre_scope/`，**只移动不删**；
其收益比值、cap(t) 取值建议、耗时倍数等结论一并作废。`docs/` 与 PPT 里引用的
「收益损失 58%」「保持率 ≥80%」「N=100 达 83.4%」均属旧场景
（lasa_chengguan / 8 机 / 80 km）数字，按红线不得进论文、不得与本档混用。

正式档在**等待会话外 exp11 进程退出、确认无并行负载后**严格串行跑完：
`FORMAL_RC=0` / `TABLES_RC=0` / `FIGURES_RC=0`，`wall_clock_s=8831.853`
（单进程、单 seed 42、18 格）。三份产物在求解完成且分账门禁通过后同一时点
原子落盘，`effective_config_sha256` 在 `metrics.json` / `run_meta.json` 一致；
`n_rows=18`、`identity_ok_all=true`，10 格发生截断、8 格未发生
（N=10 五档 + 三个不做选择的对照组）。产物：6 张图 × PNG/PDF = 12 文件、4 张表。

### 6.1 逐格观测值（描述性陈列，不作优劣判断）

| N | 档位 | 入选 | 交付 | 未完成 | t_total (s) |
|---:|---|---:|---:|---:|---:|
| 10 | 五档 | 10 | 10 | 0 | 6.7–6.9 |
| 10 | 不做选择（full_baseline） | 10 | 10 | 0 | 7.0 |
| 50 | 名额 α=2.5 / α=5 | 12 | 12 | 38 | 9.8 / 9.4 |
| 50 | 能力 ×0.25 | 11 | 11 | 39 | 7.2 |
| 50 | 能力 ×0.5 | 23 | 23 | 27 | 48.7 |
| 50 | 能力 ×1.0 | 45 | 40 | 10 | 440.0 |
| 50 | 不做选择 | 50 | 45 | 5 | 632.0 |
| 100 | 名额 α=2.5 / α=5 / 能力 ×0.25 | 12 | 12 | 88 | 8.5 / 8.1 / 8.3 |
| 100 | 能力 ×0.5 | 24 | 24 | 76 | 52.2 |
| 100 | 能力 ×1.0 | 48 | 48 | 52 | 394.5 |
| 100 | 不做选择 | 100 | 55 | 45 | 7168.7 |

分段值逐格见 `metrics.json`；`t_total` 与 `t_muas` 同量级，`t_build` 最大 8.1 s，
`unreachable` 全 18 格为 `null`（不可观测）。

### 6.2 论文里必须交代的三条口径（否则会被审稿人质疑）

1. **「压到 12」不是全局常数。** `max_ratio·K=12` 只在名额族与能力 ×0.25
   档成为实际上界；N=100 时能力 ×0.5 / ×1.0 的 `capability_cap` 为 24 / 48，
   入选数也就是 24 / 48。选择数由候选名额与能力预算共同决定，写曲线结论时
   **必须限定档位**，不能笼统说「选择层把任务压到 12」。
2. **`remain` 不是淘汰，也不构成完成约束被违反的证据。** 本实验是单周期静态
   快照、没有后续周期：N=100 不做选择的对照组 100 到达、55 交付、45 未完成；
   选中也不等于交付（N=50 能力 ×1.0 入选 45、交付 40）。完成约束由 exp01
   滚动闭环检验，本实验不写完成率、不写「可完成任务都完成了」。
3. **墙钟只作量级观测。** 单 seed、单次 `perf_counter`、无重复无置信区间；
   `distance_fn` 记忆化缓存跨格共享，先跑的格承担冷启动；`polish_evals` 随
   收敛浮动。本档虽在无并行负载下串行跑完，仍只描述曲线形状与量级，
   **不作机制归因、不报告耗时倍数、不给 p 值**，与 exp01 / exp05 / exp06
   禁横向比较；本档机队同构 6 架 10 km @ 15 m/s，exp01 为异构 10/8/6 km。

### 6.3 产物验收修订（仅重出图，未重跑求解）

- R01 (b) 图例原靠 `bar(label=...)` 自动收集；N=10 后两类为 0、不画柱，
  导致图里明明有橙/紫段、图例却只剩「交付」一项。改为显式 `Patch` 句柄，
  三项齐全。
- R04 (b) 图例由两列改单列，避免「未选中」与「左柱」并排被读成对应关系。
- 复核：`render_from_json.py png,pdf` 产出 12 文件、`FIGURES_RC=0`；
  两张图版式逐张看过；定向回归 30 passed。`metrics.json` 与 4 张表未改动。

### 6.4 论文主图（§4.5 用）

既有 6 张里，只有 R01 的左面板有正文价值，但它的右面板是审计用的任务去向
分账，整幅直接入刊会把读者注意力带偏。故另出一张**不注册进 6 图清单**的
论文主图 `PAPER_scalability.{png,pdf}`，由 `make_paper_figure.py` 从
`results/metrics.json` 单源读数生成（口径门禁与出表同源：拒绝旧档与不闭合档），
不重跑求解、不覆盖既有图：

- **(a)** 到达规模 N → 单周期端到端墙钟（对数轴），满足 §4.5 占位符字面要求
  「任务规模 20→100+ 的单周期求解时间曲线」。
- **(b)** 把**同一批 18 个点**按「进入二阶段的任务数 n_sel」重排：不同到达
  规模 N 的点只要 n_sel 相近，墙钟就落在同一量级（例如 n_sel≈12 的 N=50
  与 N=100 各点均约 8–10 s）。这是本实验信息量最大的一句，且只登记对应
  关系，不作因果或机制说明；细线仅为同档位点的执行顺序连线，不是拟合。

其余 5 张（S01 设定表、R02 计数、R03 分段计时、R04 结构、R05 档位观察）
属内部审计图，需要时进附录，不占正文版面。图注已写入单 seed 单次观测、
跨格共享缓存、单周期快照与 F1 不进 f 的口径说明。

### 6.5 仍欠的技术债（下次跑批前处理，需单独发话）

- **逐格增量检查点**：当前 18 格全算完才一次性落盘，中途中断会丢掉已完成
  的 17 格（本次首跑被会话切换中断即如此，重跑了一整遍）。
- **措辞债**：图内脚注 / 标题、`config.yaml`、`run.py` 注释仍用「参照臂」
  等说法，与项目沟通约定冲突；全面收口会改变已验收图版式。
- 论文 §4.5 正文仍是 `\placeholder{}`，本档数值可作为该段素材，但措辞须
  遵守 §6.2 三条与 `experiments/README.md` §9。
