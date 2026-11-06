# 第二阶段重构：约束语义裁定 → 三层架构规划 → 实施计划

> 2026-11-06 | **状态：已被 `stage2_final_scheme_2026-11-06.md` 取代为定稿，本文保留为 v1 规划**
> ⚠️ 本文 §1 的文献核实基于公开摘要与 Springer 预览，**有 5 处与原文不符**；
> 以定稿 §7 的勘误表为准（Lagaros 全文已核对）。§6 的 6 个待拍板项中，第 1、4 项已由定稿收口。
> 未改动任何生产代码；P0–P4 仍待用户发话。
> 依据：`methods/muas` 全量代码 + 四层约束语义审计（本会话）+ 三步机制诊断
> `ab_mechanism_3steps/SYNTHESIS.md` + 选择口径探针 `selection_gap_report.md`
> + 既有 `docs/stage2_refactor_analysis_2026-11-06.md`（同日，依据 Lagaros2023）
> 文献：Lagaros et al. (2023) *Optim. Eng.* 24(4):2251-2298, DOI 10.1007/s11081-022-09782-9；
> Salcedo-Sanz (2009) 修复综述；Gandomi & Deb (2020) *CMAME* 363:112917 (BU)；
> Rahimi et al. (2024) *Sci. Rep.* 14:4816 (BU 扩展)。
> 对照基线：`method-phase1` / `phase1-evolution-decode-mcf-polish` @ `46b9739`（不可移动）。

---

## 0. 本文与既有分析文档的关系（先说清楚，避免两套计划）

`docs/stage2_refactor_analysis_2026-11-06.md` 已回答「**为什么改**」：现状把 Lagaros2023
中本应分立的两类 CHT 合并进每代每体的 `build_solution`，且无统一决策台账。本文回答
「**怎么改、怎么验、按什么顺序改**」，并裁定它与本轮用户方案的两处分歧。两者配套，
不互相替代：

| | 既有 analysis 文档 | 本轮用户方案 | 本文裁定 |
|---|---|---|---|
| 分层 | ① 可行性修复（Baldwin 默认）② ε-constrained 统一比较器 ③ 显式预算的目标搜索 + 受控 Lamarck 回写 | ① 进化搜索（DE 不变、纯惩罚）② 可选硬约束修复（Baldwin）③ 可选交付精修（不改归属/顺序） | 层次可合并为同一套：层 1 = 既有①+②的一部分，层 2 = 既有①，层 3 = 既有③。见 §4.3 |
| 治精英冻结的杠杆 | §3.2 明确为「**ε-constrained 比较器放松早期选择压力**」+ 受控 Lamarck 回写 | 「剥掉解码期优化、候选直接竞争」⇒ 精英更新频率显著上升 | **方向冲突，必须实测裁定**。见 §2.3、§5 P1 前置闸门 |
| DE 选择算子 | 主张替换为 ε-constrained 三元比较 | 明确「层 1 DE 核心保持不变」 | 折中：比较器做成**独立可开关的 `CompareSpec`**，默认关、A/B 验证；DE 的变异/交叉/反映射一律不动。见 §4.3 |

---

## 1. 文献核实（四处需要校正，两处完全支持）

### 1.1 支持：修复是可选组件、回写比例取决于问题性质
Lagaros2023 §2.4 原文（p.2264）首段：「*The use of the repaired individual depends
principally on the nature of the problem and the developer decision and varies between
either replacing the original individual with the repaired one, or only a small
percentage, or every infeasible individual in the population*」——**这一句直接支持
「修复 = 可选组件、开发者决定替代多少个体」**。
⚠️ 订正：v1 曾写「§2.4 明确提到 repair probability parameter，用户只需调修复概率」，
该词实际只出现在 Chootinan & Chen (2006) 的具体方法描述里，**不是 §2.4 的通用论断**；
且原文的「比例」指**修复后个体替换原个体的比例**（Baldwin↔Lamarck 连续谱），
不是「有多少个体被执行修复」。§2.4 引 Salcedo-Sanz (2009) 修复综述，后者把「以修复作
约束处理的 hybrid EA」与「以局部搜索改进适应度的 memetic algorithm」明确区分：
> "the LS of a hybrid algorithm is used to handling constraints, in this way reducing
> the search space size... Using a repair technique reduces the search space to feasible
> solutions; **no special operators or modifications of the fitness function need to be
> considered**... the computation time required for repairing the solution is usually higher."

⇒ 用户的「修复 = 可选组件、开发者决定替代多少个体」**成立**；既有 analysis 文档 §2.1
已把它表述为「逐算子声明 Baldwin / Lamarck」，两者一致，直接采纳。
⚠️ 但 Salcedo-Sanz 同时给出两个代价，用户方案未提及：① 修复法与惩罚法是**两条路线**
（修复后不再需要改适应度），本方案想「层 1 惩罚 + 层 2 修复」并用，属**混合 CHT**，
必须回答「被修复个体与未修复个体如何同台比较」；② 修复**计算开销更高**，与「层 2
每体都跑」叠加会抬墙钟，需预算上限。

### 1.2 已校正：FeasRulesNEW 是**适应度变换**，EconstNEW 是**比较规则**，都不是问题目标
Lagaros2023 摘要原文：8 个新变体基于 feasibility rules 与 ε-constrained，特征为
> "consider the **level and number of constraint violations, besides the objective
> function value, for selection of individuals within a population"

原文核实（p.2269–2270、表 2 p.2275）：
- **FeasRulesNEW1–4**：`p_violation`（Eq. 8a = 最大违反量；8b/8c/8d = 最大/均值/中位数
  违反量 ×(1 + 违反条数/总条数)），适应度 `F(x)=f(x)` 若全可行，否则
  `max(f_best_feasible, f(x)) × p_violation`（Eq. 9b）——是**乘性适应度重标定**。
- **EconstNEW1–4**：Eq. 13 的 ε-level 四分支**比较规则**（配合 Eq. 8a–d 与 Eq. 12 调度）。

⇒ 用户「违反层级 × 违反数量 × 客观函数值」的**方向正确**；但 v1 把它判成「纯选择判据、
不是目标函数」**不准确**——原文的 FeasRulesNEW 确实改写适应度。正确表述是：
它改写的是**算法内部选择标量 `F_sel`**，不是问题的**目标函数 `f`**。本项目的 `f`
承载 big-M、`λ=0.1`、`w_late=0.01/s` 与航程汇率（10 m ≈ 1 s）的标定，
若直接乘进去将同时毁掉：`W_hard` big-M 标定、`λ` 统一口径、`w_late` 汇率、
所有诊断脚本口径 —— **与 `method-phase1` 的 f 绝对值不再可比**。
⇒ 落地为 `CompareSpec`（见 §4.1）：默认 Deb 三规则，ε / Lagaros 两类变体作可选对照档，
全部只作用在 `F_sel`，不动 `f`；与既有 analysis 文档 §3.2 的 ε 比较器**合并为一个组件**。
（v1 曾写「准确公式未能核实、不要凭乘积二字写实现」——现已核实：**原文就是乘积**，
但我们仍不照搬，理由是标定与可比性。）

### 1.3 需校正：BU 方法只能**类比**，不能写成「UAV 版 BU」
Gandomi & Deb (2020) BU 是**隐式** CHT：直接在**变量上下界**上按约束收缩
（"directly updating variable bounds with constraints"），明确
"**rather than constraint handling by penalty or other fix-ups**"，且 Lagaros2023 §2.4
指出它 "requiring constraint variables pre-categorizing"。
本项目的**航程/能源是函数约束**：`_tour_cost(uid, routes[uid], end_nest)` 依赖整条
访问顺序、任务集合与终点巢，不是某个基因分量的 box bound。`decode_repair.py:581`
的 `_repair_range` 实际是「弹出任务直到航程 ≤ limit」，属 Lagaros 第 4 类
**projection / repair**，不是 BU。
⚠️ 更要注意反向风险：Rahimi et al. (2024) 扩展 BU 时指出 BU 会
"twist the search space, which can make the optimization problem more challenging"，
并需在找到可行域后**切换回原问题**。若真做成边界收缩式投影，**可能加剧精英冻结**，
与用户预期相反。
⇒ 论文表述用「受 BU 思想启发的航程可行性投影」，不写「BU 方法的 UAV 版」。

### 1.4 需校正：Zhao Ming 2017 的「原意」与仓库记录不符（论文引用风险）
仓库 `docs/research_positioning_20261012.md:187` 把母本 **Zhao Ming 2017** 记录为
本项目「可验证增量」的对照对象，列出的母本特征包括「选择性剔除 + **解码期约束修复**
（prune → repair_range → choose_end_nests 迭代至不动点）」与「**Lamarckian 回写**
只回写 start/terminal 两通道」；`tests/test_operators_fidelity.py` 逐条核对母本
公式 3-9 ~ 3-12。`docs/design_nest_state.md:153` 确有
`f2 = 路径代价 + w_penalty × penalty`，但 `:172` 明确处置顺序是
「① repair 改派 → ② penalty 兜底 → ③ 落地拒绝」，即**修复与惩罚并存**。
⇒ 「Zhao Ming 原意 = 惩罚驱动、不可行时不强制修复」与仓库既有对标口径不一致。
**建议**：把「软约束惩罚驱动、不强制修复」写成本阶段的**设计选择**（依据 Lagaros
惩罚类 CHT + ε-constrained），Zhao Ming 2017 仍只作为**编码与算子母本**引用。
实施前请用户核对原文并给出确切出处（本会话未检索到该文全文，不臆造 DOI）。

### 1.5 结论成立，但原文依据须改写：主动优化应移出解码期
原文核实后，两条「文献依据」都不成立：
- 「同一时刻只有一个 CHT 生效、按种群状态显式切换」只是 **Malan (2018) landscape-aware**
  的具体做法（p.2263–2264）；同节 Mallipeddi & Suganthan (2010) 的 ensemble 反而是
  **四种 CHT 并行、各管一个种群**。
- 全文 `local search` 仅 3 处，均为 §2.6 中 Rosso et al. (2022) 的
  「new local search operator … to help localize the feasible region」，
  **没有**「预算受控、只对选定个体调用」的表述。

⇒ 该结论改由本项目自己的两条方法论铁律承担依据：
「成本优化必须作用于交付解」「解码期算子干扰 DE 搜索」，并与用户第三条原则、
既有 analysis 文档 §3.1/§3.3 一致，**仍为最高优先**，只是不再挂 Lagaros 名下。

---

## 2. 用户方案逐条审查

### 2.1 五条拆解原则裁定

| 原则 | 裁定 | 依据/修正 |
|---|---|---|
| 修复 = 可选组件、可决定替代比例 | ✅ 采纳 | §1.1；现 `lamarckian` 是全局二值，需升级为逐算子 `replacement` 策略 + 修复比例 |
| 软约束 = 惩罚驱动、不强制修复 | ✅ 采纳但需重标定 | 现 `f` 已有 `w_late·L` / `W_skip·n_skip`（`problem.py:401-404`），但 `w_violation=None` 时迟到项为 0、门禁下 `n_skip≡0` ⇒ 两条惩罚**从未真正咬合**，需标定（§2.4） |
| 主动优化 = 交付后可选 | ✅ 采纳（最高优先） | §1.5；`_consolidate`(`decode_repair.py:504,546-549`)、`_best_order`(`:309`) 需拆 |
| 硬约束（航程/机巢/覆盖）不变 | ✅ 采纳，且升为**红线** | 见 §2.5 |
| 软约束保留惩罚、不被强制修复吃掉 | ✅ 采纳 | 但「覆盖」在本项目是**硬约束**，探索期放宽须有独立 ε 契约（§2.4） |

### 2.2 三层设计的三处**字面不可行 / 内部矛盾**（必须修正后再实施）

**(a) 「层 1 不执行任何修复、机巢选择」在代码上不可实现。**
`cost_vector → inverse_phi → Individual.assignment` 只是 N 个标量 + 编码规则；
`problem.scalar_fitness` 的输入是 `AllocationSolution`（`problem.py:396`）。
`late / path / coverage` 全部定义在解结构上，且 `late` 依赖 ETA，ETA 依赖访问顺序
与终点巢 ⇒ **不做终点巢选择，`C_late` 无定义**。
另外 `inverse_phi` 内的 `repair_rules`（`nearest_match` / `unique_filter` /
`invalid_mutator` / `terminal_nest`）本身就是 Lagaros 第 4 类「编码边界合法性修复」，
去掉基因即失效。
⇒ **修正定义**：层 1 = 「结构翻译 + 编码边界合法性 + 硬约束可行性投影，
**零目标下降型改写**」，而不是「零解码」。要剥的是 `opt` 动作，不是解码本身。

**(b) 「修复链长度归零」应改为「`reason=opt` 动作数归零」。**
`decode_repair.py:109-122` 的 6 轮不动点不是「链长」而是**硬约束耦合拓扑**：
航程修复 pop 任务 → 覆盖补全补回 → MCF 换巢拉长返航 → 再触发航程修复
（`:156-163` 末段又闸三次）。`decode_constructive.py:171` 的构造式解码同样保留 ≤3 轮。
层 2 必须保留不动点迭代，只是每轮只做 `hard` 投影、不做成本比较。

**(c) 层 3「绝不改变任务归属和顺序」与「顺序微调、防逆序」自相矛盾。**
且 `cost_polish_relocate_enabled=True`（`stage_config.py:172`）是跨机改派；
`polish_assignment` 的 relocate/swap/position 也改归属。项目已知缺陷站位记录
「polish 从不优化航程（`muas_stage.py` 早退），**增益来自救回任务**、与 λ 正交」
⇒ 禁改归属会让层 3 收益大幅缩水，甚至不显著。
⇒ **建议拆两档**：`L3a` 纯几何（2-opt/Or-opt + 段内定序，不改归属/执行集）、
`L3b` 归属类（relocate/swap，显式开关、单独记 Δf 与 `task_to_uav` 变动）。

### 2.3 「精英更新频率会显著上升」是**待验证假设**，不是既定收益（最重要）

三条既有证据指向相反方向：

1. `ab_mechanism_3steps/step1`：精英冻结代占比 **0.89**、总增益 11.62、
   改进代数 ≈ 0.2，根因是「**子代系统性劣于初始精英**」，不是「候选没参与竞争」——
   候选一直参与，`dmde_solver.py:225` 的 `if child.fitness < population[i].fitness`
   就是父代/子代同台竞争。用户表述的「候选直接参与父代竞争」**不是新增机制**。
2. `ab_optimization_attribution/probe_mechanism.py`：`lamarckian=False` +
   gen80/pop20 下进化臂**仍等于裸修复基线**（7937.661）。即「不替代原始基因」
   （Baldwinian）单独并不能恢复进化增益。
3. 既有 analysis 文档 §1.2/§3.2 的机制判断恰好相反：解码链的目标改进是
   「**事实上的 Baldwin 式、对全体同口径施加**」⇒ 不产生可遗传的个体间差异 ⇒
   DE 无梯度可爬；它开出的药方是 **ε-constrained 比较器**与**受控 Lamarck 回写**，
   而用户方案固化了 Baldwinian、且未采纳比较器。

**但用户方案确实引入了一个真正的新变量**：剥掉解码期目标改写后，`f_eval` 的信号
结构改变 —— 不再对每个基因一视同仁地施加同一份局部改进，理论上会**增大候选间
适应度方差**，可能恢复梯度。机制可成立，**方向不确定**（也可能因硬约束投影更强、
表型碰撞率上升而更糟；step1 的「碰撞率 0」是旧链下的结论，不能外推）。

⇒ **裁定**：把「精英更新频率上升」降级为 P1 的**前置验收指标**，用 step1 现成
工具实测，不达标则不进入 P2。这本身就是「贡献可独立验证」的第一个实例。
验收线：冻结代占比 < 0.89、精英总增益 > 11.62、改进代数 > 0.2、
表型碰撞率不显著上升、完成率恒 100%。

### 2.4 覆盖「探索期软 / 交付期硬」会引入两处新风险

- **反激励**：审计已证 `f` 不含 `−m·F1`（`problem.py:406,427-429`），
  少交付一个任务时 `path / late / n_hard` **全部变小 ⇒ f 更优**。若探索期允许
  `n_skip>0` 并只加「覆盖不足临时惩罚」，则必须标定
  `λ_cov > max_x (Δpath + Δlate + Δhard)`，否则 DE 会学会「偷懒」。
  现 `W_skip = W_hard`（big-M）理论够，但**从未在这条路径上标定过**（生产 `n_skip≡0`）。
- **错配未按 §4.10 消除，只是换了来源**：交付层强制覆盖门禁 ⇒ `S_eval` 与
  `S_final` 在覆盖维度仍不同，`probe_selection_gap.py` 揭示的
  `select-then-polish ≠ polish-then-select` 会变成 `select-then-gate ≠ gate-then-select`。
  ⇒ 覆盖门禁必须纳入「评价一致性」契约，报告并列 `n_skip^eval` / `n_skip^final`，
  **禁止混用同一个数字**。
- **「覆盖门禁唯一主口径」危险**：`stage_config.py:93-95` 与 exp04 E1E2 实证
  「门禁单独上场时覆盖指标恒真而**完成率 −5pp**」，注释明确「完成语义由
  **覆盖优先装载 + 单调 polish** 两段式承担，门禁退居事后校验」。
  ⇒ 完成语义必须由 `coverage_first` / `_load_coverage_fixed_point` 构造式装载承担，
  门禁只作终态校验与补漏；把门禁设为唯一主口径会重演 exp04 的失败。
- ε 建议采用 Lagaros **§3.3**（v1 误写 §3.1，§3.1 是 penalty methods）的
  ε-constrained 调度（由松到紧收敛到 0）；原始调度为 Takahama & Sakai 的 Eq. 11，
  **8 个新变体实际采用 Fan et al. (2018) 的 Eq. 12**（含可行解比例 `rk`、`TC`、`cp`、
  `τ`、`α`）。须显式声明采用哪一式、参数取值、收敛代数，以及交付层构造性补全的兜底。

### 2.5 硬约束红线（任何阶段不得破）

来自项目铁律，每阶段合并前逐条断言：

1. 完成率（分母排除 UNREACHABLE）恒 **100%**；`arrived = done + unreachable + remain`。
2. **不存在「超期/过期淘汰」**：出现 `eliminating`（非不可达）= bug，须修到 0。
3. `gate_mode()` 恒 True、`coverage_gate` / `lexicographic_coverage` 双开。
4. `λ = 0.1` 全实验统一；唯一距离 = 垂直切面 DEM 三维航程，**有向**，
   禁对称近似；落盘坐标不得 round。
5. `sequence` 恒 True，按 `sequence_order` 判**同一航次内**顺序（跨机跨周期未实现）。
6. 实验独立性：各实验自持 config/build/paths/metrics/plot，禁建 `_shared/`。
7. 与 `method-phase1` 对照时**只比指标层**，不比 f 绝对值（见 §5.1）。

---

## 3. 关键冲突汇总（按严重度）

| # | 冲突 | 后果 | 裁定 |
|---|---|---|---|
| C1 | 「层 1 零修复」不可实现 | 无解码 ⇒ 无 `late/path/coverage`，评价链断裂 | 修正为「零目标下降型改写」；MCF 降级为层 1 确定性投影（最近合法巢），层 3 才做最小费用优化 |
| C2 | 「精英更新上升」与既有机制判断相反 | 若直接实施 P2/P3，可能白改一轮 | 降为 P1 前置闸门，实测不过不推进 |
| C3 | 「覆盖门禁唯一主口径」重演 exp04 −5pp | 完成率铁律破 | 完成语义交 `coverage_first` 构造式装载，门禁只作终态校验 |
| C4 | 层 1 惩罚与层 2 修复属混合 CHT，比较规则未定义 | 被修复/未修复个体不可比 | `CompareSpec` 统一比较算子（含 Lagaros 三元 + ε），默认关、A/B |
| C5 | 覆盖探索期软 ⇒ DE 学会偷懒 | 完成率破 | `λ_cov` 标定到 > 少做任务收益上界；`n_skip^eval` / `n_skip^final` 分列 |
| C6 | 层 3 禁改归属 + 字面矛盾 | polish 收益缩水、层 3 不显著 | 拆 L3a/L3b，分账；先做「几何 vs 救回任务」收益拆分实验 |
| C7 | Lagaros 变体若乘进 f | 毁 λ / big-M / 汇率标定，phase1 不可比 | 只改比较算子，不动 `f` |
| C8 | Zhao Ming「原意」引用口径 | 论文与既有对标节自相矛盾 | 改为「本阶段设计选择」，Zhao Ming 只作编码/算子母本 |

---

## 4. 目标架构（规划）

### 4.1 三层 + 三个契约

```text
基因 g (cost_vector)
  │  inverse_phi + repair_rules（编码边界合法性，Lagaros 第4类，保留）
  ▼
【层 1 进化搜索 / 评价】  DE 变异·交叉·反映射 一律不动
  │  结构翻译 → 硬约束可行性投影（只向可行移动，零成本比较）
  │  f_eval = W_hard·n_hard + w_exc·excess + λ_cov·n_skip^eval
  │           + w_late·L + λ·path            （f 公式保持不变）
  │  → 候选与父代同台比较（CompareSpec 可开关，默认沿用硬贪婪）
  ▼
【层 2 可选硬约束修复】  只做「必须满足」：
  │  航程/能源投影 · 机巢合法性+容量 MCF · 覆盖构造式补全
  │  不动点迭代保留（≤3~6 轮），每轮 reason=hard
  │  修复个体仅用于评价（Baldwin 默认）；replacement 比例可配
  ▼
【层 3 可选交付精修】  只对最终可交付解
     L3a 纯几何：2-opt/Or-opt + 段内定序/防逆序（不改归属、不改执行集）
     L3b 归属类：relocate/swap（显式开关、单独记账；默认关，供对照）
     所有采纳判据统一为 f(cand) < f(cur) − ε 且约束向量不退化

贯穿三契约：
  ConstraintSpec  —— 每约束唯一 id / 对象层级 / 硬软 / judge() / 归属层 / 处置 / 单位
  DecisionLedger  —— 逐次 (代, 层, 算子, constraint_id, action, object, from, to,
                       reason=hard|opt, Δf, Δviolation, 接受与否, 闸门原因)
  CompareSpec     —— DE 替换 / 邻域接受 / 档案择优 全部走同一比较算子与 ε(t)
```

### 4.2 约束归属矩阵（每层只准引用 `ConstraintSpec.judge()`）

| 约束 | 层 1 | 层 2 | 层 3 | 唯一判据来源 |
|---|---|---|---|---|
| 任务唯一性 | 结构去重 | 复核 | 执行集门复核 | `validate_unique_tasks` |
| 航程/能源 | 投影（不比较成本） | 不动点投影 | 航程门 | `check_range_constraint` |
| 机巢合法性/容量 | 最近**合法**巢（确定性） | MCF 精确解 | 容量复核 | `check_nest_capacity` |
| 终点巢成本最小 | ✗ | ✗ | 可选（L3，显式登记为 opt） | `nest_capacity.solve_end_nest_assignment` |
| 覆盖/完成 | `λ_cov·n_skip^eval` 软惩罚 | **构造式补全（承担完成语义）** | 门禁终态校验 | `coverage_gate` + `_load_coverage_fixed_point` |
| 时间窗/迟到 | `w_late·L` 惩罚，**不修复** | 不触发（软约束） | 迟到分量不增 | `task_window` / `soft_violation` |
| 先后顺序 | 惩罚/违约计价 | 同航次内构造性归位 | 防逆序复核 | `sequence_order`（逆序对口径） |
| 不可达两类 | 任务池出池，非违约 | 排除 inf 边 | 完成率分母剔除 | UNREACHABLE 判定 |
| 动态禁飞/避障 | 查询期时间窗 | 排除 inf 边 | 几何可行性复核 | `visibility_graph` |

### 4.3 与既有 analysis 文档的合并点

- 既有 §3.1「拆 `_consolidate` / `_best_order`」= 本文层 1 的「零 opt 改写」，**同一件事**。
- 既有 §3.2「ε-constrained 三元比较器」+ 用户「Eq. 8a–d/9b 层级×数量×目标」
  = 本文 `CompareSpec`，**合并为一个组件**，默认关、作为 P1 的 A/B 因子。
  这样「DE 核心保持不变」（变异/交叉/反映射不动）与「换比较算子」不冲突：
  比较器是**可选插入件**，不是重写 DE。
- 既有 §3.3「显式预算的目标搜索 + 受控 Lamarck 回写」= 本文层 3 + 一个
  **可选** `replacement` 策略（用户默认 Baldwin，Lamarck 作为 A/B 因子保留）。
- 既有 §3.4「决策台账」= 本文 `DecisionLedger`，字段扩展到含 `constraint_id`
  与 `reason=hard|opt`（这正是上一轮四层审计要求的可追踪性）。

### 4.4 现成基座（不必从零写，逐项确认）

| 基座 | 位置 | 用途 | 缺口 |
|---|---|---|---|
| `decode_mode="constructive"` / `_build_constructive` | `stage_config.py:210-225`、`decode_constructive.py:100-207` | 层 1/2 的构造式解码，已保留基因结构信号、已有 ≤3 轮耦合迭代、exp04 B1 已做 A/B | `_feasible_insert`(`:153`) 用「代价最优位」、`_removal_saving`(`:184-188`) 挑「节省最多」、`_reorder_for_deadlines`(`:194`) 仍在 ⇒ 需加 `projection_only` 子模式剥掉成本比较 |
| `w_late·L` / `W_skip·n_skip` | `problem.py:401-404,433` | 软约束惩罚项已在 `f` 里 | 需显式化 `w_late`、标定 `λ_cov`、区分 `n_skip^eval/^final` |
| `_ORDER_WINNER_COUNTS` / `_WRITEBACK_STATS` | `decode_repair.py` / `dmde_solver.py` | 现成计数器 | 默认关、口径不一，需并入 `DecisionLedger` |
| `cost_history` / `population_callback` | `dmde_solver.py:253-266` | 精英轨迹、多样性监控 | 可直接复用作 P1 验收 |
| `probe_selection_gap.py` / step1 / step2 / `verify_pop_sensitivity.py` | `experiments/ab_mechanism_3steps/` | 口径一致性、精英冻结、2×2 交互、种子敏感性 | 直接复用为验收工具，无需另建框架 |

### 4.5 必须统一的「另写判据」（上一轮审计的四处漂移）

1. 时间窗：解码直读 `task.latest`（`decode_repair.py:382,402`）vs 模型 `task_window`。
2. 时序：`_seq_violation` 单调比较 vs 模型逆序对计数。
3. 机巢容量：三份账（MCF / `check_nest_capacity` / `coverage_gate._nest_loads`
   `coverage_gate.py:171`）；`coverage_gate.py:118,126,150` 另写 `_tour_cost` /
   `_best_end_nest` / `_range_ok`。
4. 采纳判据：`stage_runner.py:425` 已算 `cand_fitness` 却未进 `:430-435` 的判据
   （现判据只看「航程严格下降」），⇒ 存在「f 变差却被采纳」的通道。
   **统一为 `f(cand) < f(cur) − ε` 且约束向量不退化**。

---

## 5. 实施计划（P0 → P4，每阶段一个提交、一个可回滚点）

| 阶段 | 内容 | 改动文件（主要） | 新增开关 | 行为变化 | 验收 / 回滚 |
|---|---|---|---|---|---|
| **P0 契约与可观测**<br>（零行为改动） | `ConstraintSpec` 注册表 + 唯一 `judge()`；修 §4.5 四处另写判据；`DecisionLedger` 默认开（bit-exact 观测）；`dmde_solver.py:244-246` 的 `except: pass` 改计数+日志；`_ORDER_WINNER_COUNTS`/`_WRITEBACK_STATS` 并入台账 | `constraints/`、`coverage_gate.py`、`decode_repair.py`、`dmde_solver.py`、新建 `decision_ledger.py` | `ledger_enabled=True`（纯观测） | **无** | 同一解四层判据逐位一致；`pytest 433 passed`；与 `method-phase1` 数值 **bit-exact**；台账 `reason` 字段覆盖率 100% |
| **P1 统一比较 + 剥解码期优化**<br>（关键闸门） | `CompareSpec`（ε-constrained 三元 + Lagaros 层级×数量，默认关）；`decode_mode=constructive` + `projection_only` 子模式；`_consolidate` 成本合并、`_best_order` NN/航程裁决移出每体路径；MCF 在层 1 降级为「最近合法巢」确定性投影 | `decode_repair.py`、`decode_constructive.py`、`nest_capacity.py`、`dmde_solver.py`、新建 `compare_spec.py` | `projection_only`、`compare_mode`、`epsilon_schedule`、`repair_replacement` | 隔离、可 A/B | **前置闸门**：step1 工具实测冻结代占比 ↓、精英总增益 ↑、碰撞率不升、完成率 100%；未达标**停止推进 P2**；`probe_selection_gap` 关总闸时 Spearman 仍须 1.0 |
| **P2 评价一致 + 软惩罚标定** | `w_late` 显式化；`λ_cov` 标定到 > 少做任务收益上界；`n_skip^eval/^final` 分列；ε(t) 调度与交付层构造性补全兜底；所有采纳判据统一为 f | `problem.py`、`coverage_gate.py`、`stage_runner.py`、`route_polish.py` | `lambda_coverage`、`eps_coverage` | 中等 | `probe_selection_gap`：**Spearman → 1、regret → 0**（开/关总闸两档）；不再有「f 变差却被采纳」；完成率恒 100%、无 `eliminating` |
| **P3 继承适应度 + 选择看见交付** | 重算 Lamarckian 回写后的 `re_encoded.fitness`；选择期对候选集做「先打磨后选」或 `f_final` 廉价下界代理 | `dmde_solver.py`、`evaluator.py` | `recompute_reencoded_fitness`、`select_after_polish` | **会改数值** | 复用 `probe_selection_gap.py` 与 step2 2×2；重跑全部基线；配对 t 检验如实声明（冒烟档不外推） |
| **P4 层 3 拆分 + 文献对标** | L3a/L3b 分账；`CompareSpec` 开关对照（普通惩罚 / ε-constrained / Lagaros 新变体）；统一「事后评价器」重评 phase1 与本版交付解 | `route_polish.py`、`local_search.py`、新实验目录 | `polish_relocate`（L3b 默认关） | 中等 | 完成「几何 vs 救回任务」收益拆分；主效应 + **交互项**并列报告（禁止写「独立收益」）；论文可复现表格 |

### 5.1 对照实验设计（贯穿 P1–P4）

- **基线**：`method-phase1` @ `46b9739`；同场景、同 seed、同几何实例、同搜索预算
  （pop×gens 固定；注意 `max_iter` 同时是退火调度分母，改预算即换轨迹）。
- **数值可比性**：`f` 公式保持不变（P0–P2 不动 `λ=0.1` / `W_hard` / `w_late`），
  若 P4 引入 `CompareSpec` 变体，**只比指标层**：完成率、按时率、航程、迟到、
  `n_hard`、墙钟、`n_evals`；用统一「事后评价器」重评两版交付解，不比 f 绝对值。
- **基线固化**：把 phase1 关键数值落成 `baseline_phase1.csv` 存进新实验目录
  （遵守实验独立性，禁 `_shared/`），避免运行时切分支。
- **验收工具复用**：`step1_genotype_phenotype.py`（精英/碰撞）、`step2_2x2.py`（交互）、
  `probe_selection_gap.py`（口径错配）、`verify_pop_sensitivity.py`（种子敏感性）。
- **纪律**：严禁多实验并行；跑时勿渲图；挂起加 `-u`；长跑勿 `| tail`；
  跨 seed 对比用 `seed_summary`/`per_seed`，顶层 `summary` 仅首 seed。

### 5.2 每阶段合并门槛

`pytest task_allocation/tests tests -q` 全绿（当前 433）→ exp01 审计 25/25 →
完成率 100% 与无 `eliminating` 断言 → 同 seed 配对数值留档 → 论文数字与留档一一对应
→ 单独提交（便于 bisect）。`method-phase1` 标签不可移动，任何阶段可整体回退。

---

## 6. 六个决策的现状（v1 遗留，已由定稿收口）

2026-11-06 用户已拍板最终方案（四边界 + 六要素），本表只留状态：

| # | 决策 | 状态 |
|---|---|---|
| 1 | 比较器 | **已收口**：`可行性优先的 DMDE 竞争` 为定案；默认 Deb 三规则，ε / Lagaros 变体作可选开关对照（定稿 §6 要素 2） |
| 2 | L3b 归属类精修默认值 | 仍待定：建议默认关、保留开关，先做收益拆分（定稿 §9 第 3 项） |
| 3 | 覆盖 ε 与 `n_skip^eval` | 仍待定：影响「完成率恒 100%」表述，须分列两个数字（定稿 §9 第 2 项） |
| 4 | Lagaros 变体落点 | **已收口并修正**：原文核实后不是「只改比较算子」，而是「只在**内部选择标量 `F_sel`** 引入，且区分 Eq. 9b 变换与 Eq. 13 比较两类」；问题目标 `f` 仍不动（定稿 §7 勘误 3） |
| 5 | Zhao Ming 2017 引用 | 仍待定：改「本阶段设计选择」，只作编码/算子母本 |
| 6 | BU 表述 | 仍待定：用「BU 思想类比的航程可行性投影」 |

⇒ 全部执行细节以 `stage2_final_scheme_2026-11-06.md` 为准；本文不再维护。

---

## 7. 诚实口径与本文不做的事

- 本文是**分析与规划**，未修改任何生产代码、未跑新实验、未改 `Chapter3_README.md`
  （§4.8/§4.9/§4.10 是 `method-phase1` 的冻结描述，重构未开始不应覆盖）。
- 文献核实状态：**Lagaros2023 全文已获取并逐条核对**（Zotero `storage/UEDCMGSJ`），
  本文 §1 的 5 处偏差与定稿 §7 勘误表一致；**Zhao Ming 2017 全文仍未获取**，
  相关引用口径待核。Gandomi & Deb (2020)、Rahimi et al. (2024)、
  Salcedo-Sanz (2009) 仍为二次引用，不臆造公式与 DOI。
- 「精英更新频率上升」「可行率与交付质量同时提升」「搜索空间更干净」三项均为
  **待验证假设**，不是本规划保证的结果；P1 设前置闸门正是为此。
- `S_final` 的重放口径仍受 §4.10 的下界限制（未覆盖 `polish_assignment` 每候选重放）；
  重构后须用新口径重测，不得沿用旧数字。
- 预算与场景：若沿用 pop8×gen8 冒烟档，结论不可外推正式档（pop50×gen200）；
  纯几何 SRP 实例结论不外推 DEM 有向代价与多周期滚动。
