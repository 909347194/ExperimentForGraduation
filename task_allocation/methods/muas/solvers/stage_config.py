# -*- coding: utf-8 -*-
"""stage_config.py — 第二阶段编排参数（``MUASStageConfig``）。

2026-10-27 自 ``muas_stage.py`` 拆出：本模块只定义**参数**（各开关的
设计理由、消融出处、默认口径都写在字段注释里），不含算法逻辑；
``__post_init__`` 只做取值合法性校验。模块常量 ``POLICY_*`` 是
``participating_policy`` 的取值域。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from task_allocation.methods.muas.solvers.local_search import (
    DEFAULT_MOVES,
    MOVE_POSITION,
    MOVE_RELOCATE,
    MOVE_SWAP,
)

# 参与 UAV 的选择策略
POLICY_CHEAPEST = "cheapest"     # 按「到最近任务的最小代价」升序取前 K 架
POLICY_FIRST = "first"           # 按登记顺序取前 K 架（便于复现）
POLICY_MARGINAL = "marginal"     # 边际出动决策：覆盖代价边际下降 ≤ 出动代价则跳过该机（非停机）
POLICY_DEMAND = "demand"         # 需求感知：贪心最大覆盖，保证每个选中任务都有可达参与机
POLICY_DEMAND_MARGINAL = "demand_marginal"  # 三层选机：设施选址预选 + 字典序边际精修（exp04 B2）


@dataclass


class MUASStageConfig:
    """第二阶段编排参数（全部可由实验 yaml 覆盖）。"""

    # 参与本周期规划的 UAV 数上限；None 表示自动（保证 K < 任务数以走 srp 模型）
    max_participating_uavs: int | None = None
    participating_policy: str = POLICY_CHEAPEST
    # 参与机数 K 与任务数解耦（修复「K 顶格 N-1 → N-K 恒小、巡游顺序维度塌缩」）：
    #   sortie_budget        本周期出动架次上限 S_t（可由滚动层按起降/充电能力推导）；
    #   participation_cost   每架固定出动代价 γ（起降/能耗/架次占用）；
    #   participating_policy="marginal" 时按「覆盖代价边际下降 ≤ γ 即停」内生定 K。
    # 三者都缺省时退化为旧规则（尽量多派，上界 N-1）。
    sortie_budget: int | None = None
    participation_cost: float = 0.0
    # marginal 估计器的单机服务容量：一架机一次出动最多串几个任务。
    #   None（默认）= 按航程自动推导 cap = max(1, ⌊min_u 剩余航程 / 平均单程代价⌋ − 1)
    #     （串 c 个任务的航程 ≈ (c+1)×平均单程，含返航；推导不出时退化为无容量）；
    #   显式整数 = 固定容量（实验/消融用）。
    # 为什么需要它：旧估计器 est(j)=Σ_t min_{u∈前j架} C(u,t) 把每架机当成能
    # 「同时」覆盖全部任务，完全不含航程/时间窗容量——gain 度量的是纯覆盖代价
    # 下降，与「多派一架的真实服务能力」脱节（γ 因此从不咬合）。加容量后未覆盖
    # 任务记往返惩罚，gain 才反映真实边际贡献。详见 _select_by_marginal_takeoff。
    marginal_tour_cap: int | None = None
    # 选择性：剔除「执行代价超过收益」的任务（y_j = 0，保留到下一周期）。
    # 注意：门禁口径（唯一主口径，2026-10-24）下「完成是约束」，经济性剔除
    # 与完成约束冲突（丢正收益任务即违反约束），已**永久关闭**——任务暂缓仅由
    # 可行性修复产生；原 legacy 加权和（lexicographic=False）口径已从代码移除
    # （见 docs/objective_caliber_two_approaches_archive_2026-10-24.md）。
    prune_unprofitable: bool = True
    prune_max_rounds: int = 8
    # 零奖励剔除（四层适应度配套）：对「ETA ≥ latest + W」的任务
    # （迟到折减 eff=R·max(0,1−L/W) 下 F1 贡献≈0）在 repair 链末轮暂缓
    # （y_j=0）。字典序下经济性剔除（prune_unprofitable）整体关闭——丢正收益
    # 任务必降 F1；而零奖励任务不降 F1，剔除它是严格的适应度改进（省 T2
    # 迟到惩罚），还把机会留给下一周期（可能按时交付）。仅当
    # problem.late_reward_decay_seconds>0 且时间窗约束启用时生效。
    #
    # **默认 false（消融定稿）**：剔除在搜索期对每个候选生效，会把「硬塞
    # 必然迟发任务」的候选救回可交付形态，搜索偏向「周期 1 多塞一个但迟到
    # 更深」的盆地；而迟到归零项已单独消灭零奖励交付，靶病不存在。
    # 开关保留供大规模压力场评估。
    prune_zero_reward: bool = False
    # ── 覆盖门禁（2026-10-22，合法解 = 物理硬约束 + 覆盖门禁）────────
    # True：build_solution 末态（最后一次 _repair_range 之后、_assemble
    # 之前）对 S 内未交付任务做一阶检验并**修到不动点**（能救必救：插入
    # 现有航次 / 空闲机开班），gate_stats 落 solution.meta——
    # voluntary_skip 由构造归零。False（默认）：不修复；仅当
    # problem.lexicographic_coverage=True 时做纯审计分账（n_skip 进
    # 适应度 W_skip 层），不改解。判定口径详见 coverage_gate.py。
    coverage_gate: bool = False
    # 修复不动点的防御性轮数上限（不挤出 ⇒ 通常一轮收敛）。
    coverage_gate_max_rounds: int = 8
    # ── 覆盖优先解码 + 单调成本 polish（2026-10-23 用户裁定）──────────
    # 解码/repair 两段式：**先**按覆盖目标装到约定口径下的最大可行集
    # （_load_coverage_fixed_point：一阶判据 = 航程 + 自身时间窗 + 开班
    # 巢容量；救援含插入现有航次 / 闲置机开班（参与集→全机队）/ 换机
    # 腾位；EDD 升序修到不动点），**再**在不减少已装集合的约束下做
    # 成本 polish（_polish_routes_monotone：跨机 relocate / 调序，四道
    # 门 = 航程 / 自身窗 / 迟到数不增 / 成本严格降）。
    # 动机：E1E2（2026-10-23）证实门禁单独上场时覆盖指标恒真而完成率
    # −5pp——「可装载」内生于当前解，成本-only 适应度把航程/窗口占满
    # ⇒ 剩余任务一阶判 shortfall ⇒ 淘汰。覆盖优先构造把「留装载
    # 余量」的激励放回解码本身；单调 polish 保证成本优化不侵蚀覆盖。
    # 门禁证明「能装却不装」可被抹成 0，但**不能单独承担完成语义**——
    # 完成语义由「覆盖优先装载 + 单调 polish」两段式承担，门禁退居
    # 事后校验。False（默认）= 现行修复链口径，逐位不变。
    coverage_first: bool = False
    # 覆盖装载不动点轮数上限（装载 → 重排 → 换巢 → 航程修复 = 一轮）。
    coverage_first_max_rounds: int = 4
    # 单调成本 polish 轮数上限（无改进即停）。
    monotone_polish_max_rounds: int = 30
    # 单调 polish 严格改进阈值（绝对；吸收浮点求和噪声）。
    monotone_polish_eps: float = 1e-6
    # 重排期的航程（成本）优化（2026-10-24 修）：``_best_order`` 原早退
    # 「原序零迟到 → 直接返回」把候选判据 ``(迟到数, 迟到量, 航程)`` 的
    # 最后一位整段作废——exp01 常态零迟到 ⇒ 航程从未进入比较，实测
    # polish 增益 100% 来自 F1（见 docs/fitness_path_optimization_
    # diagnosis_2026-10-24.md §3）。开启后：零迟到时仍评估「最近邻」这一
    # 唯一以航程为目标的候选，仅在「仍零迟到 且 航程严格更短」时采纳。
    # 语义 = 「覆盖优先、成本随后」：执行集与可行性均不变（F1 不动、
    # 不引入迟到），只压航程，因此**不触碰主适应度阶梯**，可与任意
    # 口径（含覆盖门禁）叠加。False = 修复前行为（早退全退）。
    #
    # ⚠️ 2026-10-24 A/B 实测**（默认关闭，勿擅自开启）**：
    #   单周期冒烟（pop20/gen20）航程 −16.7%（419→349 km）看似有效，但
    #   正式配置（pop50/gen100→现默认 gen200、3 seed）**净持平且总航程 +4.0%**：
    #     ① 主周期 km/任务 27.04 → 27.03（持平）——访问顺序本就是 DE 编码的
    #        一部分（cost_vector[K,N) 巡游段，见 test_tour_order_encoding），
    #        搜索充分时「最近邻」贪心覆盖 DE 搜出来的顺序，净收益为零；
    #     ② 非空周期 10 → 13（多 3 个只完成 1~2 个任务的尾巴周期，
    #        39~159 km/任务）⇒ 总航程被尾巴拉高，按时率 −2.4pp、
    #        正确执行率 −5.3pp、s_coverage 0.9958→0.9736。
    #   结论：**「最近邻」不是正确的航程优化器**。早退确实是真缺陷（航程
    #   从未进入比较），但正确解法是 2-opt/Or-opt 组内重排，且应作用于
    #   **交付解**而非解码期（避免干扰 DE 搜索）。见 docs/fitness_path_
    #   optimization_diagnosis_2026-10-24.md §9。
    reorder_range_optimize: bool = False
    # 采纳航程改进的严格阈值（米，绝对）。吸收浮点求和噪声，避免为
    # 1e-9 级的差异改序（改序本身有下游 ETA 重算成本）。
    reorder_range_eps: float = 1.0
    # ── 交付后成本 polish（2026-10-24 用户裁定，诊断报告 §9 排序 1+2）───
    # DMDE 搜索结束、交付解确定后，对**交付解本身**做组内 2-opt（段反转）
    # / Or-opt（段长 1~3 迁移）纯成本重排。与同文件其它两个"成本优化"的
    # 区别：
    #   · vs reorder_range_optimize（解码期最近邻，2026-10-24 A/B 证伪、
    #     默认关闭）：① 作用对象是**交付解**而非解码期——访问顺序本就是
    #     DE 编码的一部分（cost_vector[K,N) 巡游段，test_tour_order_
    #     encoding 证明被反映射真实消费），解码期强加贪心等于用贪心覆盖
    #     搜索结果（正式档实测净持平、总航程 +4.0%）；② 求解器是
    #     2-opt/Or-opt 而非最近邻构造——只接受严格改进，不可能更差；
    #     ③ 走廉价原语（_tour_cost / 单趟 ETA），不过完整 evaluate 链。
    #   · vs polish_assignment（local_search.py 的 relocate/swap/
    #     position，默认开启）：邻域互补——position 只移单任务，2-opt 的
    #     段反转 / Or-opt 的段迁移是 TSP 类问题的强邻域；且本 polish 在
    #     run_selective_muas 终态（polish/memetic/coverage_first 之后）
    #     统一执行，候选不进适应度缓存、不占 n_evals 口径。
    # 四道门（与 _polish_routes_monotone 同纪律）：
    #   1. 航程门：改序后全航程（起点→任务→终点巢）≤ 该机剩余航程；
    #   2. 迟到门：迟到任务数不增 **且** 迟到总量不增（逐分量，不制造
    #      新违约、不加深旧违约 ⇒ soft_violation 不增）；
    #   3. 执行集门：2-opt/Or-opt 是航次内置换 ⇒ 任务集由构造不变
    #      （run_selective_muas 采纳端再以 executed ⊇ 旧解复核）；
    #   4. 成本门：航程严格下降 > eps 才采纳（first-improvement，接受
    #      即重启扫描；无随机，逐位可复现）。
    # 语义 = 覆盖优先、成本随后：F1 不动、迟到不增、路径严格降 ⇒ 任意
    # λ>0 下适应度严格改进，**不触碰主适应度阶梯**，可与覆盖门禁 /
    # coverage_first 任意叠加。默认开启（生产口径）；消融对照显式 False。
    cost_polish_enabled: bool = True
    # 轮数上限（整轮无改进即停；first-improvement 下 n_rounds ≈ 接受数+1）。
    cost_polish_max_rounds: int = 30
    # 采纳阈值（米，绝对；吸收浮点求和噪声）。
    cost_polish_eps: float = 1.0
    # ── 跨机指派成本 polish（2026-10-24 用户裁定，组内重排之后的转向）───
    # 组内 2-opt/Or-opt 只改访问顺序；本开关打开**归属维度**：把单任务从
    # 供体机改派到受体机（含可用但空闲的机），四道门与 _cost_polish_routes
    # 同构（两侧航程门 / 两侧迟到门 / 执行集由构造保持 / 净航程严格下降）。
    # 背景：3 seed A/B 钉死组内维度已穷尽——63 个周期解仅 3 次接受、
    # 总增益 2262 m（占四 seed 总航程 0.014%），覆盖/按时逐位不变。
    # 若本维度同样零接受 ⇒ exp01 交付解的航程在「顺序 × 归属」两个后处理
    # 维度均已局部最优，残余差距只能来自搜索期目标权重（λ 弱杠杆），
    # 为「不再拧适应度」提供实证边界。默认开启（与 cost_polish_enabled
    # 同生产口径）；消融对照显式 False。
    cost_polish_relocate_enabled: bool = True
    # 剔除判据的裕度：仅当 λ·节省 > 收益 × (1 + margin) 时才剔除
    prune_margin: float = 0.0
    # 等待补偿（论文 §3.2 的 W_i(t)）：任务滞留越久越不容易被剔除，
    # 避免偏远任务连续多周期得不到执行而永久滞留
    prune_wait_bonus: float = 1.0
    wait_scale: float = 3600.0
    # 需求感知参与（policy="demand"）：贪心最大覆盖，使每个选中任务都
    # 有一架「可达」的参与机，而非 cheapest 那样把机堆在任务稠密区、饿死
    # 稀疏任务（北/西角离群任务无近参与机，后期 K 收缩时不可达）。
    # 覆盖判据：UAV u 覆盖任务 t 当且仅当 uav_target[row_u, t] <= 覆盖半径；
    #   覆盖半径 = ``demand_coverage_radius``（显式，米）否则
    #   ``demand_range_factor · remaining_range(u)``（默认 0.5，对应单任务
    #   往返航程 ≤ 剩余航程的保守可达性）。覆盖为零的机不纳入（避免塞无用机）。
    demand_coverage_radius: float | None = None
    demand_range_factor: float = 0.5
    # Fix⑤ 服务能力感知 provisioning（policy="demand" 生效）：最小距离覆盖只保证
    # 「每任务有可达参与机」，但一架机 80km 航程常不足以同时服务它覆盖的多个远任务
    # （覆盖≠能服务），于是远任务在有限航程预算下被留成 y_j=0。开启后在第一阶段
    # 最小覆盖基础上做容量感知补机：把「最近参与机往返代价 > 剩余航程×裕度」的过载
    # 任务逐个剥给最近的空闲机（拉入参与），直到无过载或达 K 上限。直接针对
    # 「K 塌缩→远任务未服务」这一 exp01 残余瓶颈。
    demand_provision_serving: bool = True
    demand_serve_margin: float = 0.95  # 服务可行性裕度（与 selection.feasibility.range_margin 对齐）
    # Fix⑥「可达即执行」的救援池口径。方法论：任务只要能力可达、不违反约束就
    # 应最终被执行，成本最小化是此前提下的次级目标（字典序两阶段）。而「可达」
    # 是**存在量化**（∃ 机队中某方案可服务），不是「给定方案内塞得进」。
    # 此前 _reflow_task 只在 participating 内找闲置机改派，但 demand 参与策略
    # 按最小距离覆盖把 K 压到 2~5 架，且这些机实际要服务多个任务（航程被摊薄），
    # 于是物理可达的任务因「参与集内无闲置机可改派」被判 y_j=0——假暂缓。
    # 例：exp01 的 T10，非参与闲置机 UAV1/6/8 单任务航次 67.3/71.0/76.1km
    # 均 ≤ 80km 航程，却因不在参与集而拿不到救援。
    #   "fleet"（默认）：救援池扩到全可用机队，落地存在量化；
    #   "participating"：历史口径，仅在参与集内改派（消融对照）。
    reflow_rescue_scope: str = "fleet"
    # 解码模式（exp04 B1 对照臂）：
    #   "repair_chain"（默认，生产口径）：现状反应式修复链——
    #     _consolidate → _prune → _repair_range → _choose_end_nests（≤6 轮
    #     不动点）→ _complete_coverage → _reorder_for_deadlines →
    #     _prune_zero_reward → _repair_range → _choose_end_nests →
    #     _repair_range。exp01/exp02 既有结果 bit-exact 不变。
    #   "constructive"：构造式解码（_build_constructive）——把「救援」前置为
    #     主解码：逐任务按基因组顺序做可行性内联插入（优先 DMDE 指派机 →
    #     其它参与机 → 全机队闲置机开单 = 第3层安全网），不可行才 y_j=0；
    #     终点机巢/时间窗为耦合子问题，保留 ≤3 轮小迭代（不承诺单趟）。
    #   三条红线（exp04 notes B1）：cost_vector/assignment 结构必须引导插入
    #   （不得退化成 cost 排序键贪心，否则 DMDE 退化为排列编码 GA）；巢/
    #   时间窗留小迭代；默认关、gated A/B。两种模式共用同一批 helper
    #   （_tour_cost/_range_limit/_window_ok/_choose_end_nests/
    #   _reorder_for_deadlines/_assemble），口径一致。
    decode_mode: str = "repair_chain"
    # 终点机巢决策的求解方式：master 分支恒为方案 A（分层求解）——
    #   DMDE 只搜任务分配与访问顺序，终点机巢由内层子问题
    #   solve_end_nest_assignment 精确求解（容量约束下最优），返航代价经
    #   完整成本评价间接影响进化搜索。
    #   「终点机巢进编码」的联合变体（方案 B，编码优先、精确分配兜底）已从
    #   master 移除，在 pro 分支实现。
    # Lamarckian 回写：把修复后的解（经 _prune / _repair_range /
    # _choose_end_nests）的「起始任务归属」编码回获胜个体的基因，使后继代际的
    # 差分变异围绕**修复后的可行子空间**展开（默认开启）。
    # 关闭后退化为当前行为：进化搜索的是未修复空间。
    lamarckian: bool = True
    # 事件驱动重规划：被标记不可用的机巢（论文 §6 的 NEST_UNAVAILABLE 事件）
    # 一律不可选为终点机巢 z_ub，其归属 UAV 强制改降其它巢（多机巢异巢终止的
    # 容错分支）。由滚动层每周期从事件上下文注入。
    excluded_nest_ids: set[int] = field(default_factory=set)
    # 当前时刻，用于算等待补偿；由滚动层每周期传入
    current_time: float = 0.0
    # 适应度缓存（DMDE 会重复评估同一个体）
    use_cache: bool = True
    # 解码后局部搜索 polish：DMDE 收敛后对 best_assignment
    # 做 relocate / swap / 插入位局部搜索，候选一律过 evaluator.evaluate
    # （与 DMDE 个体同一 build_solution 修复链），仅严格改进才接受。
    # 治「收敛曲线早期躺平」的两个根因：① big-M 阶梯适应度下差分算子对
    # 换机/换序级改善不可见（路径级优化空间远小于单任务收益量级）；
    # ② Lamarckian 回写只写起始基因通道致归属冻结。局部搜索
    # 直接在解空间操作，绕过编码—回写冻结构悟。
    # 默认开启（生产口径）；消融对照显式传 False。
    polish_enabled: bool = True
    # 启用的移动类型（顺序即扫描优先级；relocate 结构性收益最大排首）
    polish_moves: tuple[str, ...] = DEFAULT_MOVES
    polish_max_rounds: int = 30     # 无改进即停的轮数上限
    polish_max_evals: int = 2000    # 真实评估数预算（缓存命中不计）
    polish_eps: float = 1e-6        # 严格改进阈值（绝对；吸收浮点求和噪声）
    # 搜索期内精英档案（memetic M1，2026-10-19）：开启后每 memetic_K 代在
    # DE 进化循环内对当前精英跑一次 polish_assignment，维护「best polished」
    # 档案（assignment/fitness/solution）。M1 **不改基因型**（无反映射），
    # DE 群体照常探索结构；档案使命中路由在**搜索期内**即被持续压低，交付解
    # 取「收敛后 polish」与「档案最优」的更优者——把 DMDE 早平后留给收敛再做的
    # 路由改进前移进搜索期。默认关（exp01 非 memetic 基线不动）；exp01 memetic
    # 变体显式开。M2（Lamarckian 反射写回，让路由改进回流群体）见报告展望。
    polish_in_loop: bool = False
    memetic_K: int = 20             # 每 K 代触发一次精英 polish（>=1）
    # 搜索期内精英 polish 的**从紧预算**：强化只需 incremental 改进、不必每步
    # 收敛到局部最优（first-improvement 本就早停），故轮数/评估数远小于收敛后
    # 的终打磨。控住单周期耗时（每 K 代一次、每次封顶，避免 10× 全量 polish）。
    memetic_max_rounds: int = 6
    memetic_max_evals: int = 500

    def __post_init__(self) -> None:
        if self.max_participating_uavs is not None and self.max_participating_uavs < 1:
            raise ValueError("max_participating_uavs 必须 >= 1 或为 None")
        if self.marginal_tour_cap is not None and self.marginal_tour_cap < 1:
            raise ValueError("marginal_tour_cap 必须 >= 1 或为 None（None=按航程自动推导）")
        if self.participating_policy not in (
            POLICY_CHEAPEST, POLICY_FIRST, POLICY_MARGINAL, POLICY_DEMAND,
            POLICY_DEMAND_MARGINAL
        ):
            raise ValueError(f"未知的参与 UAV 选择策略：{self.participating_policy}")
        if self.decode_mode not in ("repair_chain", "constructive"):
            raise ValueError(
                'decode_mode 必须是 "repair_chain" 或 "constructive"，'
                f"当前为 {self.decode_mode!r}"
            )
        if str(self.reflow_rescue_scope).lower() not in ("fleet", "participating"):
            raise ValueError(
                'reflow_rescue_scope 必须是 "fleet" 或 "participating"，'
                f"当前为 {self.reflow_rescue_scope!r}"
            )
        if self.coverage_first_max_rounds < 1:
            raise ValueError("coverage_first_max_rounds 必须 >= 1")
        if self.monotone_polish_max_rounds < 1:
            raise ValueError("monotone_polish_max_rounds 必须 >= 1")
        bad = [m for m in self.polish_moves
               if m not in (MOVE_RELOCATE, MOVE_SWAP, MOVE_POSITION)]
        if bad:
            raise ValueError(
                "polish_moves 只能含 relocate/swap/position，"
                f"当前含未知项 {bad!r}"
            )
        if self.polish_max_rounds < 1:
            raise ValueError("polish_max_rounds 必须 >= 1")
        if self.polish_max_evals < 1:
            raise ValueError("polish_max_evals 必须 >= 1")
        if self.polish_eps < 0:
            raise ValueError("polish_eps 不能为负")
        if self.memetic_K < 1:
            raise ValueError("memetic_K 必须 >= 1")
        if self.memetic_max_rounds < 1:
            raise ValueError("memetic_max_rounds 必须 >= 1")
        if self.memetic_max_evals < 1:
            raise ValueError("memetic_max_evals 必须 >= 1")
        if self.cost_polish_max_rounds < 1:
            raise ValueError("cost_polish_max_rounds 必须 >= 1")
        if self.cost_polish_eps < 0:
            raise ValueError("cost_polish_eps 不能为负")

