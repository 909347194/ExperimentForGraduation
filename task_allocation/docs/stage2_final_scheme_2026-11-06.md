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
| 4 | 可选受限交付精修 | L3a 默认开、L3b **默认开**（2026-11-07 起，见 §11.9）；采纳判据统一为 `f`（`path` 仅兼容别名） | `polish_enabled`、`l3b_enabled` | 完成「几何 vs 救回任务」收益拆分；L3b 单记 `task_to_uav` 变动 |
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

**数据环境（2026-11-09 更正，详见 §11.13）**：本节原记「本环境缺 DEM 场景数据、
exp01 审计 25/25 无法在此运行」，系 2026-11-06 fresh clone 时的环境事实，**当前
机器已不成立**：`guangzhou` / `lasa_chengguan` 数据齐备，exp01 审计已于
2026-11-09 补跑通过（26/26），全量回归 549 passed。上表第 2 条「427 绿 + 6
失败」保留为 P0 当时的历史记录，不追改；补验收的**限制与仍须补跑项**见 §11.13。

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

---

## 11.6 P4 首个实验脚手架与冒烟档（2026-11-06）

**授权边界**：用户「继续推进」后从四个方向中选定「建脚手架 + 冒烟档」。
本轮**未改任何代码默认值**、**未启动其它实验**（严格串行单实验）、
**未渲图**、**未覆盖任何既有结果**。数值产物落在被 `.gitignore` 忽略的
`results/` 下，只在本机留存，不进仓库。

### 1. 新增实验 `exp07_polish_accept`

自持 `config.yaml / config.py / build.py / paths.py / metrics.py / run.py /
plot/compare.py / notes.md`（遵守「实验独立性」约定：不建 `_shared/`、
不抽公共绘图包、不跨实验 import）。

| 档 | 判据 | 阈值 |
|---|---|---|
| `path` | `accept_cost_polish_view`（航程分量门，当前默认） | `cost_polish_eps=1.0` 米 |
| `f` | `accept_f_view`（f 严格改进 + 约束向量不退化） | `cost_polish_eps_f=1e-6` f 单位 |

唯一差异由 `dataclasses.replace(stage_model, cost_polish_accept_mode=...)`
锁定，`summary.json` 落盘两档配置对象 diff 并断言
`only_accept_mode_differs=True`。同 seed 配对 `[7, 11, 23]`，
每 seed 先跑完两档再进下一个；`l3b_enabled: false` 显式关总闸
（否则历史 yaml 的 `cost_polish_relocate_enabled: true` 会经保义映射
把两通道偷偷打开，§11.5）。`--profile formal` 会拒绝在冒烟参数未被
`--set` 覆盖时运行，防止冒烟结论外推。

### 2. 冒烟结果（2 档 × 3 seed = 6 次运行）

| 项目 | 结果 |
|---|---|
| 场景 / 收敛 | 3 机巢 + DEM + 20685 栋建筑；3 seed 均于周期 4 池空自然收敛 |
| 配置纯度 | `n_differing_fields=1`，仅 `cost_polish_accept_mode` |
| 完成率 / 按时率 | 两档逐 seed 均 1.000；`n_hard=0`；各项检查全过 |
| 航程（7/11/23） | 27.27 / 28.01 / 20.71 km，两档逐位相同 |
| `final_fitness_sum` | 2726.901 / 2801.242 / 2070.883，两档逐位相同 |
| 采纳候选 / 漂移 | 3 seed 合计仅 1 次采纳（seed 11，两档都采纳、两视图都 True）；漂移全 0 |

**verdict（如实写入 `summary.json`）**：两档在所有质量指标上逐 seed
完全一致；本冒烟档**无法区分** `path` 与 `f`，既不能据此翻转默认，
也不能写成「两档等价」。原因是冒烟规模下交付后成本 polish 几乎无候选。
⇒ 本轮验证的是**实验设计与统计链路可用**，**没有回答**「默认是否翻 f」。
全量回归 **526 passed**（与 §11.5 持平，未破坏既有测试）。

### 3. 统计口径的三处如实修正（本轮实测暴露）

1. **venv 未装 scipy**。不擅自加依赖，改为在 `metrics.py` 内用正则化
   不完全 β 精确计算 Student t 双尾 p（`p = I_{df/(df+t²)}(df/2, 1/2)`），
   已用 `t(1)=1→0.5`、`t(2)=2.228→0.0500` 等临界值校验；**未用正态近似
   冒充精确检验**。墙钟配对 `t=−1.596, p=0.251`（不显著，且非质量指标）。
2. **零方差 ≠ 不显著**。所有质量指标差值为常数 0 ⇒ 配对 t 检验不适用，
   报告 `p_reason=zero_variance_no_test`，不得写成「p 缺失」或「不显著」。
3. **`n_eliminating` 不是 diagnostics 字段**（它只是 `independent_audit`
   的入参）。本实验的 0 是「TaskPool 已无超期出池路径」的**结构性事实**，
   非独立测量；落盘 `n_eliminating_basis=structural_zero_unverified`、
   `eliminating_independently_verified=False`，并在 `invariants_scope`
   注明未接入独立事后评价器。

另有一条口径局限：`cost_polish_accept` 是每周期**最后一次**候选的快照，
不是全候选累计 ⇒ 低频候选场景会低估判据分歧次数。要精确统计须扩展
`stage_runner` 落盘累计计数（属代码改动，本轮未做），已写入
`summary.json.caveats`。

### 4. 本轮新发现（第四项待裁定，未擅改）

复制骨架时核对到**代码默认** `projection_only=False` /
`compare_mode="penalty"`，而定稿 §6 表格写 `projection_only=True` /
`compare_mode="deb"`。本实验如实沿用代码默认，并在 `run_meta.json` 的
`stage_effective` 逐项留痕；**未改代码、未改文档表格**，列为待用户裁定项。

### 5. 仍未回答 / 下一步（均需单独发话，严禁并行）

- **默认翻 `f` 仍未回答**：需正式档配对 A/B（`--profile formal` + 显式
  覆盖 `n_tasks/population/max_iter/n_cycles/arrivals`）；冒烟零差异不构成依据。
- 原三项待裁定仍在：§4.3 第 2 条覆盖门禁双开口径（§11.4 第 3 条）、
  第三来源是否纳入 `l3b_enabled` 总闸（§11.5 第 6 条）、上述第 4 节新发现。
- P4 其余实验**未启动**：「几何 vs 救回任务」收益拆分、MCF 层 1/层 3 对照、
  三级嵌套消融 + 2×2 交互、重跑全部基线配对 t 检验、`method-phase1` 同 seed 对照。

---

## 11.7 审查后修复 + 「需要什么做什么」的去留判断（2026-11-06 深夜）

用户裁定两条：① **先修**（不先跑正式档）；② 「什么关不关、开不开；
**我们的方法需要什么就做什么，不需要什么去掉什么；不确定的话就做实验对比**」。
即：不再做「默认开/关」这类管理性裁定，改从方法本身的需要出发；
判断不了就用同 seed 配对数据说话。

### 1. 审查结论（只读，未改文件）

对 `c39d607`（L3b 总闸）与 `5f60589`（exp07 脚手架）做只读审查，
A 级三条：**A1** 该配对实验在结构上测不到设计意图；**A2** 「L3b 默认关」
在论文主实验链路上因旧 config 保义映射实际不生效；**A3** 独立交付验收
没接上、铁律只观察不断言。另有 B 级 7 条统计/防呆瑕疵。

### 2. A1 的结构缺陷（比「候选太少」严重得多）

修复前 `post_cost_polish` 局部搜索的成本门恒为「航程严格下降」
（`c < base_cost − cost_polish_eps`），迟到门又要求迟到不增，
于是 `f` 判据串在航程门**之后**：`f` 档只能在 `path` 档的候选子集里
做更严的筛查，永远不可能接受「航程略增但迟到/综合更优」的解。
在「约束向量不退化」前提下，前四个 f 分量（n_hard / excess / n_skip / L）
非增 ⇒ 两视图恒同真同假。**§11.6 把零差异归因于「冒烟规模候选太少」
是错的，已作废**：跑再大也测不出。

另一必要条件：迟到恒 0 时 `f = λ·path + 常数`，两判据数学等价。
用 `uav_speed=4.0` 制造迟到（按时率降至 0.750）后，该规模交付后
polish 仍零接受（12 周期全零），仍观测不到分歧。这两条都是设计条件，
不是样本量问题。

### 3. 修复内容

* **判据下沉**：`RoutePolishMixin._cost_polish_criterion()` 返回
  `(mode, score, eps)`。`path` 档 `score=航程(米)`、`eps=cost_polish_eps`，
  调用点保留原短路顺序；`f` 档 `score=λ·航程 + w_late·迟到`、
  `eps=cost_polish_eps_f`，允许航程不降而迟到降更多。
  `_cost_polish_routes` / `_cost_polish_relocate` 的成本门与最优插入位
  选择均改读该评分；新诊断 `cost_polish_n_f_only_gain` 记录「只有 f
  判据会采纳」的移动数。归因计数固定用米制阈值，不与 `eps_f` 混单位。
* **bit-exact 硬核验**：`git archive 5f60589` 同场景同 seed {7,11,23}，
  `path` 档 **78 项公共诊断 × 3 seed 零差异**，唯一新增为诊断字段。
* **单元级反证**：`tests/test_p4_accept_criterion.py`（4 项）构造
  基线 1000 m / 200 s 迟到 → 2-opt 候选 1005 m / 0 s 迟到，
  `path` 档拒绝、`f` 档接受且两档交付解不同 ⇒ 判据对照不再冗余。
* **A3**：实验层把完成率恒 1、`arrived` 恒等式残差非负（取消
  `max(0,…)` 钳位）、`n_eliminating=0`、`n_hard=0` 改为**硬断言**，
  失败时 `run.py` 返回非零码。`independent_audit` 的几何重建重评
  **仍未接入**：`problem` 是 `run_selective_muas` 的局部对象，
  `RollingHorizon` 未暴露审计钩子；本轮不擅自改核心层，记为技术债。
* **B1–B7 全修**：质量判定排除诊断计数/墙钟；用未 round 差值 +
  显式容差 `1e-9` 判零；caveats 补全三重截断；两档顺序按 seed 交替；
  formal 防呆补 `rolling.arrivals.counts` 并做类型归一；删掉 verdict
  中不存在的「交互项」；指纹含 seed、配置 diff 逐 seed 核验、
  `coverage_audit` 归位到 `problem_effective`。
* 全量 **530 passed**（526 + 新增 4）。

### 4. 两组同规模配对数据（正式档本轮跑不动）

正式档实测 **1 seed × 2 档 > 10 分钟**，全量长跑本轮不可行。
改用可控中间档 `preset=mid`（12 任务 / pop25 / gen60 / n_cycles20 /
counts[3,3]），3 seed 配对、严格串行、两组实验分开跑。
⚠️ **mid 不是正式档，结论不可外推正式档。**

| 对照 | 结果 |
|---|---|
| `path` vs `f`（mid） | 3 seed 质量指标逐位相同，`f_only_gain` 全 0、漂移全 0 ⇒ 修复在结构上有效，但该规模候选域里不存在判据分歧解 |
| L3b 关 / 开（mid） | 配置 diff 仅 `l3b_enabled`；6 次铁律全过。seed 7 航程 34.42→31.14 km（−9.5%，3 次归属动作），seed 11/23 逐位相同；完成率/按时率两档全同。配对均值 −1093.7 m，**t 检验 p=0.423 不显著**（df=2 效能极低） |

L3b 冒烟 1 seed 自检降幅更大（27.27→18.53 km），说明效果随场景波动，
更不可外推。

### 5. 按「方法需要什么」给出的判断（本轮不擅自改默认值/删代码）

1. **L3b 是方法需要的，不该继续挂在「默认关 / 待裁定」状态。**
   它是**交付后**精修——发生在选择之后，不参与 DE 竞争、不回写基因，
   因此不违反 §4.3 的「评价与遗传传递一致性」契约（该契约约束的是
   「参与选择的表型」）。实测能在不损完成率/按时率的前提下压低航程。
   但把 `l3b_enabled` 默认翻回 True 属于**数值口径变更**，且
   `c39d607` 的「默认 False」是用户上一轮明确拍板，本轮不擅自推翻。
   消融用显式 `l3b_enabled=False` 已由 `compare_l3b.py` 验证可用。
2. **`cost_polish_accept_mode` 更像不需要的复杂性。** §4.2 要求
   「采纳判据统一到 f」本就是**口径一致性**而非收益问题；冒烟 8 任务
   与 mid 12 任务各 3 seed 两组均零分歧，保留双视图开关没有换来
   可观测信息。是否删 `path` 分支仍缺正式档证据（方法论铁律①），
   本轮只标注、不删。
3. **§11.6 第 5 节「冒烟零差异不构成依据」仍成立，但理由已换成
   本 §11.7 第 2 节的结构缺陷 + 迟到恒 0 条件。**
4. **唯一缺的是正式档跑批**：`run.py --profile formal` 与
   `compare_l3b.py --preset formal`，两组各 3 seed 配对。
   正式档跑完才能把上面两条从方向性判断升级为可执行的代码去留。
   须单独发话，严禁多实验并行。

### 6. 新增/修改文件

`route_polish.py`（判据下沉）、`stage_runner.py`（诊断字段与注释）、
`tests/test_p4_accept_criterion.py`（新增 4 项）、
`exp07/metrics.py`（硬断言 + B1/B2/B3/B6 + `expected_accept_mode`）、
`exp07/run.py`（B4/B5/B7 + 非零退出码 + `mid` profile）、
`exp07/compare_l3b.py`（新增，L3b 去留配对）、
`exp07/notes.md`（§6 更正 + §7/§8）、`docs §11.7`（本节）。

---

## 11.8 正式档跑批结果 + 去留判断升级（2026-11-07 凌晨）

用户 2026-11-07 00:24 单独发话「跑」，按 §11.7 第 5 节第 4 条补齐正式档。
两组实验**严格串行**（同一后台进程内 `&&` 串联，组 1 退出码 0 后才启动组 2），
全程无并行、不渲图、`-u` 挂起。总耗时 **65 min 27 s**
（00:24:43 → 01:30:10），批次 `BATCH_RC=0`，两组脚本各自返回 0。

### 1. 跑批口径与复现

```bash
.venv/Scripts/python.exe -u task_allocation/experiments/exp07_polish_accept/run.py \
  --profile formal --seeds 7,11,23 \
  --set tasks.n_tasks=20 --set solver.population=50 \
  --set solver.max_iter=200 --set rolling.n_cycles=60 \
  --set 'rolling.arrivals.counts=[5,5]'
.venv/Scripts/python.exe -u task_allocation/experiments/exp07_polish_accept/compare_l3b.py \
  --preset formal --seeds 7,11,23
```

规模 20 任务 / pop50 / gen200 / 60 周期上限 / 到达 20+5+5；广州 3 机巢、
DEM 接入、20685 栋建筑；池空自然收敛于周期 4（60 只是硬上限）。
单档墙钟 313–340 s，其中第一周期求解约 300–316 s。
产物：`results/formal/summary.json`、`results/l3b_formal/summary.json`
及逐档逐 seed 的 `metrics.json` / `run_meta.json`（共 24 个 JSON，齐全），
日志 `results/formal_batch.log`；均被 `.gitignore` 忽略，本机留存。

### 2. 组 1｜采纳判据 `path` vs `f`

| seed | 航程 path / f (km) | f 之和 path / f | 按时率 | 迟到 (s) | 采纳数 | f-only 改进 |
|---|---|---|---|---|---|---|
| 7 | 44.071 / 44.071 | 4407.132 / 4407.132 | 1.000 | 0 | 1 / 1 | 0 / 0 |
| 11 | 41.699 / 41.699 | 4171.783 / 4171.783 | 0.933 | 192.979 | 1 / 1 | 0 / 0 |
| 23 | 38.841 / 38.841 | 3884.132 / 3884.132 | 1.000 | 0 | 2 / 2 | 0 / 0 |

配置 diff 仅 `cost_polish_accept_mode`（1 字段）、同档跨 seed 一致、
6 次铁律全过、配对 seed 完整。配合 §11.7 的 4 项单元级反证
（构造例 `1000 m/200 s → 1005 m/0 s` 中 path 拒绝、f 接受）可判定：
**当前交付后 polish 的候选域里不存在「航程略增、迟到显著降」的移动**，
不是判据实现失效，也不是样本量不足。冒烟 8 任务、mid 12 任务、
正式 20 任务三档规模各 3 seed 共 9 组配对零分歧。
统计口径：零方差不检验；结论只能写「本次跑批未观测到可区分差异」，
**不得写成「两档等价」或「path 档更差」**。

### 3. 组 2｜L3b 关 / 开

| seed | 关档航程 (km) | 开档航程 (km) | 差 (m) | L3b 归属动作 | 归属变动 | 完成率 | 按时率 |
|---|---|---|---|---|---|---|---|
| 7 | 44.071 | **41.887** | −2184.4 | 6 | 4 | 1.000 | 1.000 |
| 11 | 41.699 | 41.699 | 0 | 1 | 0 | 1.000 | 0.933 |
| 23 | 38.841 | **37.305** | −1536.6 | 4 | 1 | 1.000 | 1.000 |

配对均值差：航程 **−1240.3 m**、f **−124.03**，Student t 双尾
**p=0.1956（df=2）不显著**；完成率 / 按时率 / 迟到 / 硬违约逐 seed
零方差不检验；墙钟 **+14.47 s，p=0.0208（显著变慢）**。
配置 diff 仅 `l3b_enabled`、铁律全过；关档 L3b 动作 `[0,0,0]`、
开档 `[6,1,4]`；`l3b_ungated_monotone_relocate` 恒 0
（本实验 `coverage_first=False`，第三来源测不到）。
交叉校验：关档三 seed 数值与组 1 的 `path` 档逐位一致。

纪律：两档单因素设计**没有 2×2，不得写「独立收益」**；
p 不显著须如实声明，也不得写「无差异」。逐 seed **零反例**
（2 降 1 平、质量约束逐位不变）⇒ 在「完成是约束、成本是目标」
口径下是不退化改进，但统计效能极低，不得升格为「显著收益」。

### 4. 可执行去留判断（本轮只写判断，代码与默认值零改动）

按用户裁定「我们的方法需要什么就做什么，不需要什么去掉什么」：

1. **`cost_polish_accept_mode` 不需要，建议去掉。** §4.2 要求
   「采纳判据统一到 f」本是口径一致性而非收益问题；正式档证明两档
   交付解逐位相同 ⇒ 把默认由 `path` 翻到 `f` **零数值变化**，
   删掉 `path` 分支与开关后，方法只会在真实出现「航程增、迟到降」
   候选时按 f 更优。落地动作：删双视图分支、保留
   `cost_polish_eps_f`、收口 §4.2 文字；落地前须全量测试 +
   与本次 `results/formal` 做 bit-exact 核验。
2. **`l3b_enabled` 是方法需要的，建议默认开。** 它是交付后精修，
   在选择之后，不参与 DE 竞争、不回写基因 ⇒ 不违反 §4.3
   「评价与遗传传递一致性」契约；正式档逐 seed 零反例地压低成本。
   两点影响面必须先看清：
   * 主实验链路 `exp01_smoke/config.yaml:365` 只写旧键
     `cost_polish_relocate_enabled: true`、未写新键 ⇒ 按 §11.5
     保义映射走 `legacy_on`，**实际一直是 True**。因此翻默认
     对 exp01 主链路数值无影响；受影响的是纯默认档
     `MUASStageConfig()` 与显式写 `l3b_enabled: false` 的实验（如 exp07）。
   * 但 `c39d607` 的「默认 False」是用户明确拍板；翻默认会把纯默认档
     的口径变回 `010b136` / `method-phase1` 的开态，且使若干断言
     「默认关」的单测失效 ⇒ **须用户单独拍板后再改**，本轮不动。
   * 消融通道（显式 `False`）必须保留，否则 `compare_l3b.py` 与
     P4 消融无法复现。
3. 若不愿翻默认，最小替代：所有主实验**显式写** `l3b_enabled: true`，
   把口径从「靠旧键保义」改成「显式声明」，论文同步写明消融档用 false。

### 5. 仍未解决 / 外推边界

`independent_audit` 几何重建重评仍是技术债（`RollingHorizon` 未暴露
problem / 审计钩子）；§4.3 第 2 条「双开」与 §3.3、生产默认
`coverage_gate=False` 的张力；代码默认 `projection_only=False` /
`compare_mode="penalty"` 与 §6 表格 `True` / `deb` 不一致；
`coverage_first` 单调 polish 的跨机 relocate 是否纳入总闸未裁定；
P4 其余实验（收益拆分 / MCF 层1-层3 对照 / 三级消融 / 重跑基线）
**未开工，严禁并行**。

外推边界：单一场景（广州 3 机巢）、固定到达流 20/5/5、
种子组 {7,11,23}、df=2。正式档结论不可外推其它规模 / 场景 / 更长
跑批；p 不显著不可写显著。

### 6. 本次新增/修改

仅文档：`docs §11.8`、`exp07/notes.md §9`、
`.workbuddy/memory/2026-11-07.md`。代码与默认值**零改动**；
本次未重跑全量测试（代码未变，上次 530 passed）。

---

## 11.9 两条去留判断的代码落地（2026-11-07 下午）

用户发话「按上面两条把代码落地；P4 其余实验」，据此把 §11.8 第 4 节
从判断升级为实现。**代码、默认值、测试、文档同步落地，实验脚手架零改动。**

### 1. 采纳判据统一到 `f`（去掉 `path` 分支）

* `stage_config.py`：默认 `"path"` → **`"f"`**；新增只读 property
  `cost_polish_accept_mode_effective` 恒返回 `"f"`；`"path"` 保留为
  **兼容别名**（校验仍接受，但没有任何实现分支读它）。
* `route_polish.py`：`_cost_polish_criterion()` 恒返回
  `("f", λ·航程 + w_late·迟到, cost_polish_eps_f)`；删掉
  `_cost_polish_routes` / `_cost_polish_relocate` 里全部
  `if mode == "path"` 的航程门分支与 `if mode == "f"` 的条件判断，
  `n_f_only_gain` 改为恒记录。米制 `cost_polish_eps` 退为**归因计数**
  （`blocked_range` / `blocked_late`）专用，不再拦采纳。
* `stage_runner.py`：`accepted = accept_f`（不再由 mode 决定）；
  `accept_cost_polish_view` 仍计算但只作 P0 判据漂移审计；新增诊断
  `cost_polish_accept_mode_effective` 与 `cost_polish_accept.mode_effective`。
  `cost_polish_accept_mode` 诊断字段保留**请求值**，供 exp07 历史产物留痕。
* **为什么字段没删干净**：实验脚手架（`exp07/run.py`、`compare_l3b.py`、
  `config.py`、`config.yaml`、`metrics.py`）与正式档产物都按
  `dataclasses.replace(..., cost_polish_accept_mode=...)` 锁定唯一对照变量。
  彻底删字段会让已归档的两组正式档**无法用当前代码复现**。本项目按
  「历史实验可复现优先」保留兼容键，语义上开关已经取消（任何取值同行为）。
  落地前的完整双判据代码由附注标签 **`p4-exp07-formal`（→ `0962bc5`）**
  归档，需要逐位复现原始两档时 checkout 该标签。

### 2. `l3b_enabled` 纯默认档翻为开

* `effective_l3b_enabled` 优先级不变（显式新键 > 旧 config 保义映射），
  只把**三者均未设**的纯默认档从 `False` 改为 **`True`**。
* 显式 `l3b_enabled=False` 消融通道、旧键 `legacy_on/legacy_off`
  保义映射、`l3b_source` / `l3b_legacy_conflict` 诊断全部不变。
* 影响面（§11.8 已论证）：`exp01_smoke/config.yaml:365` 只写旧键
  `cost_polish_relocate_enabled: true` ⇒ 主链路一直是开，**翻默认对
  exp01 数值无影响**；exp07 显式写 `false` 也不受影响。真正变化的是
  纯 `MUASStageConfig()` 默认档（回到 `010b136` / `method-phase1`
  的开态），以及断言默认关的单测 —— 已全部改为显式配置固定口径。

### 3. 验证

* 全量 **531 passed**（原 530 + 新增 1 项统一判据契约测试；
  `test_p4_accept_criterion.py` 从「证明两档不同」重写为「证明别名与
  f 同行为、两档交付解逐位相同」）。
* **bit-exact 核验**：重跑 mid 档两组各 3 seed（`VERIFY_RC=0`），
  与落地前 `results/_baseline/` 逐字段比对：
  2 份 summary 的全部配对质量指标 + 12 份逐档 `metrics.json` ×
  11 个质量字段 **零差异**；唯一差异是 `wall_seconds` 及其派生 p 值
  （机器负载，非数值口径）。配置 diff 仍各只有 1 个字段、铁律全过。
* 两组正式档（`results/formal/`、`results/l3b_formal/`）用的是**显式档**
  （`l3b_enabled` 显式 off/on、`cost_polish_accept_mode` 显式 path/f），
  因此本次落地不改变其任何数值；§11.8 的结论继续有效。

### 4. 口径声明（覆盖历史表述）

§11.4 / §11.5 / §11.7、§11.8 与 `exp07/notes.md` §8 中出现的
「L3b 默认关」「`cost_polish_accept_mode` 默认仍 `path`」「默认档
bit-exact」等均为 **2026-11-07 落地前**的口径，自本节起不再生效；
历史章节作为时间线证据保留，不回改。§4.2「采纳判据统一到 f」与
§6 要素 4 表格已同步。

### 5. 仍未解决

`independent_audit` 几何重建重评（缺 `RollingHorizon` 审计钩子）、
§4.3 第 2 条「双开」与生产默认 `coverage_gate=False` 的张力、
`projection_only=False` / `compare_mode="penalty"` 与 §6 表格不一致、
`coverage_first` 单调 polish 跨机 relocate 是否纳入总闸（第三来源）。

---

## 11.10 P4 第 1 项：收益拆分 2×2 正式档（2026-11-07 晚）

**状态**：`exp08_gain_decomposition` 自持实验（`f80693d`）冒烟 1 seed、
mid 3 seed、正式档 3 seed 全部 `RC=0`；正式档 12 次 × 约 5.5 min，
总 1 h 4 min 35 s，`FORMAL_RC=0`。**严格串行，未与其他实验并行**。
明细见 `experiments/exp08_gain_decomposition/notes.md` §5；本节只记
跨实验有效的口径与结论。

### 1. 设计

按 §11.4 清单第 1 项，用 P3-b 三本账做 2×2 同 seed 配对：
因子 G = 几何（`cost_polish_enabled`）、因子 B = 归属
（`l3b_enabled` + `polish_moves`），四档 `g0b0/g0b1/g1b0/g1b1`，
`g1b1` 为生产口径。采纳判据四档统一 `f`（§11.9 落地后口径），
按 seed 交替档序、配置逐 seed 验纯度、完成率/恒等式/eliminating
为硬断言。报告**主效应 + 交互项并列**
（`interaction=(f00−f01)−(f10−f11)`），精确配对 t 检验
（正则化不完全 β，df=2），**禁写「独立收益」**。

### 2. 正式档结果（3 seed：7 / 11 / 23）

| 效应 | f 之和 均值 | p | 总航程 均值 (m) | p |
|---|---|---|---|---|
| G@B0 | −35.680749 | 0.187638 | −355.087667 | 0.188645 |
| **G@B1** | **−62.743618** | **0.033718** | **−625.716333** | **0.034832** |
| B@G0 | −96.969062 | 0.180185 | −969.690667 | 0.180185 |
| B@G1 | −124.031931 | 0.195601 | −1240.319333 | 0.195601 |
| **交互** | **−27.062869** | **0.372719** | **−270.628667** | **0.372719** |

配置纯度 True（只差 3 个预期字段）、`all_invariants_passed=True`、
`unpaired_seeds=[]`。完成率恒 1.000、硬违约恒 0、按时率四档逐位相同
（1 / 0.933 / 1）；seed 11 迟到秒数从 244.572 s 降到 192.979 s，
但按时任务个数不变。**只有 G@B1 达到 0.05 显著**；归属主效应与
交互项 p 均 > 0.18，**未达统计显著，也不得写成「无差异」**。

三本账：L3a 在 G=1 两档为 4/4/5 与 7/3/6，G=0 两档恒 0；
L3b 在 `g0b1` 为 5/1/2、`g1b1` 为 6/1/4 ⇒ **B 因子在 G=0 档
确实通过 `polish_assignment` 激活**（notes.md §3 第 2 条的
「未激活」分支未触发）；`l3b_ungated_monotone_relocate` 四档全 0
⇒ `coverage_first` 第三来源在本场景无实测动作，§11.9 第 5 节的
待裁定项仍无证据。

### 3. 口径限制（覆盖结论强度）

1. `cost_polish_enabled=False` 整段跳过 `post_cost_polish`
   （`cost_polish_n_accepted` 在 G=0 两档恒 0 为证），
   G=0 档的 B=1 只有 `polish_assignment` 的 relocate/swap，
   **不含交付后通道 B**。交互项混有算子可得性差异，
   **不能解释成纯二阶交互，禁写「协同效应」**。
2. 本实验完成率 / 硬违约 / 按时率四档相同、迟到交互为 0，
   故 `Δf = 0.1·Δ航程` 在交互项上是线性缩放（−270.628667×0.1
   = −27.062867），f 与航程的交互 t 同为 −1.139083、p 同为
   0.372719。**这不是两条独立证据**，论文不得写「双双验证」。
3. 主效应必须在另一因子固定水平下陈述（G@B0≠G@B1、B@G0≠B@G1），
   不得合并为单一「几何贡献 / 归属贡献」。
4. 终点机巢 MCF 是**给定路径下的条件最优（非全局最优）**；
   单一场景、3 seed、df=2 效能极低；冒烟 / mid 结论不可外推正式档。

### 4. 冒烟 / mid / 正式 交互项对照（铁律实测支持）

−874.159（smoke，单 seed 不检验）→ −108.549（mid，p=0.4270）
→ −27.062869（formal，p=0.372719）。方向一致、量级差 4~32 倍，
**mid 档结论不可外推正式档**再次被实测确认；三档交互项 p 全部
不显著，不存在「跑大就显著」的情形。

### 5. 与 §11.8 两组正式档的交叉验证

`g1b0` 逐位等于 exp07 的 `path` 档（f 4407.132314 / 4171.783458 /
3884.132182），`g1b1` 逐位等于 exp07 的 L3b `on` 档
（航程 41886.966 / 41698.537 / 37304.721）；B@G1 的
−124.031931 / p=0.195601 与 §11.8「L3b 关→开」完全相同。
两个独立实验在同场景 / 同 seed / 同预算下逐位复现，
2×2 设计确实把两个单因素对照嵌进同一坐标系，
§11.8「逐 seed 零反例但 p 不显著」的结论继续有效。

### 6. 结论落位

四档完成率与违约判定逐位相同 ⇒ **交付后精修只作用于成本，
不改变任务完成与时间窗违约判定**，这正是「受限交付精修」的
职责边界；生产档 `g1b1` 在 3 个 seed 上 f 与航程都最低、逐 seed
零反例，但除 G@B1 外的效应均未达统计显著。论文按条件差陈述，
不写「独立收益」、不写「协同效应」、不把 f 与航程当双重证据。

**待办不变**：MCF 层 1 / 层 3 对照、三级嵌套消融、Deb / ε / Lagaros
开关对照、统一事后评价器重评 phase1、重跑全部基线，仍严格串行。

---

## 11.11 P4 第 2 项：终点机巢分层开关与 exp09 设计（2026-11-07 深夜）

### 1. `projection_only` 不能做 MCF 对照（设计审查结论）

§4.1 要求「层 1 最近合法巢投影 vs 层 3 MCF，两层 × 四档同 seed 配对」。
落地前核对到 `projection_only` 是 **P1 复合档**，一次同时剥掉三处解码期
目标改写：`_consolidate` 成本合并跳过、`_best_order` 剔 NN 候选且选择键
退为 `(迟到数, 迟到量)`、终点巢降级为最近合法巢。用它做 MCF 对照会把
三处改动一起归因到 MCF，口径不纯 ⇒ 必须新增**只作用于终点巢**的分层开关。

### 2. 新增分层开关（零默认行为变化）

* `MUASStageConfig` 新增 `end_nest_l1_mode` / `end_nest_l3_mode`，
  取值 `auto`（默认）/ `nearest` / `mcf`，非法取值在 `__post_init__` 拒绝。
* `DecodeRepairMixin._choose_end_nests(routes, *, phase=None)`：
  `phase="l1"` = 解码期（`decode_repair.py` 3 处 + `decode_constructive.py`
  2 处），`phase="l3"` = 交付期（`route_polish.py` 的
  `post_cost_polish` / `post_polish` 2 处）；不传 phase 的诊断性调用
  （`stage_runner` 的 subset-optimality proxy）恒走 auto。
  `auto` ⇒ `_end_nest_mode_for_phase` 返回 None ⇒ 完全沿用既有分支
  （含 `projection_only` 兼容语义），**零行为变化**；显式 `mcf` 可覆盖
  `projection_only`，显式 `nearest` 走 `_choose_end_nests_projection`。
* 新增两项**纯观测**诊断（`stage_runner.diagnostics`，不参与任何判定）：
  `return_leg_m` = Σ(末任务→终点巢的**有向**三维 DEM 代价，与
  `tour_path_cost` 末段同口径)、`nest_overflow` =
  `check_nest_capacity` 逐巢 `overflow` 之和。对应 §4.1 要求的
  「返航段航程 / 可行率（overflow）」指标。

### 3. 验证

* 新增 `tests/test_p4_end_nest_phase.py`（7 项）：配置默认/校验、
  `replace()` 幂等、阶段解析、nearest→投影 / mcf→求解器分流、
  `projection_only` 兼容、解码期调用点全部标注 `l1`、
  默认 auto 与显式双层 `mcf` 逐位一致。全量 **538 passed**（531 + 7）。
* **bit-exact 核验**：exp07 两组 mid 各 3 seed 重跑，与改动前
  `results/_baseline/exp09_pre/` 逐字段比对 **26 份 JSON
  （2 summary + 12 metrics + 12 run_meta）零差异**，仅
  `wall_seconds` / `timestamp_utc` 不同。第一组曾误用 `run.py` 的空 mid
  预设（沿用冒烟参数）导致假差异，按 `12/25/60/20 + [3,3]` 正确参数
  重跑后归零；该次失误只影响核验过程，未改动任何生产数值。

### 4. exp09 2×2 设计（自持实验目录 `exp09_mcf_layers`）

|  | 层 3 投影 | 层 3 MCF |
|---|---|---|
| **层 1 投影** | `nn` | `nm` |
| **层 1 MCF** | `mn` | `mm`（当前生产口径） |

四档用 `dataclasses.replace` 只改两个字段 + 统一 `cost_polish_accept_mode="f"`
（§11.9）；逐 seed 验配置纯度、按 seed 交替档序、严格串行。
报告主效应 + 交互 `(nn−nm)−(mn−mm)` 并列、精确配对 t 检验，
指标含 `total_return_leg_m` / `nest_overflow_total` / 墙钟 + 常规质量与
`cost_polish_n_mcf_resolve`。**禁写「独立收益」「协同效应」**。

口径限制：① 层 1 作用于每个候选个体的解码 ⇒ 改变整条 DE 搜索轨迹，
层 1 主效应是端到端效应，不是「同一解上换巢」的局部收益；
② 层 3 只在 `post_cost_polish` / `post_polish` 真正被调用的周期生效，
零精修周期两档相同（算子可得性差异，交互项不纯）；
③ overflow 若恒 0，说明两策略都满足容量约束，不得写「可行率提升」；
④ MCF 是**给定路径下的条件最优（非全局最优）**；
⑤ 冒烟 / mid 不可外推正式档，3 seed / df=2 的 p 不显著须如实声明。

### 5. 冒烟档（1 seed，仅设计自检，不可外推）

`SMOKE_RC=0`，配置纯度 True（差异字段=2）、铁律全过、`run_meta` 四档
`end_nest_l1_mode` / `end_nest_l3_mode` 逐档正确、`projection_only` 全 False。

| 档 | f 之和 | 总航程 | 返航段 | overflow | 完成率 |
|---|---|---|---|---|---|
| `nn` | 1913.736 | 19.14 km | 5.62 km | 0 | 1.000 |
| `nm` | 1913.736 | 19.14 km | 5.62 km | 0 | 1.000 |
| `mn` | 1852.743 | 18.53 km | 7.43 km | 0 | 1.000 |
| `mm` | 1852.743 | 18.53 km | 7.43 km | 0 | 1.000 |

**层 3 主效应与交互项全 0**（`nn≡nm`、`mn≡mm`），四档 `MCF重解=2`
且 overflow 恒 0 ⇒ 本场景终点机巢无容量竞争，投影与 MCF 退化为同一
分配；这是**场景限制**，不是开关失效（生效配置已逐档核验）。
层 1 换到 MCF 使总航程 −0.61 km 但**返航段 +1.81 km**，方向相反
正是「整条搜索轨迹被改写」的证据，不得解释为局部换巢收益。

### 6. mid 档（3 seed，不可外推正式档）

`MID_RC=0`，配置纯度 True（差异字段=2）、铁律全过、配对完整。
逐 seed 逐位 `nn≡nm`、`mn≡mm`：层 3 主效应与交互项在 f / 总航程 /
返航段 / overflow 上**全 0**（零方差不检验）；`nest_overflow_total`
12 次运行恒 0。层 1 配对主效应：f −11.759004 (p=0.373722)、
总航程 −113.928 m (p=0.367874)、返航段 −404.722 m (p=0.427883)，
**均未达统计显著**，df=2 效能极低，不得写成「无差异」。

**机制诊断**：`end_nests_signature`（逐周期 `uav>巢` 串）逐 seed
逐位 `nn≡nm`、`mn≡mm`，且 `cost_polish_n_mcf_resolve` 四档相同
⇒ 层 3 的 `_choose_end_nests` 确实被调用，但本场景（3 机巢、
容量充足）没有终点巢容量竞争，「最近合法巢」与 MCF 返回同一分配，
层 3 因子完全退化。这是**场景限制**而非开关失效（生效配置逐档核验 +
7 项单测证明分流）。**注意：该零效应只属于冒烟 / mid 档，正式档已
推翻（见第 7 节），不得写进论文**。

### 7. 正式档结论（2026-11-07 深夜，唯一可作结论依据）

`FORMAL_RC=0`，3 seed × 4 档严格串行、总 1 h 6 min 1 s，12 次运行全
PASS；配置纯度 True（差异 2 字段）、铁律全过、`unpaired_seeds=[]`、
`nest_overflow_total` 四档 12 次恒 0。

| 效应 | f 之和 | p | 总航程 m | p | 返航段 m | p |
|---|---|---|---|---|---|---|
| L1@L3n | +12.244580 | 0.406644 | +120.101 | 0.437272 | −551.357 | 0.613504 |
| L1@L3m | +20.296046 | 0.195534 | +200.616 | 0.203942 | −470.842 | 0.636033 |
| **L3@L1n** | **−8.051466** | 0.422650 | **−80.514667** | 0.422650 | **−80.514667** | 0.422650 |
| L3@L1m | 0 | 零方差不检验 | 0 | 零方差不检验 | 0 | 零方差不检验 |
| 交互 | +8.051466 | 0.422650 | +80.514667 | 0.422650 | +80.514667 | 0.422650 |

1. **层 3 首次分叉，且只在 seed 11、L1=投影档**：`nn → nm` 时第 1
   周期终点巢签名 `3>1,4>2,5>0` → `3>1,4>0,5>2`（UAV4 / UAV5 对调
   终点巢），返航段 −241.544 m、总航程 −241.544 m（**去程不变**）、
   f −24.154，overflow 仍 0 ⇒ 这是「给定路径下联合最优 vs 逐机最近
   合法巢贪心」的**纯成本差异**，不是可行性修复。`mn ≡ mm` 逐位相同
   ⇒ 交互项完全由「L1=投影档才有层 3 优化空间」驱动，混有算子可得性
   差异，**禁写协同效应**。
2. **红线**：层 3 效应 `Δf = 0.1·Δ航程`，返航段交互 = 航程交互
   ⇒ f 与航程不是两条独立证据；层 3 的航程改善**全部来自返航段**。
3. **所有主效应与交互项 p > 0.19，未达统计显著**（df=2 效能极低），
   须如实声明，也不得写成「无差异」。层 1 主效应方向相对 mid 翻正
   （mid −11.76 → formal +12.24 / +20.30）且不显著 ⇒ 端到端效应
   不稳定，不可解释为「MCF 更好 / 更差」。
4. **交叉验证**：`mm`（生产口径）逐位等于 exp08 `g1b1`
   （f 4188.697 / 4171.783 / 3730.472），第三个独立实验复现。
5. **三档对照实测支持铁律**：冒烟层 3 全 0 / 层 1 −60.99 →
   mid 层 3 全 0 / 层 1 −11.76 (p=0.374) → 正式档层 3 seed 11 分叉 /
   层 1 +12.24、+20.30 / 交互 +8.05 (p=0.42265)。
   **mid 的「层 3 完全退化」结论已作废**；「冒烟 / mid 不可外推正式档」
   再次被实测确认。
6. 完成率恒 1、硬违约 0、overflow 恒 0 ⇒ 两策略都满足可行性与容量
   约束，差异只在返航代价；层 3 不改变按时率与迟到。
   **禁写**：独立收益、协同效应、可行率提升、MCF 无用 / 可删、
   f 与航程双重验证、层 3 恒无差异。

### 8. 后续待办

「容量竞争下 MCF 解决什么问题」已由 **§11.12 / exp10** 立项并开工
（本轮未擅自改动生产默认值，场景差异全部由实验层显式覆盖）。
P4 其余项（三级嵌套消融 + 2×2、Deb / ε / Lagaros 开关对照、
统一事后评价器重评 phase1、重跑全部基线 + 配对 t 检验）仍未开工。
`coverage_first` 第三来源、§4.3 双开张力、`projection_only` 与
`compare_mode` 默认值不一致等仍未裁定。


## 11.12 P4 第 3 项：容量压力场景 exp10（2026-11-08）

### 1. 立项

§11.11 的基准场景是 3 机巢 × 泊位 2 = 6 泊位、机队 6 架（初始恰好满载）。
冒烟 / mid 档层 3 主效应与交互项全 0，正式档只在 seed 11、层 1 用投影时
出现一次分叉（两架机终点巢对调，返航段 −241.544 m，overflow 仍 0）。
`end_nests_signature` 逐位相同 + MCF 重解计数相同证明开关确实生效，
因此退化原因是**场景缺少终点巢容量竞争**。定稿要求另设容量压力场景，
判据为「终点巢分配签名分叉 + 返航段同 seed 配对差」，铁律不变、严格串行。

`exp10_capacity_pressure` 自持 config/build/paths/metrics/plot，
沿用 §11.11 的 `end_nest_l1_mode` / `end_nest_l3_mode` 分层开关，
**不改任何生产默认值**；场景差异由实验层 `--scenario` 显式覆盖，
并进配置指纹与 `run_meta`。

### 2. 场景因子与设计修正

| 场景档 | 机巢 × 泊位 | 总泊位 | 机队 | 初始空位 | 到达流 | 定位 |
|---|---|---|---|---|---|---|
| `loose` | 3 × 3 | 9 | 6 | 3 | 标准 | 负对照 |
| `baseline` | 3 × 2 | 6 | 6 | 0 | 标准 | = exp09 锚点 |
| `tight` | 3 × 1 | 3 | 3 | 0 | 标准 | 泊位压到极限 |
| `burst` | 3 × 2 | 6 | 6 | 0 | 5+5 → **10+10** | 并发压力（主档） |

**初始不超容是前置条件**（`n_uavs ≤ n_nests × nest_capacity`），
否则 t=0 就 overflow、铁律失效；`run.py` 跑批前硬拒绝
（已用 `problem.n_uavs=8` 反例验证），不静默放行。

**设计修正（实测驱动）**：单纯把泊位压到 1 必须同时把机队减到 3，
冒烟四档**完全同解**（f=1772.439，层 1 主效应也是 0）⇒ 可行分配空间
反而缩小，不是竞争增强。因此正式档以定稿并列的另一条压力路径
「提高并发架次」`burst` 为主档，`tight` 只作结构性对照。
`burst` 保持机队 / 泊位物理不变，只把后续两批到达从 5+5 提到 10+10。
**立项时的预期**是活跃周期与末态同时返航次数上升 ⇒ 终点巢竞争机会上升；
**正式档实测该预期未成立**（overflow 恒 0、MCF 重解恒 1 次/seed、
分叉仍是同一处纯成本改派），详见 §5，此处保留原始假设以便追溯。

### 3. 口径红线（在 §11.10 / §11.11 基础上追加）

1. 不同场景的任务集、可行域与机队规模都不同，`burst` 还改变总任务数
   ⇒ **跨场景只允许描述性对照**，禁止把两个场景的指标做同 seed 配对
   t 检验，禁止写成「容量压力带来 X 收益」，也禁止声称**绝对质量量**
   与 exp08 / exp09「逐位复现」。但四档**配对差**（主效应 / 交互项）
   与 exp09 逐位相同是实测事实，须如实写成「同一现象在更大任务规模下
   复现」，并明确 **exp10 不是第二条独立证据**（见 §5）。
2. 层 3 改善若全部落在返航段、去程不变，只能写成「给定路径下联合最优
   与逐机最近合法巢投影」的纯成本差异，不是可行性修复、不是可行率提升。
3. 层 3 零差异若伴随 `cost_polish_n_mcf_resolve = 0`，是**算子可得性**
   （交付后 polish 未被调用），不得解释为「容量无竞争」或「MCF 无用」。
4. 沿用既有红线：禁「独立收益」「协同效应」「双双验证」；
   冒烟 / mid 结论不可外推正式档；p 不显著如实声明且不得写成「无差异」；
   完成率 / 恒等式 / eliminating 为实验层硬断言。

### 4. 已完成的诊断与 mid（`DIAG_RC=0`、`MID_RC=0`、全量 538 passed）

四场景冒烟各 1 seed × 4 档全部 `RC=0`、配置纯度 True（差异字段恒 2）、
铁律全过、overflow 恒 0。`baseline` 的层 1 主效应 −60.993861 与
exp09 冒烟档**逐位相同**，证明 `{}` 是干净锚点。
冒烟层 3 主效应 / 交互项四场景全 0；`burst` 四档 MCF 重解次数全 0
⇒ 冒烟规模下交付后 polish 根本没被调用，属算子可得性而非容量结论。

`burst` mid 档 3 seed × 4 档（12 次、8 min、`MID_RC=0`）：
配置纯度 True、铁律全过、配对完整、overflow 恒 0、完成率恒 1；
f 层 1 主效应 L1@L3n = L1@L3m = **−16.110142**（航程 −157.439 m、
返航段 −626.908 m），层 3 主效应与交互项 3 seed **全部零方差**
（`zero_variance_no_test`），航程 / 返航段交互同为 0。
`cost_polish_n_mcf_resolve` 在 mid 并非全 0（`nn/nm` seed 23 = 2，
`mn/mm` seed 11/23 = 1）⇒ 交付期 MCF 确实被调用过但仍与投影同解，
属「这些周期没有值得改派的分配差异」，不是算子没跑。

**口径辨析**：层 1 主效应上 `Δf = −16.110142` 而 `0.1·Δ航程 = −15.7439`
不相等，差额来自迟到 / 按时变化 ⇒ 层 1 改变整条搜索轨迹时
f 与航程**不是**简单线性缩放；层 3 零差异时 f / 航程 / 返航段同 0
也不构成「三条独立证据」。**按铁律不作任何外推**，正式档单独跑批。

### 5. 正式档结论（2026-11-08 深夜，`FORMAL_RC=0`，唯一可作结论依据）

`--scenario burst --preset formal --seeds 7,11,23`，12 次严格串行、
单进程内完成（1h33m7s），全部 `PASS`：配置纯度 True（差异字段仅
`end_nest_l1_mode` / `end_nest_l3_mode`）、铁律全过、
配对 `[7,11,23]` 无缺失、overflow 恒 0、完成率恒 1、硬违约恒 0。

**场景确实生效**：`run_meta.arrivals_per_cycle` 实测
`{1:20, 2:10, 3:10}`（40 任务），exp09 为 `{1:20, 2:5, 3:5}`（30 任务），
均 `pool_empty` 自然收敛；`nn` 档 f 按 seed 从
`[4152.968, 4170.778, 3730.472]` 抬到 `[5285.400, 5454.331, 5113.950]`
（+1132.432 / +1283.553 / +1383.478），航程同步 +11.3~13.8 km。

**同 seed 配对效应**（负=更优）：

| 效应 | f | p | 航程 (m) | 返航段 (m) |
|---|---|---|---|---|
| L1@L3n | +12.244580 | 0.406644 | +120.101 | −551.356667 |
| L1@L3m | +20.296046 | 0.195534 | +200.615667 | −470.842 |
| L3@L1n | −8.051466 | 0.422650 | −80.514667 | −80.514667 |
| L3@L1m | 0 | `zero_variance_no_test` | 0 | 0 |
| 交互 | +8.051466 | 0.422650 | +80.514667 | +80.514667 |

**所有可检验 p > 0.19，均未达统计显著，不得写成「无差异」**。
完成率 / 硬违约 / overflow / 按时率的层 3 主效应全 0。

**分叉性质**：层 3 只在 seed 11 且层 1 用投影时分叉，
`nn → nm` 为 f −24.1544、总航程 −241.544 m、返航段 −241.544 m
（去程不变）、按时率与迟到不变、overflow 仍 0，
终点巢签名显示一对 UAV 的终点巢对调。`mn≡mm` 逐 seed 成立
⇒ 交互完全由「层 1 用投影时层 3 才有优化空间」驱动，
**禁写协同效应 / 独立收益**。`cost_polish_n_mcf_resolve` 四档三 seed
**恒为 1** ⇒ 交付期 MCF 每次都实际调用，不是算子没跑；
它只解决给定路径下的联合最优分配（**条件最优，非全局最优**），
不解决可行性。

**与 exp09 的关系（关键）**：绝对质量量不同（任务集 40 vs 30），
故禁止声称「逐位复现」；但四档**配对差**（全部主效应与交互项）
与 exp09 正式档逐位相同：f 交互同为 +8.051466 / p=0.422650，
分叉同样只在 seed 11、幅度同样 −241.544 m。新增的 10 个任务
在四档里被以相同方式服务，额外代价是与终点巢策略无关的固定增量。
⇒ **exp10 是同一现象在更大任务规模下的复现，不是第二条独立证据**，
禁写「双双验证」「独立验证」；跨场景禁止配对 t 检验。

**定量口径佐证**：层 3 `Δf = −8.051466` 与 `0.1·Δ航程 = −8.0514667`
精确一致，f / 航程 / 返航段是同一条证据的线性缩放，不是三条独立证据；
层 1 `Δf = +12.244580` 而 `0.1·Δ航程 = +12.0101`，差额 +0.2345
恰等于迟到项 `0.01 × 23.449667 s` ⇒ 层 1 改变整条搜索轨迹时
f 与航程**不是**线性缩放（与 §4 mid 档辨析一致）。

**对「容量竞争下 MCF 解决什么」的回答（诚实结论）**：
`burst` 提高了并发到达与总任务数，但 overflow 恒 0、
MCF 重解恒 1 次/seed、分叉仍是同一处纯成本改派，
**未制造出终点巢容量竞争**，立项时的预期被实测推翻。
结合 `tight` 反例（降泊位须同时降机队，反而缩小可行分配空间），
可写为：当前机巢 / 机队 / 到达参数化下，单靠提高并发到达
不足以产生末态容量竞争，MCF 的可观测作用仍是给定路径下的返航代价优化。
真正的容量竞争场景须另设（保持机队规模而改变返航时刻分布 / 泊位布局），
并重新核对铁律；本实验未擅自改动生产场景。
**禁写「MCF 无用 / 可删」「容量压力带来 X 收益」**。

**运行口径**：首次正式档进程在 6/12 档时被宿主回收且曾被挂起约 80 分钟
（`wall_seconds` 失真），该批半截产物整体归档
`results/_interrupted/burst_formal_2026-11-08/`，**未参与任何结论**；
从头完整重跑后墙钟恢复正常（447.6–572.9 s）。墙钟只作观测，
不与 exp07 / exp08 / exp09 横向比较。
产物：`exp10/results/burst/formal/summary.json`、`summary.csv`、
逐档 `metrics.json` / `run_meta.json`、`figures/mcf_layers_burst.png`。

### 6. 后续待办（P4 剩余，严格串行、正式档单独跑批）

① 容量竞争场景的正确构造（须另设，未擅改生产场景）；
② 三级嵌套消融 + 2×2；③ Deb / ε / Lagaros 开关对照；
④ 统一事后评价器重评 phase1 与本版交付解；
⑤ 重跑全部基线 + 配对 t 检验如实声明。
待裁定不变：`coverage_first` 第三来源是否纳入总闸、
§4.3「双开」与生产默认 `coverage_gate=False` 的张力、
`projection_only=False` / `compare_mode="penalty"` 与 §6 表格不一致、
`independent_audit` 几何重建重评接入。

---

### 11.13 数据环境补验收与场景口径澄清（2026-11-09）

**背景**：§11.1「已知环境缺口」及 `docs/rolling_review_2026-11-03.md` 中
「本机无 DEM/建筑数据」等表述，均为 2026-11-06 前 fresh clone 环境的记录。
本轮用现有数据补跑搁置的验收并更正；**用户裁定：`guangzhou` 为当前实验场景**。
本轮**未启动任何实验、未改任何实验 config、未改生产默认值、未改数据文件**。

#### 1. 数据齐备性（实测）

| 场景 | 内容 | 加载自检 |
|---|---|---|
| `guangzhou` | `dem.tif` + `building/`（5 类建筑 shapefile，20670 栋）+ 3 机巢 CSV + `region.geojson`，26M / 44 文件 | `build_terrain` → `DEMTerrain`，`build_cost_estimator` → `VerticalSectionCostEstimator`；`scenario=guangzhou`、3 机巢 |
| `lasa_chengguan` | `Chengguan_DEM.tif` + 6 机巢 CSV + 城关区多边形 | 目录齐备，可切回 |

- `pytest task_allocation/tests/test_task_region.py -q` → **8 passed**
  （§11.1 当时 6 个 DEM/geojson 数据缺口失败已全部消失，非代码修复所致）；
- `pytest task_allocation/tests/test_p0_contracts.py -q` → **19 passed**；
- 全量 `pytest task_allocation/tests tests -q` → **549 passed in 28.91s**。

#### 2. exp01 审计补跑（§4.3 第 4 条）

```
.venv/Scripts/python.exe -u task_allocation/experiments/exp01_smoke/audit_correctness.py
→ AUDIT_RC=0；3 个 seed 目录（42/43/44）合并，26/26 通过，无 WARN
```

落盘 `effective_config.json` 的 `data` 段为 `scenario=guangzhou` /
`dem=dem.tif` / `buildings=building` / `problem.n_nests=3`，即审计确实在
**广州城市场景（DEM + 建筑包络）** 下复核，A–O 各不变量（终局守恒、完成唯一、
迟到、奖励守恒、航程物理有序重算、机巢容量、ETA 一致性等）全部 PASS。

**项数勘误**：§4.3 第 4 条与 `exp01_smoke/notes.md` 历史段落写「25/25」，
是旧版检查项数；提交 `bdc32b9`（补「无超期淘汰 + 软窗」口径）新增 A2
「淘汰恒为零（结构性保证）」后共 **26 项**。属检查项扩充，不是新增失败，
也不是口径退化；§4.3 条款措辞按验收源纪律保持原样，以此勘误为准。

#### 3. P0 数值核验的边界（更正一处易误读的表述）

P0 bit-exact 用的是**合成采样探针**（`docs/p0_criteria_drift_baseline_2026-11-06.md`
明示「不依赖 DEM 场景数据」，seed=20261106）+ 全量单测，因此**从未受数据缺口
影响**。§11.1 把它与「缺数据」并列只说明验证手段，不表示 P0 核验被阻塞；
数据缺口实际只阻塞 exp01 审计与论文数字对账。P0 契约 19 项已在上述全量回归中通过。

#### 4. 仍未闭合（不得据此宣称验收完成）

- 本次审计针对 `exp01_smoke/results/` 的 **2026-11-05 20:40 落盘批次**，
  验证的是该批次内部一致性；11-07 之后生产代码已有默认变更（§11.9 采纳判据
  统一 `f`、`l3b_enabled` 默认开）。故 **26/26 不等于当前代码在 guangzhou 场景
  的端到端验收**。
- §4.3 第 4 条的「论文数字对账」仍须用当前代码重跑 exp01 后回填；按纪律
  正式档须单独发话、严格串行跑批，本轮未启动。**（以上两条为 11-09 时点记录；
  2026-11-10 已用当前代码重跑闭合，结论与限制见 §11.14。保留原文以保持
  历史快照，勿再按本段判断当前状态。）**
- §4.3 第 5 条（与 `method-phase1` 同场景、同 seed、同预算配对 t 检验）与
  P4 待办「统一事后评价器重评 phase1」仍未做，见 §11.12 §6。
- exp05 v3 正式档遗留（seed 43 早停使完成约束仅 30/35 成立、`f_audit` 混尺子、
  单周期口径退化 210 项）不因本节补验收而改变，见
  `docs/exp05_v3_formal_results_2026-11-09.md`。

#### 5. 场景口径（用户裁定）

- **当前实验场景 = `guangzhou`**：`data/README.md` 已登记；`exp01_smoke`、
  exp07 / exp08 / exp09 / exp10 均使用该场景（3 机巢、DEM + 建筑垂直切面包络）。
- `lasa_chengguan` 保留在库、可切回（6 机巢、DEM、无建筑）。**exp05 v3 正式档
  使用该场景**，属另一场景的历史批次：跨实验**禁直接比绝对值**，也禁把它的
  数值写成当前 guangzhou 场景结果。切换场景只改各自 `config.yaml` 的 `data:`
  段与 `problem.n_nests`，代码零改动（`data/README.md` §场景切换）。
- 本轮未统一 exp05 v3 的场景，也未新增 guangzhou 外效度对照档；如需要，另发话、
  另跑批。

#### 6. 本轮改动

- `docs/stage2_final_scheme_2026-11-06.md`：§11.1 环境缺口段改写 + 新增本节；
  **§0–§10 拍板内容零改动**（验收源不得走样）。
- 未新增/修改代码、未跑实验、未提交 git。

### 11.14 当前代码 exp01 端到端重跑与论文数字对账（2026-11-10）

**触发**：§11.13 §4 列出的未闭合项「§4.3 第 4 条论文数字对账须用当前代码
重跑 exp01」。用户本轮发话「进行实验；分析问题」即正式档单独跑批授权；
`guangzhou` 为当前实验场景。详见
`docs/exp01_current_code_reconcile_2026-11-10.md`。

#### 1. 跑批与验收

```
FORMAL_START 2026-11-10 14:31:22 +08:00
.venv/Scripts/python.exe -u task_allocation/experiments/exp01_smoke/run.py --no-plots
FORMAL_RC=0（17m57s，3 seed 严格串行、不渲图、不并行）
```

3 个 seed 均周期 4 `pool_empty` 自然收敛、30/30 完成；末尾自动审计
**逐 seed 3 目录合并 26/26 PASS、无 WARN**。11-05 旧批次（17 文件 / 30M）
完整归档到 `experiments/_archive/exp01_smoke_results_2026-11-05_pre_current_code/`，
未删数据。**§11.13 §4 第 1 条「26/26 不等于当前代码端到端验收」至此闭合**；
§4.3 第 4 条的「论文数字对账」结论见下条，但**不等于论文数字已可直接引用**。

#### 2. 当前代码在 guangzhou 的新基线（取数以此为准）

`results/metrics.json` 的 `seed_summary`（3 seed 均值±std）：完成 30.00±0.00 /
完成率 **1.000±0.000** / 按时率 **0.9778±0.0157** / 总航程
42953.63±2570.25 m / 每完成航程 1431.79±85.67 m / 迟到 0.667±0.471 /
求解 342691±14910 ms；淘汰、硬违约、服务不可达、剩余池全 0，恒等式 3/3 闭合。

#### 3. 论文数字对账结论：历史主表数字整体作废

历史主报告 §2 的 exp01 数字（按时 0.7695±0.0218、每完成航程 24.22±0.64 km、
60 任务）属 **8 机巢 / 80 km 满电 / 60 任务 / 4.5–9 周期**口径；提交 `55f4b5b`
切换口径时该报告被删除，此后又切到 `guangzhou` 3 巢 / 10 km / 20-5-5。
**跨三套口径禁比绝对值、禁算百分比、禁配对 t 检验**；由其推出的
「−13% / −15.8pp」一并作废（长期记忆中「exp01 按时 0.7695 / 24.2 km」
应标为历史口径、禁止进论文主表）。当前工作区已无 `FULL_REPORT.md`，
仅在 git 历史中可查。

#### 4. 与 11-05 同题批次对照（描述性，禁作受控推断）

`solution.json` 的初始机队 / 30 任务 / 3 机巢 / `fitness_weights` **3/3 seed
逐位一致**（`metrics.fleet` 是末态快照，其 `nest_id` 排列差异属交付解、
不是初始条件）。逐 seed 差值（新−旧）：总航程 +41.3 / +692.1 / **−2435.4 m**，
按时率 1.000→0.9667（2/3 seed 各 1 个迟到，9.03 s / 84.30 s），
交付后 `cost_polish_gain_m` −240 / −136 / −487，求解耗时 +10~16%；
完成数、完成率、不可达、淘汰、硬违约、`total_reward` 逐位一致。

逐周期诊断显示终点机巢 `end_nests` 全变、解码期 `polish_accepted.relocate`
由 12/11/6 次降到 0/1/1 次、新增修复三分流与返航段诊断。11-05 之后有
**多处并发改动**（终点巢分层、修复三分流、解码期算子收口、`l3b_enabled` 默认开、
采纳判据统一 `f`、`coverage_audit` 换名），故**不能单因素归因**、
**禁做跨版本配对 t 检验**、禁写「L3b/MCF 带来收益」或「成本换时效」。
`n_repair_failed`=24/21/34 是搜索期候选诊断计数（含被淘汰候选），
`delivery_refused` 全 false，不得误读为交付失败。

#### 5. 口径红线（实测确认）

- `f = 0.1·path + 0.01·late_s` 在 9 个有作业周期残差全为 0 ⇒ `f` 与航程
  **线性同源，不是两条独立证据**；
- `total_f2 = path + penalty` 混加米与秒（本批次差 9.033 / 84.301 即迟到秒数），
  **禁当航程引用**，航程一律用 `total_path_m` / `total_cost`。

#### 6. 仍未闭合

① **完成约束反例未消除**：本场景 3 seed 100%，但 exp05 v3 在
`lasa_chengguan` 的 seed 43 仍遗留 1 个可完成任务（早停判据待修），
**禁写「当前代码完成率恒 100%」**；
② 单因素归因须在当前代码内另做受控消融（L3b 总闸 / 终点巢层 1×层 3 /
解码期 polish / 采纳判据），正式档另发话另跑批；
③ §4.3 第 5 条 phase1 同场景同 seed 配对 t 检验、P4「统一事后评价器重评
phase1」仍未做；
④ exp05 v3 场景为 `lasa_chengguan`，与本批次**禁直接比绝对值**；
⑤ 本次 `--no-plots`，无新图件，旧图属旧批次不可配新数字。

#### 7. 本轮改动

新增 `docs/exp01_current_code_reconcile_2026-11-10.md`、回填本节与
`exp01_smoke/notes.md`；**未改生产代码 / 实验 config / 数据文件、未渲图、
未提交 git**。






