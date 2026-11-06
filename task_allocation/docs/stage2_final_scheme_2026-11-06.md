# 第二阶段方法定稿：四条边界 × 六要素链条

> 2026-11-06 | **状态：方案与六项默认参数已拍板（回写比例 0 / ε 关 / L3b 关 /
> 失败 seed 不重采样 / Lagaros 按原文限制引用 / docs 入库）；
> P0 已落地（同日，见 §11 实施记录）；P1 入口条件已满足**
> 用户本轮裁定：四条边界（进化搜索 / 可行性保障 / 交付质量 / 评价与遗传传递一致性）
> + 最终方案「原始候选独立评价 + 可行性优先的 DMDE 竞争 + 可选硬约束修复候选
> + 可选受限交付精修 + 独立交付验收 + 完整搜索轨迹与消融实验」。
> 上位文档：`stage2_refactor_analysis_2026-11-06.md`（为什么改）、
> `stage2_refactor_plan_2026-11-06.md`（怎么改 v1）。**本文为定稿，冲突处以本文为准**。
> 文献依据：Lagaros et al. (2023) 全文已获取并逐条核对（Zotero `UEDCMGSJ`），
> 勘误见 §7；v1 文档基于公开摘要的推断不再作为引用依据。
> 对照基线：`method-phase1` / `phase1-evolution-decode-mcf-polish` @ `46b9739`（标签不可移动）。

---

## 0. 拍板内容（验收源，逐条不得走样）

**四条边界**

1. **进化搜索边界**：明确哪些变化真正来自 DE/DMDE。
2. **可行性保障边界**：明确哪些修复操作恢复硬约束，以及修复失败时如何处理。
3. **交付质量边界**：明确精修能改变什么、不能改变什么，以及最终如何独立验收。
4. **评价与遗传传递的一致性**：如果修复后的表型参与选择，却无法对应回实际进入
   下一代的基因，那么搜索收益的归因仍然是不严谨的。

**最终方案（六要素）**

`原始候选独立评价` → `可行性优先的 DMDE 竞争` → `可选硬约束修复候选`
→ `可选受限交付精修` → `独立交付验收`，并以 `完整搜索轨迹与消融实验` 贯穿。

**定稿相对 v1 的三处收口**：① 「可行性优先的 DMDE 竞争」已拍板，v1 §6 第 1 项
（要不要 ε 比较器）不再悬置——默认 Deb 三规则硬可行性优先，ε / Lagaros 变体降为
**可选开关对照**；② 修复失败语义从「不讨论」升为**必须显式三分流**（§3.2）；
③ 一致性从「P3 再修」升为**贯穿全链的强制契约**（§5），这是本轮最重要的变更。

---

## 1. 三个解对象与唯一评价口径

一切边界靠「对象分离」实现。同一个 `f` 可以对三类解求值，但**必须带来源标签**，
禁止把三个数字混成一列比较。

| 对象 | 产生位置 | 允许的动作 | 评价标量 | 是否进入下一代 | 是否交付 |
|---|---|---|---|---|---|
| `S_raw(g)` | 基因 `cost_vector` → `inverse_phi` + 编码边界合法性 | 结构翻译；**零硬约束修复、零目标优化** | `f_eval^raw` | 是（默认遗传对象） | 否 |
| `S_repair(g)` | 层 2 对 `S_raw` 做硬约束可行性投影 | 只向可行移动；不动点迭代；**零成本比较** | `f_eval^repair` | 否（Baldwin 默认；回写比例可配） | 否 |
| `S_final` | 层 3 之后 + 覆盖门禁 | 受限精修 + 构造式覆盖补全 | `f_final`（独立评价器重算） | 否 | 是 |

**唯一目标函数保持不变**（项目铁律）：

```
f = W_hard·n_hard + w_exc·excess_m + W_skip·n_skip + w_late·L + λ·path
λ = 0.1 全实验统一；唯一距离 = 垂直切面 DEM 三维有向航程；落盘坐标禁 round
```

- `f` 是**问题目标**，不是算法内部选择标量。Lagaros Eq. 9b 的乘性变换若引入，
  只能作用在**内部选择标量 `F_sel`**（§6.2），不得改写 `f`，否则毁掉 big-M 标定、
  `w_late=0.01/s` 与航程的固定汇率、以及与 `method-phase1` 的可比性。
- 与 `method-phase1` 对照**只比指标层**（完成率、按时率、航程、迟到、`n_hard`、
  墙钟、`n_evals`），不比 `f` 绝对值。
- 覆盖维度必须分列 `n_skip^eval` / `n_skip^final`，**禁止混用同一个数字**
  （`probe_selection_gap.py` 已证「先选后打磨 ≠ 先打磨后选」）。

---

## 2. 边界 A：进化搜索边界

### 2.1 什么算「真正来自 DE/DMDE」

一条改进记入 `Δ_DE` 当且仅当**同时满足**：

1. 可追溯到基因 `cost_vector` 的变异 / 交叉 / 反映射所产生的变化；
2. 在**同一评价口径**下重评（统一关闭修复与精修，或统一开启）仍然存在；
3. 不是解码期确定性贪心（成本合并、最近邻定序、MCF 费用最小）带来的
   —— 这些一律记入 `Δ_repair` 或 `Δ_polish`。

⇒ 判定口径是「**基因可追溯 + 同口径重评**」，不是「出现在进化代里」。
现 `build_solution` 在每代每体施加同一份局部改进却不回写基因，属**事实上的
Baldwin 式全体同口径施加**，不产生可遗传的个体间差异，不能记入 `Δ_DE`。

### 2.2 强制记账与消融

- 三级嵌套消融的开关组合（P4 落地为实验 config，逐档固定 seed 集与搜索预算）：

| 档 | 进化 | 修复 | 精修 | 回答的问题 |
|---|---|---|---|---|
| `repair_only` | 关（gen=0，仅初始解解码） | 开 | 关 | 修复地板：解码+修复单独能做到哪 |
| `evolution` | 开 | 开 | 关 | 加进化后的边际（C−A） |
| `full` | 开 | 开 | 开 | 再加精修的边际（A−B） |
| `raw_only`（诊断用） | 关 | 关 | 关 | `S_raw` 地板：完全不修复能到哪 |

  进化边际 `C−A`、精修边际 `A−B`，顺序不能反。
- 2×2 开关对照（开 / 关 进化 × 开 / 关 精修）四档同 seed 配对：
  `interaction = (f00 − f01) − (f10 − f11)`。
  step2 已证**强交互、收益非可加**，故报告必须并列主效应与交互项，
  **禁止写「独立收益」**。
- 三本账分列：`Δ_DE` / `Δ_repair` / `Δ_polish`，每笔带来源 `ledger_id`。
- 复用现成诊断，不另建框架：`step1_genotype_phenotype.py`（基因型→表型指纹、
  精英冻结、碰撞率）、`step2_2x2.py`、`step3_modules.py`（`prod` / `reversed`
  双基准）、`verify_pop_sensitivity.py`（种子敏感性）、`probe_selection_gap.py`。

### 2.3 诚实口径

- 精英冻结（代占比 0.89、总增益 11.62、改进代数 ≈0.2）根因是**子代系统性劣于
  初始精英**，不是空间坍缩（碰撞率 0）。「剥掉解码期优化后精英更新必然上升」
  仍是**待验证假设**，设为 P1 前置闸门，不达标不推进 P2。
- 60:1 的「进化占比 68.7%」结论在 pop / seed 改变后跳变，只能写成**初始种群依赖**。
- 冒烟档（pop8×gen8）结论不可外推正式档（pop50×gen200）；`max_iter` 同时是
  退火调度分母，改预算即换轨迹。

---

## 3. 边界 B：可行性保障边界

### 3.1 硬约束清单与修复白名单

| 硬约束 | 唯一判据 | 允许的修复动作 | 归属 |
|---|---|---|---|
| 任务唯一性 | `validate_unique_tasks` | 结构去重 | 编码层 |
| 航程 / 能源 | `check_range_constraint` | 弹出/改派任务直到航程 ≤ limit | 层 2 |
| 机巢合法性 / 容量 | `check_nest_capacity` | 换巢、MCF 分配 | 层 2 |
| 覆盖 / 完成 | `coverage_gate` + `_load_coverage_fixed_point` | **构造式补全** | 层 2 |
| 先后顺序（同一航次内） | `sequence_order` | 构造性归位 | 层 2 |
| 不可达两类 | UNREACHABLE 判定 | 任务池出池（非违约） | 层 1 前置 |

**修复层只做「必须满足」**：每轮动作 `reason=hard`、只向可行移动、记录
`Δviolation`；**禁止**以成本下降作为修复成功判据，禁止在层 2 做 `_consolidate`
成本合并、`_best_order` 的 NN / 航程裁决、终点巢费用最小化（这些是目标动作）。

**现状（`decode_repair.py:97-163`）**：主循环 `for _ in range(6)`，每轮
`_consolidate → [_prune 消融档，生产关] → _repair_range → _choose_end_nests`，
`state`（routes + end_nests 快照）与前轮相同则提前 break；随后覆盖补全
（`coverage_first` 走 `_load_coverage_fixed_point`，否则 `_complete_coverage`）
→ `_reorder_for_deadlines` → `_repair_sequence` → 末段再闸
`_repair_range / _choose_end_nests / _repair_range` 三次。
`decode_constructive.py:171` 构造式解码为 ≤3 轮。

**目标规则（P2 落地）**：

1. `max_passes` 显式进 config（主循环默认 6、构造式 3；改值须开关对照）；
2. 每轮记 `violation_vector` 快照进台账；收敛判据 = 向量全零 **或** 快照重复；
3. 快照重复但向量非零 ⇒ `REPAIR_FAILED` 分流（§3.2），禁止继续静默轮转；
4. 每轮动作 `reason=hard`；「链长归零」不是目标，**`reason=opt` 动作数归零**
   才是（`_consolidate` 的成本合并、`_best_order` 的 NN / 航程裁决在 P1
   移出每体路径）。

### 3.2 修复失败必须显式三分流（本轮新增，v1 缺）

修复结果只能是以下三种之一，**不得静默降级为可交付解**：

| 结果 | 判据 | 处置 |
|---|---|---|
| `REPAIRED` | 硬约束向量全零 | 正常进入竞争，台账记 `reason=hard`、动作数、Δviolation |
| `UNREACHABLE` | 真几何不可达（全巢往返超满电）或机队服务不可达 `no_uav_in_range` | 出池；服务类单列 `n_unreachable_service`；完成率分母排除 UNREACHABLE |
| `REPAIR_FAILED` | 非不可达但 `max_passes` 内未收敛 | **不得进入交付**；标记 `infeasible` 保留在候选池供诊断，独立计数 `n_repair_failed`；该 seed 若无可行交付解则**如实判失败** |

红线：**不存在「超期 / 过期淘汰」**；出现 `eliminating`（非不可达）= bug，须修到 0。
禁止用淘汰类术语掩盖修复失败。恒等式 `arrived = done + unreachable + remain` 与
完成率（分母排除 UNREACHABLE）恒 100% 在每阶段逐条断言。

**完成率统计口径（含 REPAIR_FAILED 的显式处理，2026-11-06 定）**：

```
分母       = arrived − n_unreachable
分子       = done
n_unreachable = n_unreachable_geom + n_unreachable_service   （两类单列）
REPAIR_FAILED 的任务 ⊆ remain，不计入 unreachable：
  · 交付层构造性补全救回 → 计入 done；
  · 最终仍未交付 → 该 seed 无可行交付解，如实判失败（不静默重采样、
    不换 seed 重跑掩盖），单列 n_seed_failed；
  · 失败 seed 的指标不并入均值，单独列示。
```

「完成率恒 100%」的精确表述：**成功 seed 的完成率恒 100%，失败 seed 单列**，
两者不得混写在一个数字里。

### 3.3 完成语义的承担者

完成语义由 `coverage_first` / `_load_coverage_fixed_point` **构造式装载**承担，
覆盖门禁**只作终态校验与补漏**。exp04 E1E2 已证「门禁单独上场时覆盖指标恒真而
完成率 −5pp」，把门禁设成唯一主口径会重演该失败。

---

## 4. 边界 C：交付质量边界

### 4.1 精修能改什么、不能改什么

| | 允许（L3a 纯几何，默认开） | 禁止（默认关，L3b 显式开关 + 单独记账） |
|---|---|---|
| 段内定序 | ✅ 同一航次内按 `sequence_order` 防逆序 | — |
| 路径 | ✅ 2-opt / Or-opt，碰撞复核回退 | — |
| 终点巢 | ✅ 最小费用分配（登记 `reason=opt`） | — |
| 任务归属 | ❌ | relocate / swap（跨机改派） |
| 执行集 | ❌ | 除构造式覆盖补全（那是可行性，不是精修） |
| 硬约束集合 | ❌ | 一律不得放宽 |
| 口径 | ❌ | 不得改 `λ`、权重、距离函数、门禁开关 |

术语红线：图与正文禁写「Dubins 平滑」（实为 Chaikin / Catmull-Rom）；
「控制点」称「图节点」。

**终点机巢的定位（2026-11-06 拍板）**：MCF（`solve_end_nest_assignment`）解的是
**给定每架机访问序列（路径）条件下的终点巢最优分配**——是**条件最优**，不是
「归属 + 顺序 + 巢」的联合最优。论文与代码注释一律写「给定路径下的最优终点机巢
分配」，禁写「全局最优」。同理，回写比例 = 0 时 DE 竞争的结果也是条件性的
（依赖修复链行为，§5.2）。

**MCF 对照方案（P3 实验）**：层 1 用「最近合法巢」确定性投影（零费用比较），
层 3 才用 MCF（费用最优）；对照 = 两层 × {最近合法巢, MCF} 的开关组合同 seed
配对，指标 = 返航段航程、可行率（`check_nest_capacity` 的 overflow）、墙钟。
层 3 的 MCF 收益记 `Δ_polish`（opt）；层 1 的投影与 MCF 差异单独列示。

### 4.2 采纳判据统一

统一为 `f(cand) < f(cur) − ε` **且约束向量不退化**。现状有三个阈值、语义各异
（P3 统一时须逐一迁移并留新旧对照）：

| 阈值 | 现值 | 作用分量 | 单位语义 |
|---|---|---|---|
| `monotone_polish_eps` | 1e-6 | f（`_polish_routes_monotone`） | 吸收浮点求和噪声 |
| `cost_polish_eps` | 1.0 | **航程**（成本 polish 的成本门） | 航程单位（米），**非 f** |
| `polish_eps` | 1e-6 | f（`polish_assignment`） | 吸收浮点噪声 |

⚠️ `cost_polish_eps = 1.0` 是**航程分量**阈值（stage_config 注释原文「航程严格
下降 > eps 才采纳」），与统一判据的 f 单位 ε **不是一回事**。统一后 ε 取 f 单位，
换算基准 `λ=0.1`：1 单位 f ≈ 10 m 航程 ≈ 100 s 迟到（`w_late=0.01/s`）；
迁移须开关对照，不得静默替换。必须修掉 `stage_runner.py:425` 已算出
`cand_fitness` 却未进入 `:430-435` 判据（现只看「航程严格下降」）的缺陷 ——
该通道允许「`f` 变差却被采纳」。禁止任何只按单一分量裁决的旁路。

### 4.3 独立交付验收

用**独立事后评价器**：不经过搜索链、不读缓存，从落盘的 `S_final` 重建解后重算
完成率、按时率、`n_hard`、航程、迟到、`n_skip`、`eliminating`。验收清单：

1. 完成率 100%、`arrived` 恒等式成立、`eliminating`（非不可达）= 0；
2. `gate_mode()` 恒 True、`coverage_gate` / `lexicographic_coverage` 双开；
3. 事后评价器数字与搜索链自报数字逐位一致（口径一致性断言）；
4. `pytest task_allocation/tests tests -q` 全绿 + exp01 审计 25/25；
5. 与 `method-phase1` 同场景、同 seed、同几何实例、同搜索预算对照，
   同 seed 配对留档，配对 t 检验如实声明（不显著就写不显著）。

---

## 5. 边界 D：评价与遗传传递的一致性（本轮最重要）

### 5.1 问题陈述

用户点名：「如果修复后的表型参与选择，却无法对应回实际进入下一代的基因，
那么搜索收益的归因仍然是不严谨的。」

现状有两处具体缺陷：

- `dmde_solver.py:225` 用 `if child.fitness < population[i].fitness` 让**修复后
  表型的适应度**参与父子竞争，但进入下一代的是**原始基因**（Baldwin）。
  选择看见 `S_repair`，遗传传递 `g_raw`，二者不是同一对象。
- Lamarckian 分支把 `re_encoded.fitness = child.fitness` 直接赋值（**未重算**），
  使回写后的基因携带的是「修复前表型」的适应度，属于**双重错配**。

实测后果（`selection_gap_report.md`）：开启交付后处理时 `f_eval` 与 `f_final`
的 Spearman ≈ 0.059、反转率 ≈ 0.477、选择价值 1984.81 → 154.72；
d150 档 top1 重合率 ≈ 0.333、regret ≈ 49.00、DE 选中个体平均排 2.7/100。

### 5.2 一致性契约（强制，任何阶段不得绕过）

每个进入下一代的个体必须可追溯到四元组：

```
(g_raw, S_eval_used, inherited_g, ledger_id)
```

且满足：**`S_eval_used` 与 `inherited_g` 解码所得对象在同一评价口径下可比**。

**定位修正（2026-11-06 拍板）**：R1 / R2 不是两条并列路线，而是**同一个机制
（修复表型是否回写基因）在「回写比例」这一个连续参数上的两个取值**——
回写比例 = 0 即纯 Baldwin（Lagaros §2.4 原文口径 *"used solely for fitness
function evaluation purposes"*）；回写比例 > 0 即 Lamarck（须重算适应度）。
R3（竞争与修复候选池结构性双轨）属另一维度的改法，暂不实施。

| 参数点 | 做法 | 一致性 | 代价 |
|---|---|---|---|
| 回写比例 = 0（**默认，已拍板**） | 修复表型只用于评价；台账强制记 `selection_object=S_repair` / `inherited_object=g_raw`；`f(S_repair) − f(S_raw)` **记修复账，不记 DE 账** | 归因严谨；但选择看见的与遗传传递的是两个对象，竞争结果是**条件性的**（依赖修复链行为，与 §4.1 终点巢的条件最优同类） | 必须如实报告 regret 与 top1 重合率 |
| 回写比例 > 0（对照档） | 修复表型按比例回写基因，且**必须重算 `re_encoded.fitness`**（现代码直接赋值 `child.fitness`，属双重错配）；过「表型复核 + 范围控制 + 种群方差护栏」闸门 | 选择看见与遗传传递是同一对象 | 会改数值，须重跑基线 |

**默认回写比例 0；对照档取值与闸门在 P2 开工前定义**（用户拍板）。
理由：保持与 `method-phase1` 可比、符合「可选硬约束修复候选」的措辞、
且无需重写 DE 主循环。

### 5.3 一致性验收指标（从「诊断」升为「门槛」）

- `probe_selection_gap.py` 的 `regret`、top1 重合率、Spearman / Kendall、
  并列最优数：R1 下必须**如实并列报告**，不得只报有利的一档。
- 关掉交付后处理总闸时，Spearman 必须仍为 1（口径自洽的下界检查）。
- 三本账 `Δ_DE` / `Δ_repair` / `Δ_polish` 之和与 `f_final − f_baseline` 对账，
  差额必须可解释到台账条目（含交互项单列）。
- 若 R1 的 regret 超过预设阈值，必须**显式切换 R2 对照**或如实声明
  「选择价值损失」，不得用措辞遮盖。

---

## 6. 六要素链条落地

| # | 要素 | 实现落点 | 开关 | 验收 |
|---|---|---|---|---|
| 1 | 原始候选独立评价 | `S_raw` 独立求值 `f_eval^raw`；剥掉每代每体的目标改写（`_consolidate` 成本合并、`_best_order` NN/航程裁决） | `projection_only=True` | 同基因重复评价 bit-exact；`reason=opt` 动作数 = 0 |
| 2 | 可行性优先的 DMDE 竞争 | 默认 Deb 三规则（可行优于不可行 / 两可行比目标 / 两不可行比违反量），作用在**内部选择标量 `F_sel`**；DE 变异、交叉、反映射**一律不动** | `compare_mode`（默认 `deb`；`epsilon` / `lagaros` 为可选对照档） | 完成率恒 100%；精英冻结指标达 P1 闸门；与 v1 同一套 `CompareSpec` 组件，不重复建设 |
| 3 | 可选硬约束修复候选 | `decode_mode="constructive"` + `projection_only` 子模式；修复比例 / 回写比例可配；失败三分流 | `repair_replacement`（默认 0 = Baldwin）、`max_passes`、`repair_enabled` | `n_repair_failed` 独立计数；硬约束向量全零；不动点收敛断言 |
| 4 | 可选受限交付精修 | L3a 默认开、L3b 默认关；采纳判据统一为 `f` | `polish_enabled`、`polish_relocate` | 完成「几何 vs 救回任务」收益拆分；L3b 单记 `task_to_uav` 变动 |
| 5 | 独立交付验收 | 独立事后评价器重建 + 重评 | — | §4.3 五条门槛 |
| 6 | 完整搜索轨迹与消融 | `DecisionLedger` 默认开（纯观测）+ `ConstraintSpec` 唯一判据 + 三级/2×2 消融 | `ledger_enabled=True` | 台账 `reason` / `constraint_id` 覆盖率 100%；每笔 Δ 可回溯到 `ledger_id` |

**`DecisionLedger` 最小字段**（在 v1 基础上补 `selection_object` / `inherited_object` / `ledger_id`）：

```
(代, 层, 算子, constraint_id, action, object, from, to, reason=hard|opt,
 Δf, Δviolation, accepted, gate_reason,
 selection_object, inherited_object, ledger_id)
```

**关于要素 2 的文献落点**：Lagaros 的 8 个新变体分两类（§7 勘误 3）——
FeasRulesNEW 走 Eq. 9b 的 `F(x) = max(f_best_feasible, f(x)) × p_violation`
（**适应度变换**），EconstNEW 走 Eq. 13 的 ε-level **比较规则**。故
「可行性优先」的默认档采用 Deb 三规则；ε / Lagaros 变体作为**内部选择标量层的
可选对照**，两者都不碰问题目标 `f`。这同时收口了 v1 §6 第 4 项待拍板问题：
不是「只改比较算子」，而是「只在内部选择标量层引入，并区分 Eq.9b 与 Eq.13 两类」。

---

## 7. 文献原文核实（本轮新增，10 条勘误）

来源：Lagaros, Kournoutos, Kallioras, Nordas (2023),
*Optimization and Engineering* 24:2251–2298, DOI 10.1007/s11081-022-09782-9，
Zotero 全文 `storage/UEDCMGSJ/…pdf`。页码为**期刊印刷页码**，PDF 页序 = 页码 − 2250。

| # | v1 / analysis 中的断言 | 原文事实 | 判定 |
|---|---|---|---|
| 1 | §2.4「明确提到 repair probability parameter，用户只需调修复概率」 | p.2264 §2.4 首段：*"The use of the repaired individual depends principally on the nature of the problem and the developer decision and varies between either replacing the original individual with the repaired one, or only a small percentage, or every infeasible individual"*；`repair probability parameter` 只出现在 Chootinan & Chen (2006) 的具体方法描述 | **需校正**：原文支持的是**回写 / 替换比例由开发者决定**（Baldwin↔Lamarck 连续谱），不是「是否执行修复的概率」。用户「修复 = 可选组件」仍成立，但依据须换成首段 |
| 2 | 「Lagaros2023 §2.4 中 Baldwin/Lamarck 选择必须显式」 | 全文**无** `Baldwin`、`Lamarck`、`memetic` 字样；原文只用描述性语言 "used solely for fitness function evaluation purposes or for replacing the initial individual" | **需标注**：这是我们的术语化解读，不能写成原文术语 |
| 3 | 「Eq. 8a–d / 9b 是**选择判据**，不是目标函数；若改成乘性目标会毁标定」 | p.2269–2270：Eq. 9b 明写 *"The fitness function of an individual is calculated as the product of p_violation with the maximum among the objective function value…"*，`F(x)=f(x)` 若全可行，否则 `max(f_best_feasible, f(x)) × p_violation`。而 Eq. 13（EconstNEW）才是 ε-level **比较规则**。表 2 (p.2275) 列明：FeasRulesNEW1–4 = Eq.(8a–d, 9)；EconstNEW1–4 = Eq.(8a–d, 12, 13) | **需校正（最重要）**：原文确为**乘性适应度重标定**（评价层），不是纯比较算子；两类新变体机制不同。工程裁定「不动问题目标 `f`」不变，但理由改为「原文作用在算法内部 fitness，我们只将其映射到 `F_sel`」 |
| 4 | 「三元比较 (目标, 违背条数, 违背量) 向量化」 | Eq. 8a = 最大违反量；8b/8c/8d = 最大 / 均值 / 中位数违反量 `× (1 + n_constviol/n_const)`；再与 `max(f_best_feasible, f(x))` **相乘** | **需校正形式**：是乘积式，不是字典序三元组；8a 甚至不含违反条数 |
| 5 | 「§2.4 / §5 把局部搜索列为预算受控、只对选定个体调用的组件」 | 全文 `local search` 仅 3 处，均为 §2.6 *other* 中 Rosso et al. (2022) 的 "a new local search operator … to help localize the feasible region"；无预算纪律、无「只对选定个体」表述 | **无原文依据，属过度引申**：改挂到本项目自己的两条方法论铁律（成本优化必须作用于交付解 / 解码期算子干扰 DE 搜索） |
| 6 | 「组合 CHT（§2.3）同一时刻只有一个生效」 | p.2263–2264：「only one CHT being active at a time」是 **Malan (2018) landscape-aware** 的具体方法；Mallipeddi & Suganthan (2010) 的 ensemble 恰是**四种 CHT 并行、各管一个种群** | **需校正**：仅对特定方法成立。这反而**支持**本定稿「惩罚 + 修复 + 精修并存」的组合式设计；但「被修复与未修复个体同台比较」仍须由 §5 / `CompareSpec` 回答 |
| 7 | 「ε-constrained 见 §3.1」；「采用 Lagaros §3.1 的 ε 调度」 | §3.1 = penalty methods；§3.2 = feasibility rules；**§3.3 = ε-constrained**。原始调度为 Takahama & Sakai Eq. 11；**新变体采用 Fan et al. (2018) Eq. 12**（含 `rk` 可行解比例、`TC`、`cp`、`τ`、`α`） | **章节号与公式号均需校正**；实现若用 ε 应照 Eq. 12 并显式声明参数 |
| 8 | BU 相关：引用 "directly updating variable bounds…" / "rather than penalty or other fix-ups" 于 Lagaros；「第 4 类 projection/repair」 | p.2265 §2.4 末确有 *"Despite the BU method requiring constraint variables pre-categorizing"*，且 BU 被**归入第 4 类修复算法**；但前两句出自 Gandomi & Deb (2020) 原文，Lagaros 未写；Rahimi et al. (2024) 的 "twist the search space" 亦为二次引用 | **部分支持**：保留 "pre-categorizing" 与分类判定；其余改标原始出处。论文仍用「受 BU 思想启发的航程可行性投影」，不写「UAV 版 BU」 |
| 9 | 摘要引用 "consider the level and number of constraint violations … for selection of individuals within a population" | 原文摘要、p.2295、p.2296 结论一致 | ✅ **支持**，但这是**目的**不是机制；机制见勘误 3 |
| 10 | 「新变体性能优于已有 CHT」 | p.2295 作者自限：*"the derived conclusions concern the 20 numerical examples examined herein and the specific PBA and CHT variant implementation and can thus not be generalized"*；p.2296：*"there is no unique constraint handling technique that consistently performs best in any type of problem"* | **论文引用必须带这两条自限**，不得写成「Lagaros 证明新变体普适更优」 |

另：v1 §1.4 关于 Zhao Ming 2017「原意」的引用风险仍成立（本会话未获取该文全文，
不臆造 DOI）；Zhao Ming 仍只作**编码与算子母本**，「软约束惩罚驱动、不强制修复」
写成本阶段的**设计选择**，依据 Lagaros §3.1 惩罚类 + §3.2 可行性规则。

---

## 8. 修订后的阶段与门槛

| 阶段 | 对应六要素 | 主要内容 | 行为变化 | 前置 / 验收门槛 |
|---|---|---|---|---|
| **P0 契约与轨迹** | 要素 6 | `ConstraintSpec` 唯一 `judge()` **接口**；四处另写判据（时间窗 / 时序 / 机巢容量三份账 / 采纳判据）统一到该接口但**保持现有语义不变**，语义漂移先记录进台账、修复后移 P1/P2；`DecisionLedger` 默认开（纯观测）；`dmde_solver.py:244-246` 的 `except: pass` 改计数+日志 | **零（行为变化边界见 §8.1）** | 与 `method-phase1` 数值 bit-exact；台账字段覆盖率 100%；全量测试绿 |
| **P1 原始评价 + 可行性优先竞争** | 要素 1、2 | `projection_only` 剥解码期目标改写；`CompareSpec` 默认 Deb 三规则，ε / Lagaros 变体为可选对照（**ε 默认关**，已拍板）；MCF 在层 1 降级为「最近合法巢」确定性投影 | 隔离、可开关 | **前置闸门（数字线）**：冻结代占比 < 0.89（step1 口径）、精英总增益 > 11.62、改进代数 > 0.2、表型碰撞率不显著上升、成功 seed 完成率 100%；任一未达标**停止推进 P2**；关总闸时 `f_eval`/`f_final` Spearman 仍须 1.0 |
| **P2 修复候选 + 一致性契约** | 要素 3、边界 D | 修复比例 / 回写比例可配；失败三分流 + `n_repair_failed`；四元组追溯；修 `re_encoded.fitness` 重算；`n_skip^eval / ^final` 分列；`λ_cov` 标定 | 中等 | `regret` / top1 重合率如实并列；三本账对账差额可解释；完成率恒 100%、无 `eliminating` |
| **P3 受限精修 + 独立验收** | 要素 4、5 | L3a/L3b 分账；采纳判据统一为 `f`；独立事后评价器；「几何 vs 救回任务」收益拆分 | 中等 | §4.3 五条门槛；同 seed 配对留档；重跑全部基线，配对 t 检验如实声明 |
| **P4 消融与文献对标** | 要素 6 | 三级嵌套消融 + 2×2 交互；Deb / ε / Lagaros 两类变体开关对照；统一事后评价器重评 phase1 与本版交付解 | 中等 | 主效应 + 交互项并列（禁写「独立收益」）；论文表格可复现；Lagaros 引用带 §7 勘误 10 的自限 |

### 8.1 P0 的行为变化边界（明确清单）

**允许（不改变输出解）**：

- 新增文件：`constraints/` 注册表、`decision_ledger.py`；
- 在现有调用点插桩与计数（不动判定逻辑本身）；
- 四处另写判据统一到 `ConstraintSpec.judge()` 的**接口层**——各处现语义
  原样包一层，判定结果必须与统一前逐位一致；
- `dmde_solver.py:244-246` 的 `except: pass` 改计数 + 日志；
- `DecisionLedger` 默认开（纯观测，落盘可关）。

**禁止（会改数值，全部后移）**：

- 采纳判据把 `cand_fitness` 用起来（`stage_runner.py:425`）→ **P3**；
- 时间窗 / 时序 / 机巢容量三处语义漂移的**实际修复** → **P1/P2**
  （P0 只用台账量化漂移率：同一解四层判据的不一致条数）；
- 任何 `f` 公式、权重、门禁开关、距离口径的改动 → **全程禁止**；
- 回写比例、ε、L3b 的任何非默认取值 → P2 前定义，P0 一律默认值
  （0 / 关 / 关）。

**验收口径**：同一 config、同一 seed，P0 前后 metrics 落盘**逐位一致**；
`pytest task_allocation/tests tests -q` 全绿；台账对 `build_solution` 每轮动作的
`reason` 字段覆盖率 100%；另需报告一份「四层判据漂移率」基线供 P1/P2 使用。

纪律不变：严禁多实验并行；跑时勿渲图；挂起加 `-u`；长跑勿 `| tail`；
跨 seed 对比用 `seed_summary` / `per_seed`，顶层 `summary` 仅首 seed；
各实验自持 config/build/paths/metrics/plot，禁建 `_shared/`；每阶段单独提交便于 bisect。

---

## 9. 六项参数已拍板（2026-11-06）

| # | 参数 | 拍板值 |
|---|---|---|
| 1 | 回写比例 | **默认 0**（纯 Baldwin，即 §5.2 参数点 0）。迁移方式：**新参数 `repair_replacement`**（不翻转旧开关默认值）——旧布尔 `lamarckian` 改 None 哨兵作废弃映射：显式 True ≡ `repair_replacement=1.0`、显式 False ≡ 0；`lamarckian_patrol_writeback=True` 且未显式设比例时保义为 1.0（M2-1 借道 M1 调用门的历史语义）；三者均未设 ⇒ 0（拍板默认）。**对照档取值 1.0**（完整 Lamarck，即修好 fitness 重算后的正确版现行行为；中间值 0.5 仅在结果可疑单调时补）。比例取 0/1 时**不消耗 RNG**（逐位可复现），(0,1) 每次替换事件抽签。**对照档闸门（须全过）**：①表型复核——回写基因重解码 fitness 与回写前 `S_repair` fitness 逐位一致，否则该次回写计失败、保留原基因（`lamarckian_reencode` 的 Q2.1/Q3.2 已有）；②fitness 重算——`re_encoded.fitness` 必须重算（bug 修复，无论比例多少）；③种群方差护栏——每代基因型多样性 ≥ 初始代 ×0.2（`diversity_series` 纯观测，跌破记录不中断）；④数值护栏——成功 seed 完成率 100%、`eliminating`=0、f 中位数不劣于 Baldwin 档（劣化如实报告）；⑤报告纪律——Baldwin/Lamarck 两档并列 + Δ 账本，禁只报优胜档 |
| 2 | ε | **默认关闭**。若启用：**Eq.12（Fan et al. 2018）**，不选 Eq.11——原文 EconstNEW 变体明确采用 Eq.12（"the formulation proposed by Fan et al. (2018) is adopted, as given in Eq. (12)"），且其 rk≥α 放松分支（可行比例过高时主动放松 ε 防塌进可行域）正好对症精英冻结病理；Eq.11 单调收敛无 PFS 反馈。参数（全部取原文区间中值）：**γ=0.5**（θ=γ·Npop，pop50 ⇒ θ=25）、**TC=0.4·maxFEs**（pop50×gen200 ⇒ maxFEs=10000 ⇒ TC=4000 次评价 ≈ 80 代）、**cp=5**（区间 [2,10] 中值）、**τ=0**（放松分支最保守幅度，(1+0)·ϕmax=ϕmax；检验放松效应再对照 τ=0.5）、**α=0.5**（可行比例过半才触发放松分支；敏感性扫 {0.2, 0.8}）。两个落点纪律：**计数口径按 FEs（评价次数）不是代数**（台账按 FEs 计，禁混用 t）；**对照档定位 EconstOR**（原文表格：Eq.10 比较规则 + Eq.12 调度，即 ε 版 Deb 三规则）——Eq.13 四分支（含 Eq.9b 乘性 p_violation）作可选第二档不进默认，引用最省事且与「只动 `F_sel` 不动 `f`」一致 |
| 3 | L3b（跨机 relocate/swap） | **默认关**，保留显式开关，先做收益拆分再定。**已落地（2026-11-06 裁定②）**：统一总闸 `l3b_enabled`（默认 False）同时约束两个通道 + 旧 config 保义映射，见 §11.5；收益拆分仍属 P4 实验层，未预先据此改默认 |
| 4 | 失败 seed | **不静默重采样**：如实判失败、单列 `n_seed_failed`（§3.2 完成率口径） |
| 5 | Lagaros 引用 | **按原文限制引用**：必须带 §7 勘误 10 的两条作者自限；勘误 1–8 同步生效 |
| 6 | docs 入库 | **入库**：三份 stage2 文档单独提交（2026-11-06） |

实施节奏（用户指令）：**P0 先行**；回写与 ε 对照的参数定义在 P2 前完成。
**已于 2026-11-06 晚落定**（§9 第 1、2 项），P2 开工。

### 9.1 P2 拍板细则（2026-11-06 晚，用户采信助手建议后开工）

- **迁移方式选「新参数」不选「改默认值」**：拍板的是比例（0.0–1.0 连续语义），
  Lagaros §2.4 三种回写模式（全部/小比例/逐不可行个体）本身就是比例语义，
  布尔表达不了中间值；且现行 `lamarckian=True` 捆绑「回写基因 +
  `re_encoded.fitness = child.fitness` 直接赋值不重算」双重语义，直接翻转
  默认值会把 bug 一起埋掉并断掉 method-phase1 的 config 复现路径。
  新参数 + 旧参数映射（True ≡ 1.0）保住旧 config；「fitness 重算」拆成独立
  修复——**无论比例取多少都必须重算**，这是 bug 修复不是开关选项。
- **对照档 1.0 的科学问题**：「修复收益能否经基因遗传传递」——0 vs 1.0 是
  最干净的极端对照；Lagaros 原文也只讨论「全部 vs 小比例」两级，没有扫
  中间值。
- **ε 选 Eq.12 的引用纪律**：按拍板 5「按原文限制引用」，EconstNEW 用什么
  我们就引用什么；Eq.11（Takahama）需另行引用且无 PFS 反馈。
- **默认档行为变化声明（P2 起生效）**：`repair_replacement` 默认 0 意味着
  master 默认路径从「每代回写」变为「纯 Baldwin」——这是拍板的方向性变化，
  不是回归 bug。**凡需复现 method-phase1 数值的运行（含 P1 闸门工具的
  base 锚点臂）必须显式 `lamarckian=True` 或 `repair_replacement=1.0`**；
  注意 fitness 重算修复使对照档 ≠ method-phase1 的逐位复现（预期内，
  method-phase1 逐位复现一律 checkout tag）。

---

## 10. 诚实口径与本文不做的事

- 本文是**方案定稿 + 文献核实**；定稿当时未修改任何生产代码、未跑新实验、
  未改 `Chapter3_README.md`（§4.8/§4.9/§4.10 是 `method-phase1` 的冻结描述，
  重构未开始不应覆盖）。**P0 已于同日实施（§11），行为边界严格限于 §8.1
  的『允许』清单**。
- 「精英更新频率上升」「可行率与交付质量同时提升」「修复链更干净 ⇒ 搜索更有效」
  三项均为**待验证假设**，不是本方案保证的结果；P1 前置闸门正是为此。
- `S_final` 的重放口径仍受 §4.10 的下界限制（未覆盖 `polish_assignment`
  每候选重放）；重构后须用新口径重测，不得沿用旧数字。
- 文献核实已覆盖 Lagaros2023 全文（文字层提取，3 处图片未导出；公式以正文
  文本与 Eq. 编号为依据，若需逐符号核对建议看 PDF 第 19–22 页版面）。
  Gandomi & Deb (2020)、Rahimi et al. (2024)、Salcedo-Sanz (2009)、
  Zhao Ming (2017) **仍为二次引用**，未获取全文，不臆造公式与 DOI。
- 范围：若沿用 pop8×gen8 冒烟档，结论不可外推正式档；纯几何 SRP 实例结论
  不外推 DEM 有向代价与多周期滚动。

---

## 11. 实施记录

### 11.1 P0 契约与轨迹（2026-11-06，已落地）

状态：**完成**（小步提交，逐个推送）。落地内容与验收如下：

| 步 | 提交 | 内容 |
|---|---|---|
| C1 | `feat: P0-a ConstraintSpec 约束注册表 + 四处另写判据收口` | `constraints/spec.py`（注册表 + judge() 全部委托既有实现）+ `constraints/criteria_drift.py`（漂移测量库）；`feasibility.py` / `tour_cost.py` / `coverage_gate.py` 判定接线（现语义原样包一层） |
| C2 | `feat: P0-b DecisionLedger 决策台账` | `decision_ledger.py`（reason=hard|opt 必填、事件有界、计数全量、外部计数源并账）；`stage_config.ledger_enabled/ledger_max_events`；`evaluator` 挂载台账 + `_ORDER_WINNER_COUNTS`/`_WRITEBACK_STATS` 并账 |
| C3 | `feat: P0-c 插桩 + 回退不静默` | `decode_repair` 各算子逐动作入台账（`_consolidate` 成本合并记 `reason=opt`、`_best_order` 航程裁决记 `opt`）+ 时序/时间窗判据视图收口；`stage_runner` 四个采纳判据经 `accept_*_view` + 精修采纳事件入台账；`dmde_solver` 选择事件（`selection_object=S_repair` / `inherited_object=g_raw|g_repaired_reencoded`）+ Lamarckian 回写 `except: pass` → 计数+日志（原静默回退已改） |
| C4 | `test: P0 契约测试` | `tests/test_p0_contracts.py` 19 项：判据委托等价、视图漂移条件锁死、台账纪律、回退计数、选择一致性四元组、台账开/关逐位一致 |
| C5 | `docs: P0 漂移基线 + 实施记录` | `docs/p0_criteria_drift_baseline_2026-11-06.md` + 本节 |

**验收结果**：

1. **bit-exact**：5 场景探针（默认 repair_chain / coverage_first+gate /
   constructive / patrol_writeback / no_polish）P0 前后签名**逐位一致**
   （交付解结构、f1/f2、final/best fitness、n_evals、cost_history、gate_stats、
   polish/memetic 诊断；浮点 float.hex() 对照）；台账开/关两档求解结果逐位一致；
2. **全量测试**：与基线完全一致——427 绿 + 6 失败；6 个失败全部是
   `test_task_region.py` 的 DEM/geojson **数据缺口**（`Chengguan_DEM.tif` 等大
   文件不在 git，本环境为 fresh clone），非代码回归。P0 新增 19 项全绿；
3. **台账纪律**：`reason` 覆盖率 100%（非法 reason 拒绝入账且
   `n_reason_missing` 可见）；事件流有界（`ledger_max_events`，计数恒全量）；
   历史计数器（order 胜率 / writeback 闸门）已并入 `ledger.snapshot()`；
4. **四层判据漂移率基线**：已出（
   `docs/p0_criteria_drift_baseline_2026-11-06.md`）。生产口径下四处漂移实测
   均为 0，但三条**结构性通道**存在：时间窗元组分叉（100%）、时序 1e-9
   松弛（边界组 75%）、采纳判据迟到门缺失 + 阈值单位混用（50%~72.5%）。
   P1/P2/P3 按该报告顺序消解。

**P0 行为边界（§8.1）逐条遵守**：未动任何判定语义、未动 f 公式/权重/门禁/
距离口径；采纳判据缺陷（`stage_runner` 已算 `cand_fitness` 未进判据）
**保持原样、仅入台账观测**（修复在 P3）；回写/ε/L3b 均保持现状默认。

**已知环境缺口（诚实声明）**：本环境缺 DEM 场景数据（`guangzhou/dem.tif`、
`lasa_chengguan/Chengguan_DEM.tif`、`guangzhou/building/`），exp01 生产口径
实验与 exp01 审计 25/25 无法在此运行；P0 数值验证以合成探针 + 全量单测为准，
正式档验收（exp01 审计、论文数字对账）须在带数据的环境补跑后回填本节。

### 11.2 P1 原始评价 + 可行性优先竞争（2026-11-06，已落地 + 闸门实测）

状态：**代码落地（开关门控）+ 前置闸门实测通过**。P2 开工尚缺用户拍板
（§9 第 1、2 项：回写对照档取值与闸门、ε 参数）。

**落地内容**（小步提交 C6–C9）：

| 部分 | 内容 |
|---|---|
| CompareSpec（`compare_spec.py`） | 统一比较算子四档：`penalty`（默认，= 现行硬贪婪，与 method-phase1 逐位可比）/ `deb`（三规则）/ `epsilon`（ε-constrained，**占位线性调度**，Eq.12 参数 P2 定稿）/ `lagaros`（FeasRulesNEW Eq.9b 乘积式 F_sel，8a–d 近似口径显式）；只作用于内部选择，f 不动；DE 替换接线（`dmde_solver`，penalty 档逐位不变） |
| projection_only（`decode_repair`） | 剥每代每体目标改写：`_consolidate` 成本合并跳过、`_best_order` 剔 NN 候选 + 选择键退为（迟到数, 迟到量）、终点巢降级为「最近合法巢」确定性投影（容量账与 check_nest_capacity 同口径）；验收量达标：解码层台账 `reason=opt` 动作数 = 0 |
| 闸门工具 | `ab_mechanism_3steps` 复用（step1 + common）加臂开关（默认逐位不变） |

**前置闸门实测（step1 口径，合成 SRP，scenarios 40:1,60:1 × seeds 11/22/33，
同 seed 配对；基线臂完整复现锚点：冻结代占比 0.891≈0.89、精英总增益
11.624≈11.62、改进代数 0.17≈0.2）**：

| 指标（6 run 均值） | base（penalty） | proj | deb | **proj+deb** | 闸门线 | 判定 |
|---|---|---|---|---|---|---|
| 冻结代占比 | 0.891 | **0.340** | 0.891 | **0.340** | < 0.89 | ✅ |
| 精英总增益 | 11.62 | **521.4** | 11.62 | **521.4** | > 11.62 | ✅ |
| 改进代数 | 0.17 | **1.00** | 0.17 | **1.00** | > 0.2 | ✅ |
| 表型碰撞率 | 0 | 0 | 0 | 0 | 不升 | ✅ |
| 完成率 | 100% | 100% | 100% | 100% | 恒 100% | ✅ |
| n_hard | 0 | 0 | 0 | 0 | = 0 | ✅ |

⇒ **闸门通过**（五项全达标），P2 获准入场。机制归因（台账可逐笔回溯）：
剥离解码期确定性目标改写后，候选间适应度方差恢复，精英从 0.89 冻结
降到 0.34——与分析文档 §1.2 的机制判断方向一致（仍属单场景合成档结论，
不外推正式档）。

**诚实口径（重要，两处）**：

1. **交付质量权衡实测存在**：refine=off（闸门档）下 proj 臂交付航程 10360
   vs base 5762（+80%）；探针场景（12 任务，pop16×gen20）：默认 15402，
   proj+交付精修 19257（**+25%**），proj 无精修 23245（+51%），默认无精修
   15413（解码期优化承担了绝大部分成本质量）。即现有交付精修（L3a
   2-opt/Or-opt+relocate）只能收回部分差距——被移出的「成本合并 / MCF
   联合择巢」需要在交付层补齐（P3 的 L3a/L3b 分账 + 终点巢 MCF 登记 opt
   正是此项）。**projection_only 默认值翻转应待 P3 交付层承接后由 P4 全量
   A/B 决定**；当前默认 False（与 method-phase1 逐位可比），臂开关供对照。
2. **deb 档在全可行合成场景与 penalty 同效**（逐指标同值）：其价值只在
   含不可行个体的场景显现（合成档 n_hard 恒 0，无可区分样本）；
   ε / lagaros 档为可选 A/B 实现（ε 调度 Eq.12 已由 P2-e 实装、8b–d
   近似口径见 compare_spec docstring），**非默认档**，参数见 §9 第 2 项。

**P2 入场条件**：闸门✅；尚缺用户拍板（§9 第 1、2 项）：① 回写比例对照档
取值与闸门（现默认 0=Baldwin；现行代码 `lamarckian=True` 与拍板默认值的
迁移方式需定：改默认 vs 新参数 `repair_replacement`）；② ε 启用时的
Eq.11 vs Eq.12 与 `TC/cp/τ/α` 参数。定稿前 P2 不开工。

### 11.3 P2 修复候选 + 一致性契约（2026-11-06 晚，开工）

§9 第 1、2 项已落定（§9.1），P2 按 §8 表开工。实施拆步（小步提交，风格同
P1 的 C6–C9）：

| 步 | 内容 | 行为变化边界 |
|---|---|---|
| P2-0 | §9 两项拍板落定 + 本注记（docs） | 无 |
| P2-a | `repair_replacement` 参数化（默认 0；`lamarckian` None 哨兵映射；patrol 保义）；`re_encoded.fitness` 重算修复；台账 select 事件补 geno_id（四元组齐）；`diversity_series` 纯观测（方差护栏数据源） | 默认档从每代回写变纯 Baldwin（拍板方向性变化，§9.1）；对照档 ≠ method-phase1 逐位（fitness 重算，预期内） |
| P2-b | `repair_max_passes`/`constructive_max_passes` 进 config；每轮 violation_vector 快照进台账；快照重复但非零 ⇒ `REPAIR_FAILED`（`solution.meta["repair_status"]` + `n_repair_failed` 计数）；交付拒绝 REPAIR_FAILED（回退 best REPAIRED，全无则显式失败不静默降级） | big-M 下可行恒胜不可行 ⇒ 正常路径逐位不变；仅「全种群不可行」的退化情形从「交付劣解」变「显式失败」 |
| P2-d | `n_skip^eval`（交付重建后 pre-polish）/`n_skip^final`（终态）分列；`CandidateFeatures.n_skip` + `CompareSpec.lambda_cov`（F_sel 侧，默认关；实例推导 λ_cov > λ·Δpath_max + w_late·ΔL_max）；w_late 显式化（提为带推导的显式量，不改值） | penalty 档逐位不变（λ_cov 仅非 penalty 档生效）；分列只加观测字段 |
| P2-e | ε 档从占位线性衰减改 Eq.12（Fan 2018）状态机：ε(0)=ϕθ（γ=0.5）；FEs<TC 且 rk<α ⇒ ε(k−1)·(1−FEs/TC)^cp（cp=5）；FEs<TC 且 rk≥α ⇒ (1+τ)·ϕmax（τ=0）；FEs≥TC ⇒ 0；TC=0.4·maxFEs；solver 每代喂 (FEs, rk, ϕmax)，FEs=gen·pop_size 口径显式声明 | ε 默认关 ⇒ 默认档零变化；`epsilon0/epsilon_tc` 占位字段标废弃 |

**P2 状态（2026-11-06 晚全部落地）**：P2-a `a662fe9`、P2-b `de49925`、
P2-d `d3b06c9`、P2-e `841d391`；全量回归 **498 passed**（P2 前 488 → +10：
λ_cov 7 + ε 3）。验收口径（§8 + Task #38）：完成率恒 100%、无 `eliminating`、
默认档行为边界逐条守住（penalty/λ_cov 关/ε 关均零变化）、三本账差额可解释
（台账四元组 + n_skip 分列 + 方差护栏序列齐备）；采纳判据统一为 f 的修复
归 **P3**（`stage_runner.py:425` 通道，未在本阶段触碰）。

**P1 闸门 base 臂锚点（重新运行闸门时必须）**：master 默认路径自 P2-a 起为
纯 Baldwin（回写比例 0）；重跑 §11.2 闸门以对比「旧 method-phase1（每代
Lamarckian 回写）vs 新默认」时，base 臂须显式 `repair_replacement=1.0` 保锚点，
否则闸门会静默比较 Baldwin-vs-Baldwin。perturbed 探针场景（12 任务、
pop16×gen20）与 ab_mechanism_3steps 闸门工具均适用此约定。

### 11.4 P3 实施记录（2026-11-06 晚）+ P4 待办

**状态**：P3 代码三步全部落地并提交，全量回归 **516 passed**（P2 结束 498
→ +18：P3-a 6 + P3-b 5 + P3-c 7）。**默认档零行为变化**——三步均为
「新增开关 / 纯观测字段 / 独立模块」，没有翻转任何生产默认值。

| 步 | commit | 落地 | 测试 |
|---|---|---|---|
| **P3-a** | `61eef97` | 采纳判据双视图：`cost_polish_accept_mode`（默认 `path`＝旧航程门，与 method-phase1 逐位可比；`f`＝`accept_f_view`）与 `cost_polish_eps_f=1e-6`（**f 单位**，1 f ≈ 10 m ≈ 100 s，与 `cost_polish_eps=1.0` 米不同单位、禁止复用）；修掉「已算 `cand_fitness` 却不进判据、只看航程分量」的缺陷通道；`accept_f_view` 约束向量补齐第 4 分量（航程超限，兼容 `criteria_drift` 旧调用）；两视图结果 + `drift` 落 `diagnostics["cost_polish_accept"]` 并 tick 台账 | 6 |
| **P3-b** | `9dd99ab` | L3a/L3b 分账：`l3a_geometric_actions`（2-opt / Or-opt / 终点巢 MCF / position）与 `l3b_relocate_actions`（relocate / swap）分列；`n_task_to_uav_changes`（重建解→终态解的归属映射差异，新增/删除/换机各记 1）；终点巢 MCF 在交付层台账登记 `reason=opt`，措辞「**给定路径下的条件最优（非全局最优）**」（不改解码层 `reason`，避免扰动 P1 闸门的 `reason=opt` 计数） | 5 |
| **P3-c** | `68cc418` | 新增 `methods/muas/independent_audit.py`：从 `S_final` 的 tours 几何重建新解（丢弃全部派生 meta、重汇总执行集），只经 `problem` 公开评价入口重算，不读 evaluator 缓存 / `cost_history` / `best_fitness`；实现 §4.3 前三条门槛（完成率 + 恒等式 + eliminating=0；`gate_mode` 恒 True + `coverage_audit` 开；重建重评与 `diagnostics` 逐位一致），提供 `assert_audit_passed()` | 7 |

**默认档 bit-exact 硬核验（2026-11-06 晚）**：把 `010b136`（P2 结束快照）
用 `git archive` 导出到临时目录，与当前 master 用**同一合成场景、同 config、
同 seed {7, 11, 23}** 各跑一遍（`PYTHONPATH` 切换包根、同一 `.venv`
解释器、两进程互不污染），逐字段 diff 14 项 `diagnostics` +
`assignment_pairs()` ⇒ **完全一致**。⇒ P3 三步确实没有改变默认路径的
任何数字，「零行为变化」不是靠断言自证，而是与上一阶段快照实测对齐。

**验收口径核对**：§8 表 P3 的「§4.3 五条门槛」中第 1–3 条已由
`independent_audit` 代码断言、第 4 条由全量 pytest（516 passed）覆盖；
第 5 条（与 `method-phase1` 同场景/同 seed/同预算对照 + 配对 t 检验）
属正式实验，本轮**未跑**。合成场景已实测正常路径逐位一致
（`consistency` 全 True）、`passed=True`。

**P3 未擅自执行的行为变化（必须显式记录）**：

1. **L3b 默认关已落地**（原 §11.4 待裁定项，2026-11-06 用户裁定②：
   「新增统一 `l3b_enabled`（默认 False）同时约束两通道 + 旧 config 映射」）。
   实施记录与核验见 **§11.5**；本条不再列为未决。
2. **`cost_polish_accept_mode` 默认仍 `path`**。定稿 §4.2「迁移须开关对照，
   不得静默替换」；是否翻转为 `f` 由 P4 全量 A/B 决定。
3. **§4.3 第 2 条与 §3.3 的张力**：第 2 条写「`coverage_gate` /
   `lexicographic_coverage` 双开」，但 §3.3 明确门禁只作终态校验、生产默认
   `coverage_gate=False`，且 `lexicographic_coverage` 已更名 `coverage_audit`
   （旧名垫片会告警）。独立验收器默认只强制 `gate_mode()` 恒 True 与
   `coverage_audit` 开，`coverage_gate` 留作 `require_coverage_gate=True`
   可选严格档；该条口径待用户裁定后再统一。

**P4 / 实验层待办（均需单独发话，严禁多实验并行）**：

- 「几何 vs 救回任务」收益拆分：用 P3-b 的 `l3a_*` / `l3b_*` /
  `n_task_to_uav_changes` 三本账，配合同 seed 配对留档；
- MCF 对照方案（§4.1）：层 1「最近合法巢」确定性投影 vs 层 3 MCF，
  两层 × 四档同 seed 配对，指标＝返航段航程 / overflow / 墙钟；
- 三级嵌套消融 + 2×2 交互（禁写「独立收益」）、Deb / ε / Lagaros 两类变体
  开关对照、统一事后评价器重评 phase1 与本版交付解；
- 重跑全部基线 + 配对 t 检验如实声明（不显著就写不显著）；
- `coverage_first` 单调 polish 的跨机 relocate 是否也纳入总闸（§11.5 第 6 条，
  当前如实单列、不静默扩权）；
- 采纳判据默认 `f` 的翻转由上述 A/B 结果决定，不预先改默认。

---

## 11.5 L3b 统一总闸落地（2026-11-06 用户裁定②）

**裁定**：新增统一 `l3b_enabled`（默认 False），**同时约束两个通道**，并做
**旧 config 映射**（对应 §11.4 原待裁定项第 1 条的选项②，不选①）。

### 1. 范围：两个受控通道

| 通道 | 归属类动作 | 原开关 | 现状 |
|---|---|---|---|
| A | `polish_assignment` 的 relocate / swap | `polish_moves`（默认含） | 由总闸过滤为只剩 `position` |
| B | 交付后成本 polish 的 `_cost_polish_relocate` | `cost_polish_relocate_enabled=True` | 由总闸 AND 旧细粒度键 |

L3a 纯几何（组内 2-opt/Or-opt、终点巢 MCF「给定路径下的条件最优」、
`polish_assignment` 的 position）**不受总闸约束**。

### 2. 三键设计（None 哨兵 + property 无状态）

`stage_config.py`：`l3b_enabled: bool|None = None`；两个旧键改 None 哨兵
（`cost_polish_relocate_enabled: bool|None = None`、
`polish_moves: tuple|None = None`），保留「是否显式设置」的信息。有效值
一律由 property 计算，`replace()`（`stage_runner` 的 `current_time`、
memetic 从紧预算）重入幂等，已实测。

优先级规则（诊断 `l3b_source` 留痕）：

| 情形 | 有效总闸 | source |
|---|---|---|
| 显式 `l3b_enabled=True/False` | 以新键为准（旧键不能单独开归属维度） | `explicit` |
| 未设新键 + 旧键显式开启（reloc=True 或 moves 含 relocate/swap） | True（**保义**） | `legacy_on` |
| 未设新键 + 旧键显式全关 | False | `legacy_off` |
| 三者均未设（`MUASStageConfig()`） | **False（拍板默认）** | `default` |

显式新键 False 与旧键显式 True 并存 ⇒ 新键优先、不抛错（便于 `--set`
覆盖旧 yaml），诊断 `l3b_legacy_conflict=True` 留痕。
两个 `effective_*` property = 「总闸 ∧ 旧细粒度键」：
`effective_cost_polish_relocate`（通道 B）、`effective_polish_moves`
（通道 A，总闸关时只保留 `position`；空 tuple 合法，调用方不得
`or DEFAULT_MOVES` 回退，否则总闸被静默绕过——`local_search.py` 已修）。

### 3. 代码落点

| 文件 | 改动 |
|---|---|
| `methods/muas/solvers/stage_config.py` | 三键 + 6 个 property + 校验兼容 None 哨兵 |
| `methods/muas/solvers/local_search.py` | `polish_assignment` 读 `effective_polish_moves`（修空 tuple 回退坑） |
| `methods/muas/solvers/route_polish.py` | `post_cost_polish` 读 `effective_cost_polish_relocate` |
| `methods/muas/solvers/stage_runner.py` | 诊断增 `l3b_enabled` / `l3b_source` / `l3b_legacy_conflict` / `l3b_ungated_monotone_relocate`；`l3b_enabled_observed` 改读总闸 |
| `experiments/exp01_smoke/config.py` | 透传 `l3b_enabled`（yaml 缺省 None ⇒ 走旧键保义映射，实验数值不变） |
| `tests/test_l3b_unified_gate.py` | 新增 10 项：解析 / 映射 / 冲突 / 幂等 / 行为 / 逐位保义 |
| 6 个既有测试文件 | 依赖跨机算子的用例显式 `l3b_enabled=True`；默认断言按新口径更新 |

### 4. 回归与硬核验

- 全量 **526 passed**（P3 结束 516 + 新增 10；8 个旧用例因默认翻转同步更新）。
- **开关纯度硬核验**：`git archive 010b136`（P2 结束快照）导出到临时目录，
  与当前 master 同合成场景、同 config、同 seed {7, 11, 23} 各跑一遍：
  「当前 master 显式 `l3b_enabled=True`」vs「010b136 默认」⇒
  **66 项公共 diagnostics × 3 seed 全 0 差异**（含 `assignment_pairs()`）。
  ⇒ 旧 config 映射可逐位复现历史数值，改动是纯开关、无算法副作用。
- **默认关档与历史默认的差异**：同 3 seed 仅 4 项不同，全部是邻域评估规模
  （`n_evals` / `polish_candidates` / `polish_evals` / `polish_accepted` 键集合）；
  `final_fitness` / `final_path_m` / `n_executed` / `best_fitness` /
  `assignment_pairs()` 等交付质量指标**逐位一致**。该合成场景 L3b 本就零接受
  （relocate=swap=0）⇒ 关总闸只是省掉无收益的候选评估。
  ⚠️ **方法论铁律：冒烟/合成场景结论不可外推正式档**；正式档的
  「几何 vs 救回任务」收益拆分仍属 P4，未预先据此改默认。
- 完成率铁律复核：默认关档 `final_n_hard=0`、`repair_status=REPAIRED`、
  `delivery_refused=False`、`n_executed+n_deferred=n_tasks`、
  `l3b_relocate_actions=0`、`n_task_to_uav_changes=0`。

### 5. 方向性变化（必须随引用一起声明）

默认档（纯 `MUASStageConfig()`）自本步起**不再与 `010b136` / P3 记录逐位一致**，
也**不等同 `method-phase1`**。复现口径：

- 复现 P2/P3 数值：显式 `l3b_enabled=True`（或写全旧键的历史 config，由映射保义）；
- 复现第一阶段：checkout tag `method-phase1`（另需 `lamarckian=True` 等口径）。

旧实验脚本（`ab_optimization_attribution`、`ab_m21_patrol_writeback`、
`ab_mechanism_3steps`、`exp01_smoke/config.py`）均显式写旧键 ⇒ 自动映射为开，
**数值不被静默改动**；但统一总闸使「旧键只关通道 B」的语义变为「两通道同关」，
若要逐位复现旧的单通道对照档，须显式 `l3b_enabled=True` 并单独设旧细粒度键。

### 6. 残留：第三来源（不擅自扩权，P4 裁定）

`coverage_first=True` 时 `_polish_routes_monotone` 的跨机 relocate 是
**第三个**归属类来源，当前**不受总闸约束**（默认 `coverage_first=False`
⇒ 默认档恒 0）。为免「总闸关却有归属动作」的口径疑点，诊断单列
`l3b_ungated_monotone_relocate` 如实记账，`l3b_relocate_actions` 仍保持
P3-b 原有合计口径不变。是否纳入总闸属 P4 待裁定项，本步不擅自扩权。
