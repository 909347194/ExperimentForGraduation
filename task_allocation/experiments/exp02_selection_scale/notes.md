# exp02_selection_scale —— 单周期任务规模可扩展性台

**status:** active（2026-11-20 改规模口径并重跑正式档：单闸门 n_sel=min{cap(t), k_compute}，机数名额退出选择路径；结果见 §6）

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
门禁不是装饰。`selection_active` 标出该格是否真触发了截断（N=10 时各档
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
- **档位**（仅扫描水平，不作选型）：
  - `quota_a2.5` / `quota_a5`（旧口径 `mode=intersect`）：
    `k = min{⌈α·K⌉, ⌈max_ratio·K⌉}`，纯机数名额，与机队吞吐能力脱钩；
  - `cap_x1`（单闸门 `mode=single`，候选=全池）：`k = cap(t)`，
    候选不再有 `⌈αK⌉` 第二道名额，ΔB/ΔC 在整池上挑 `k` 条；
  - `cap_x1_win`（单闸门 + 优先级窗口 `candidate_window_ratio=1.0`）：
    `k` 同 `cap_x1`，但候选只取 `P_i` 前 `k` 条 —— 与 `cap_x1` 只差这一个自变量；
  - `cap_c25`（单闸门 + 算力闸门 `n_compute=25`）：`k = min{cap(t), 25}`；
    算力闸门 = 本周期二阶段最多允许求解多少条任务，单位「条/周期」；
  - 每规模外加一个 `full_baseline` 参照臂 ⇒ 18 格。
- **规模口径（2026-11-20 改定）**：单闸门 `n_sel = min{cap(t), n_compute}`，
  机数名额只落 `diagnostics.n_sel_quota`，不参与合成；候选池默认全池
  （`candidate_window=full_pool`），`P_i` 只负责排序与并列裁决。
  旧口径的 `max_ratio=2.0`（⇒`⌈2×6⌉=12`）仍写在 yaml 并显式接线，
  但只在 `quota_*` 两档生效，作为旧口径参照。
- **求解预算**：DMDE pop=20 × 40 代 + polish ≤800 评估（全格同配置）。

### 4.1 算力闸门 `n_compute` 的定义

**算力闸门：本周期二阶段最多允许求解多少条任务，单位「条/周期」。**

- 与 `cap(t)` 正交：`cap(t)` 是可行性上界（这些剩余里程跑得完多少条），
  `n_compute` 是算力上界（本周期算得完多少条）。二者取小，谁 binding 由
  `diagnostics.n_sel_binding ∈ {cap, compute}` 记录。
- **与机队规模无关**：二阶段预算（pop×gen + polish 上限）逐周期固定，机队变大
  只改变可行域形状、不加搜索预算；而单次评估开销随进入求解的任务条数增长
  （组合规模阶乘级、链上插入位置平方级）⇒ 耗时由 `|S|` 决定。本实验 K 恒为 6
  而墙钟跨三个数量级，正是否证"耗时由机队能力决定"的控制变量证据。
- **取值**：`n_compute=25` 由本实验 `n_sel → 墙钟` 曲线反推（≈60 s 单周期预算），
  不是拍脑袋常数；`None` = 不设算力上限。
- **边界（必须守住）**：它是算法侧（选择层）约束，**不进问题模型 `(P_1)` 的约束**。
  否则数学模型会随部署机器算力变化；论文里的 `σ_t` 仍只表述"机队能力估计出的
  接纳上界"。未被允许进入 `S` 的任务不构成违约，留池顺延由滚动层完成闸门承接。
- **未验证**：本实验 K 恒定，"K↑ ⇒ cap↑ ⇒ |S|↑ ⇒ 墙钟↑"这条链是推出来的，
  没有直接测过（缺口见 §6.5）。

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

# 单格冒烟（收窄规模/档位；--set 换输出目录，不碰正式 results）
.venv/Scripts/python.exe .../run.py --scales 50 --only cap_c25 --no-plots \
    --set run.full_muas_baseline_scales=null --set experiment.output_dir=_smoke_c25

# 正式档（须单独发话；求解期不出图、旧档先归档、严格串行）
.venv/Scripts/python.exe task_allocation/experiments/exp02_selection_scale/run.py --no-plots
.venv/Scripts/python.exe task_allocation/experiments/exp02_selection_scale/build_tables.py
.venv/Scripts/python.exe task_allocation/experiments/exp02_selection_scale/render_from_json.py png,pdf
```

**跑批纪律**：严禁多实验并行；正式档单独发话；求解时不出图；失败 seed 不静默
重采样；`--set` 必须写全嵌套路径（平铺是死键，会被 `effective_config.json`
记下却静默不生效）。`--scales` / `--only` 只收窄规模与档位，其余键不动，
不影响正式整档的配置指纹。

## 6. 结果档状态（2026-11-20 单闸门正式档）

2026-11-20 改规模口径后重跑。改动只在选择层：`capability.mode="single"`
（`n_sel = min{cap(t), n_compute}`），机数名额退出合成；候选池默认全池，
`P_i` 只负责排序与并列裁决。求解器、场景、任务、种子、求解预算逐位不动，
故新旧两档的差异可直接落到"选择层怎么定规模、怎么挑集合"这一件事上。

改口径前的 results 已移到
`_archive/exp02_selection_scale__results_2026-11-20_pre_singlegate/`（只移动不删）。

运行事实：单进程、单 seed 42、18 格，`wall_clock_s=9666.729`；三份 json 在
求解完成且分账门禁通过后同一时点原子落盘，`identity_ok_all=true`，
10 格发生截断、8 格未发生（N=10 的 5 条选择档 + 3 个 `full_baseline`）。

### 6.1 逐格观测值（描述性陈列，不作优劣判断）

| N | 档位 | 候选 | 入选 | 交付 | 未完成 | t_sel (s) | t_muas (s) | t_total (s) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 10 | quota_a2.5 | 10 | 10 | 10 | 0 | 0.1 | 6.6 | 6.9 |
| 10 | quota_a5 | 10 | 10 | 10 | 0 | 0.1 | 6.5 | 6.7 |
| 10 | cap_x1 | 10 | 10 | 10 | 0 | 0.1 | 6.4 | 6.6 |
| 10 | cap_x1_win | 10 | 10 | 10 | 0 | 0.1 | 6.4 | 6.6 |
| 10 | cap_c25 | 10 | 10 | 10 | 0 | 0.1 | 6.7 | 6.8 |
| 10 | full_baseline | — | 10 | 10 | 0 | 0.0 | 6.6 | 6.8 |
| 50 | quota_a2.5 | 15 | 12 | 12 | 38 | 0.3 | 8.8 | 9.4 |
| 50 | quota_a5 | 30 | 12 | 12 | 38 | 0.6 | 8.3 | 9.2 |
| 50 | cap_x1 | 50 | 45 | 45 | 5 | 3.5 | 321.6 | 325.4 |
| 50 | cap_x1_win | 47 | 45 | 40 | 10 | 3.1 | 439.6 | 443.1 |
| 50 | cap_c25 | 50 | 25 | 25 | 25 | 1.7 | 51.3 | 53.3 |
| 50 | full_baseline | — | 50 | 45 | 5 | 0.0 | 648.9 | 651.3 |
| 100 | quota_a2.5 | 15 | 12 | 12 | 88 | 0.3 | 7.8 | 8.6 |
| 100 | quota_a5 | 30 | 12 | 12 | 88 | 0.6 | 7.2 | 8.3 |
| 100 | cap_x1 | 100 | 48 | 48 | 52 | 11.3 | 267.7 | 279.6 |
| 100 | cap_x1_win | 48 | 48 | 48 | 52 | 2.9 | 414.1 | 417.5 |
| 100 | cap_c25 | 100 | 25 | 25 | 75 | 4.5 | 58.8 | 64.0 |
| 100 | full_baseline | — | 100 | 55 | 45 | 0.0 | 7345.2 | 7353.7 |

`full_baseline` 的"候选/入选"均记 N（不调用选择层）。分段值逐格见
`metrics.json`；`unreachable` 全 18 格为 `null`（不可观测）。

### 6.2 三处可核对的结构差异（旧档 → 本档）

1. **同入选数下，"入选未交付"消失了。** N=50 时 `cap_x1` 与 `cap_x1_win`
   都入选 45 条；全池 Δ 判据那一档交付 45、`n_deferred_in_selected=0`，
   P_i 窗口那一档交付 40、`n_deferred_in_selected=5`。旧档 primary 档
   （等同窗口口径）当年也是 45 入选 / 40 交付。差别只来自候选集合：
   窗口档把"P_i 排前 45"当成了承诺集，其中 5 条在二阶段装不下。
2. **同交付数下，二阶段负担不同。** N=100 时两档都入选并交付 48 条，
   `t_muas` 为 267.7 s（全池）与 414.1 s（窗口）；代价是全池判据自身
   在 100 个候选上做插入评估，`t_sel` 为 11.3 s（窗口档 2.9 s）。
   该项只登记数值，不作机制归因。
3. **候选冗余度对交付没有增量。** N=100 时 `quota_a5` 把候选从 15 扩到 30，
   入选与交付仍是 12/12，`t_sel` 从 0.3 s 抬到 0.6 s；N=50 同样 12/12。
   这条与"删掉 `n_cand` 这一层"的判断互为证据。

### 6.3 论文里必须交代的三条口径（否则会被审稿人质疑）

1. **"压到 12"只属旧口径。** `⌈max_ratio·K⌉=12` 只在 `quota_*` 两档成为
   实际上界；本档单闸门的规模是 45（N=50）、48（N=100）与 25（算力档），
   分别由 `cap(t)` 与 `n_compute` 决定。写曲线结论必须限定档位。
2. **`remain` 不是淘汰，也不构成完成约束被违反的证据。** 本实验是单周期
   静态快照、没有后续周期：N=100 不做选择的对照组 100 到达、55 交付、
   45 未完成。完成约束由 exp01 滚动闭环检验，本实验不写完成率。
3. **墙钟只作量级观测。** 单 seed、单次 `perf_counter`、无重复无置信区间；
   `distance_fn` 记忆化缓存跨格共享，先跑的格承担冷启动；`polish_evals` 随
   收敛浮动。只描述曲线形状与量级，**不作机制归因、不报告耗时倍数、
   不给 p 值**，与 exp01 / exp05 / exp06 禁横向比较；本档机队同构 6 架
   10 km @ 15 m/s，exp01 为异构 10/8/6 km。

### 6.4 论文主图（§4.5 用）

`PAPER_scalability.{png,pdf}` 由 `make_paper_figure.py` 从 `metrics.json`
单源读数生成（门禁与出表同源），不注册进 6 图清单，批量出图不会覆盖：

- **(a)** 到达规模 N → 单周期端到端墙钟（对数轴）。本档的形状是：
  `quota_*` 全程 8–9 s（只有 12 条进入二阶段）；单闸门 cap 档 325.4 s →
  279.6 s 基本持平；`full_baseline` 651.3 s → 7353.7 s。曲线从"随 N 线性"
  变成"随 n_sel 走"，这正是选择层起作用的直接体现。
- **(b)** 同一批 18 个点按 `n_sel` 重排：`n_sel≈12` 的点无论是 N=50 还是
  N=100 都落在 8–10 s，`n_sel=45/48` 的点落在 280–443 s。只登记对应关系，
  不作因果或机制说明；细线仅为同档位点的执行顺序连线，不是拟合。

其余 5 张（S01 设定表、R02 计数、R03 分段计时、R04 结构、R05 档位观察）
属内部审计图，需要时进附录，不占正文版面。图注已写入单 seed 单次观测、
跨格共享缓存、单周期快照与 F1 不进 f 的口径说明。

### 6.5 仍欠的技术债（需单独发话）

- **单 seed。** 18 格共用 seed 42，无重复、无区间；任何"同入选/同交付"的
  配对陈述都只在这一组任务实例上成立，扩到多 seed 前不进正文结论。
- **逐格检查点已落地**：`run.py` 每算完一格写 `_ckpt.json`（带配置指纹），
  中断后同指纹可续接，本次 9666.7 s 全程未重算已完成格。
- **措辞债**：`docs/` 与 PPT 里引用的旧数字（"收益损失 58%""保持率 ≥80%"
  "N=100 达 83.4%"）属 lasa_chengguan / 8 机 / 80 km 旧场景，按红线不得与
  本档混用。
- 论文 §4.5 正文仍是 `\placeholder{}`，本档数值可作为该段素材，但措辞须
  遵守 §6.3 三条与 `experiments/README.md` §9。
