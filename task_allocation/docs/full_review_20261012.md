# 全面审查报告（2026-10-12）

> 审查范围：`task_allocation` 全模块（约 18,600 行 Python）+ 2 个实验 + 15 个测试文件 + 论文/文档口径。
> 审查方式：**以代码为唯一事实源**，逐模块阅读，对照论文/文档宣称，交叉验证实验数据。
> 与 `ch3_code_review_20261011.md`（第 3 章代码审查）的关系：本报告为**全项目**审查，
> 该报告的 P1/P2/P3 条目状态在 §六更新；不重复其已记录内容，只补充新发现。

---

## 〇、审查维度与结论速览

| 维度 | 判定 | 说明 |
|---|---|---|
| 方法（问题定义/模型/策略） | ✅ 成立 | 四扩展均有代码落点；两阶段分层、选择性定位清晰 |
| 逻辑链（阶段接口/滚动闭环） | ✅ 基本成立 | 集合关系、不动点迭代、事件链路自洽；4 处口径分裂见 P1/P2 |
| 代码实现（算法/约束/健壮性） | ✅ 质量高 | 无 P0；算子与 Ming 2017 逐条一致且有测试守护 |
| 实验有效性 | ⚠️ 有硬缺口 | exp02 反证「压缩不伤质量」；航程/容量约束惰性（P1） |
| 测试覆盖 | ✅ 良好 | 137 passed；SRP 不变式、MCF 最优性、滚动不变量均有守护 |

**未发现 P0（阻断性）问题。** 全量测试 137 passed（本轮实测）。

---

## 一、方法层面审查

### 1.1 问题定义（✅ 成立）

四个扩展均落到代码：

| 宣称 | 代码事实 | 判定 |
|---|---|---|
| 任务动态到达/滞留/释放 | `task_pool.begin_cycle`：SELECTED→PENDING、来源三分类（REMAIN/NEW/RELEASE）、过期出池、冻结 T_t | ✅ |
| 仅可用 UAV 参与、可闲置 | `uav_state.available()` + `problem.from_entities` 双重过滤；空航次允许 | ✅ |
| 多机巢、终点机巢是决策 | `nest_capacity` 末态口径 + `solve_end_nest_assignment`（MCF 精确解） | ✅ |
| 三维地理代价 | `vertical_section` 地形跟随航迹长度（米量纲），DEM 贯通 | ✅ |
| 滚动时域 + 事件触发 | `horizon.run_cycle` 十步链路 + `event_trigger` 三类事件 | ✅ |

### 1.2 模型定位（✅ 成立，一处需论文显式声明）

- 选择性模型实为 **Orienteering/Prize-Collecting**（`λ·saving > R_eff·(1+margin)` 判据），
  与「覆盖优先」定位的张力已由 λ_crit ≈ 1.4e-5 推导量化（记忆 2026-10-12）。
- **`recharge_at_nest=True` 每周期末满充**（`horizon._advance_execution:614`）：
  代码行为符合 docstring 声明（「降落后在机巢换电」），但论文须显式写出该假设，
  否则「航程约束」在滚动中结构性失效（见 P1-2）。

---

## 二、逻辑链条审查

### 2.1 已验证自洽的链路（✅）

1. **两阶段接口**：`pipeline.run` 产出 T_sel（含 filter_reasons 全量诊断）→
   `_subset_cost_build` 裁剪（O(n²) 只算一次）→ `run_selective_muas`。口径一致。
2. **修复不动点**：`build_solution` 的 `prune → repair_range → choose_end_nests`
   迭代至状态不变（≤6 轮），顺序论证正确（先 prune 后选巢，避免「虚让名额」超卖）。
3. **容量口径四处统一**：`check_nest_capacity` / `choose_end_nests` /
   `solve_end_nest_assignment` / `fleet.apply_solution` 共用「末态归属」
   （`occupied_after = now − departures + arrivals`），无第二份状态。
4. **事件链路**：故障注入时机（解回写后、完成反馈前）论证正确；
   完成闸门把违约任务解绑回池而非顺延完成，归因标签三级映射（任务/UAV/巢级）合理。
5. **集合关系**：T_exec ⊆ T_sel ⊆ T_t、U_exec ⊆ U_avail 由
   `tests/test_rolling_invariants.py` 每周期守护。

### 2.2 口径分裂（⚠️ 见问题清单 P1-1、P2-6/7）

- ETA 计算：预筛端用三维 travel（T2 修复后），评价端（时间窗惩罚、F3 瓶颈时间）用欧氏。
- 预算记账：`capability.fleet_range_budget` 已同步 sortie_budget/max_participating，
  `feasibility.postcheck` 未同步。

---

## 三、代码实现审查（分级问题清单）

分级：P1 影响论文结论有效性或真实功能缺陷；P2 场景相关缺陷/口径不一致/健壮性；
P3 代码异味/文档漂移/冗余。

### P1（4 项）

**P1-1｜ETA 口径分裂：评价端用欧氏、预筛端用三维**
- 位置：`constraints/coop_constraints.py:103`（`math.dist`）、`problem.py:390`（`_euclid`）；
  对照 `selection/feasibility.py:73-81`（cost_hint 三维优先）。
- 事实：三维航迹长度 ≥ 欧氏直线（地形跟随），评价端 ETA 系统性**低估**飞行时间 →
  时间窗惩罚偏小、F3 失真；`test_feasibility_travel_cost.py` 只锁定预筛侧。
- 影响：论文若主张「三维环境影响时间窗判定」，此处是反例；山区场景惩罚偏乐观。
- 建议：把 `CostProvider`/`cost_hint` 传入 `check_time_window_constraint` 与
  `tour_time`，ETA 统一用三维段长（与 T2 修复同方向）。

**P1-2｜滚动中航程约束结构性失效（实验设计问题，非代码 bug）**
- 位置：`horizon.py:614-615`（周期末满充）+ exp01 `uav_max_range=120000`。
- 事实：每周满充 + 单周期最长航次 ~35.8 km（实测）→ 航程违约恒 0、`_repair_range`
  从不触发、`range` 约束在整个实验中是死开关。
- 影响：论文宣称的「航程约束」无任何实验证据；审稿人可据此质疑约束体系真实性。
- 建议：论文显式声明「周期末满充」假设；或做一组跨周期不充满 / 缩小 max_range
  的消融，让 range 真正 binding。

**P1-3｜prefilter 用「最近 UAV」的剩余航程做航程检查（误杀逻辑）**
- 位置：`selection/feasibility.py:71-79`：`u, dist = _nearest_avail_uav(...)` 后
  `travel > range_margin × u.remaining_range` 判淘汰。
- 事实：最近的 UAV 可能航程将尽而稍远的 UAV 航程充足 → 任务被误杀
  （正确做法：在「航程够用」的 UAV 中取最近，或逐架判定）。
- 影响：当前场景航程余量 5 倍，不触发；任务密度/航程紧张时是真实缺陷。
- 建议：改为 `min over u of (travel_u / remaining_u)` 口径或两段式判定。

**P1-4｜exp02 反证「压缩不伤质量」（方法论缺口，非代码 bug）**
- 事实：`|T|≫K`（8 机）下分层 F1≈130/134/146 vs 全量对照 F1=314，收益损失 ~58%；
  `Top-αK = ⌈2.5×8⌉ = 20` 按**机数**线性标定，与巡游模型吞吐能力不匹配。
- 影响：第 3 章「第一阶段压缩不伤质量」的立论前提不成立。
- 建议：α 改按机队总吞吐标定（如 `B(t)/典型航次代价`），并在 exp02 报告中并列
  分层/全量的目标值差距列。

### P2（7 项）

**P2-5｜`_prune` 每轮只剔一个任务，且被 `prune_max_rounds=8` 封顶**
- 位置：`muas_stage.py:486-505`（内层 break + 外层 for 最多 8 轮）。
- 事实：M=16 且有 9 个任务不划算时，最多只剔 8 个，残留不划算任务进入执行。
- 影响：大 M（|T|≫K）场景剔除不彻底；exp01（M≤10）未暴露。
- 建议：每轮扫完全部再break，或把轮数上限与任务数解耦（如 `max(8, M)`）。

**P2-6｜postcheck 预算未同步 sortie_budget / max_participating_uavs**
- 位置：`feasibility.py:103`（全 avail 求和）；对照 `capability.fleet_range_budget`
  的 k_op 截断（滚动层已把二阶段旋钮 merge 进 capability，未进 feasibility）。
- 影响：打开②③吞吐上限时，postcheck 的预算对着「不会兑现的幻影容量」校准。
  当前无害（预筛恒先生效、postcheck 退为冗余闸门），口径不一致留存。
- 建议：`FeasibilityConfig` 增加同旋钮，或由 horizon 注入合并后的 budget。

**P2-7｜forced_task_ids 绕过 cap(t) 与 α 名额**
- 位置：`pipeline.py:116-124`（强制纳入不经规模上界）。
- 影响：「规模跟能力走」叙事在事件场景有洞；T_sel 可超过 cap(t)。
  文档已声明，论文须注明（事件任务豁免是特性，不是缺陷）。
- 建议：论文/文档显式写「紧急与故障释放任务豁免规模上界」。

**P2-8｜cost_matrix 的 `task_weight_from_reward` 语义不对称**
- 位置：`cost/cost_matrix.py:81`（Task→Task 用**目标** reward 加权）vs `:96`
  （Task→Nest 恒 weight=1）。
- 影响：开启后「代价」概念被收益污染、行列语义不一致；默认关闭故当前无害。
- 建议：默认关闭保留；若启用需统一加权口径并加测试。

**P2-9｜`encoder.generate(seed)` 污染全局 RNG**
- 位置：`encoder.py:197-199`（`random.seed` + `np.random.seed`）；
  `extinction._random_cost_vector:154`（`generate(1)` 无 seed，依赖全局状态）。
- 影响：同进程多 seed 顺序实验时，全局 RNG 被重置/依赖，影响其他全局消费者；
  当前复现性保持（seed 固定），但脆弱。
- 建议：`generate` 改用局部 `random.Random(seed)`；`_random_cost_vector`
  传入 rng。

**P2-10｜`hybrid_differential_population` 在 pop_size ≤ 4 时崩溃**
- 位置：`crossover.py:76`（`rng.choice(pop_size, size=4, replace=False)`）。
- 影响：配置 pop_size<5 直接 ValueError，无防护无测试。
- 建议：加 `pop_size >= 5` 校验或退化路径。

**P2-11｜executed_ids 与 released_ids 交叠（口径风险）**
- 位置：`horizon.py:453-455`（executed 先记）→ `_inject_faults`（同任务再记 released）。
- 事实：故障任务同时出现在两个列表；完成率若按 executed 计会虚高
  （当前指标按 completed 计，安全）。
- 建议：论文取数口径固定为 completed/arrived；文档注明 executed 的语义是
  「本周期分配执行」，含执行后故障释放。

### P3（11 项，代码异味/文档漂移/冗余）

| # | 位置 | 问题 |
|---|---|---|
| P3-12 | exp01 `config.yaml` constraints 段 | `time_window: true` 但注释写「本模型暂不纳入（要纳入改 true）」——注释与值矛盾，会误导复现者 |
| P3-13 | exp02 `run.py`/metrics | `muas.model_type` 记录为 null |
| P3-14 | `operators/scale_factor.py:16-17` | docstring 称「GMR 实现上额外引入阈值 delta」，实际 `delta` 默认 None（原式），文档漂移 |
| P3-15 | `nest_capacity.py:176` | 死条件：`arr > 0 or after > 0 and now > 0` 的 OR 分支永不被利用（violations/penalty 都只在 arr>0 时加），误导性表达式 |
| P3-16 | `repair_rules/terminal_nest.py:184` | `repair_terminal_genes` 无调用点（死代码；ch3_review P3-3 已记录） |
| P3-17 | `inverse_mapper._pick_nest_by_cost:580` | 列下标 clamp 在欠尺寸矩阵下把多巢折叠到同一列（方案 B 边界；P3-3 已记录） |
| P3-18 | `marginal_gain.py:101` + `pipeline.py:177-185` | `capability_cap` 参数在 limit_override 路径为死参数；回写循环是自我赋值空操作 |
| P3-19 | `marginal_gain.py:68` | `_delta_benefit` 协同加成 0.2 硬编码（非常量） |
| P3-20 | `muas_stage.lamarckian_reencode` | 对 y_j=0 无效：被剔任务的巡游基因下轮重现再剔（有意的保守设计，但有重复剔除的算力开销） |
| P3-21 | `dmde_solver.py:225` | 回写后个体沿用旧 fitness 未重估，best_fitness 与 final 重建解可能微小不一致 |
| P3-22 | `event_trigger.summarize_events` | `ctx.faulted_uav_ids` 收集后未使用（horizon 用 `_prior_fault_released`，功能等价） |

---

## 四、实验与测试审查

### 4.1 实验（⚠️）

- **exp01**（10 机/~10 任务，dem_extent 全域撒点）：机多任务少，`cap(t)`、`postcheck`、
  Top-αK 压缩均为死代码（记忆实测 0/15 生效）；航程违约恒 0；地形只当代价、
  从未产生不可达判定（`end_nest_reachability` 启用但 filter_reasons 无一例）。
  **与论文核心假设 |T|≫K 相反**，验证场应在 exp02。
- **exp02**（8 机，50/100/200 任务，nest_anchored）：唯一真正进入 |T|≫K 区间的实验，
  但结论对分层不利（P1-4）；O(n²) 代价矩阵确认是端到端瓶颈（N=200 占 85.6%）。
- **baselines 全占位**：无「优于谁」的证据（ch3_review 已记录，仍在）。

### 4.2 测试（✅ 良好，4 个建议补充点）

现有 137 用例的正确守护：算子公式 fidelity（3-9/3-10/3-11/3-12）、SRP 不变式
（K≤M-1、每任务恰一次、长度恒定）、MCF 最优性 vs 暴力、方案 A 默认值、
滚动不变量（I1-I7 每周期）、完成闸门归因、事件重规划、ETA 用 cost_hint（预筛侧）。

建议补充（对应本轮发现）：
1. 评价端 ETA 与预筛端同口径的回归测试（P1-1）；
2. prefilter「航程够用的最近 UAV」用例（P1-3）；
3. `_prune` 在 M>8 且多数不划算时的彻底性用例（P2-5）；
4. `hybrid_differential_population` pop_size=4 的边界用例（P2-10）。

---

## 五、已验证正确的关键主张（正面结论）

1. 四个 DE 算子与 Ming 2017 公式**逐条一致**（CR/F/混合差分/GMR），
   `test_operators_fidelity.py` 守护；`delta` 默认 None 保持原式。
2. SRP 编码契约恒成立：`select_participating_uavs` 保 `K ≤ M-1`
   （balanced/overloaded 在本问题不可达），基因总数恒定，Lamarckian 回写
   只改 start/terminal 两通道、绝不增删基因。
3. 最小费用流实现正确：网络结构（源→航次→巢→汇）、容量软化（溢出边）、
   满流读出逻辑均经 `test_end_nest_assignment.py` 暴力对照验证。
4. 机巢容量「末态归属」口径在判定/贪心/精确求解/落地回写四处一致，
   `test_nest_capacity.py` 守护。
5. 修复不动点顺序论证正确（先 prune 后选巢，避免虚让名额超卖）。
6. 两阶段集合关系与滚动状态机（remain/new/release 三来源划分、故障释放回归、
   过期出池）由 `test_rolling_invariants.py` 每周期守护。
7. 方案 A/B 开关的事实状态与文档口径已统一（2026-10-12 更新，见
   `docs/guides/1整体方法.md` §5(1)(2)、`TODO.md` §0/§2.6）。

---

## 六、与 ch3_code_review_20261011.md 的条目对照

| 原条目 | 状态（本轮复核） |
|---|---|
| P2-3 终点兜底「一架冲突→全体改派」粒度过粗 | 仍在；已明确留 pro 分支（用户 2026-10-12 决策） |
| P3-3 死代码（repair_terminal_genes / clamp 折叠） | 仍在（本报告 P3-16/17） |
| P3-4 方案 B 仅 SRP 路径有终点编码 | 仍在；已写入 pro 分支待办 |
| P3-1 §5(3) y_j 公式与代码不一致（缺 λ 与 margin） | 仍在（文档公式待补） |
| P3-2 §3.4 插入式估计偏简（未写「对全部插入位置取最小」） | 仍在（文档公式待补） |
| P2-2 事件响应是整周期重规划 | 仍在；论文导言已删「局部」表述则闭环 |
| P2-4 两处成本口径不一致（空航次） | 仍在（当前调用路径不触发） |
| P2-5 内层溢出罚与适应度权重耦合 | 仍在（默认权重下一致） |

---

## 七、优先行动建议（按对论文结论的影响排序）

1. **修 α 标定口径**（P1-4）：按机队总吞吐而非机数；exp02 报告并列分层/全量目标差。
   这是第 3 章立论的前提。
2. **统一 ETA 三维口径**（P1-1）：评价端接三维 cost_hint，补回归测试。
3. **航程约束证据**（P1-2）：论文声明满充假设，或做跨周期不充满消融。
4. **落地 baselines**（greedy/random/小规模精确上界），否则「优于谁」无法量化。
5. **修 prefilter 误杀逻辑**（P1-3）+ **prune 封顶**（P2-5）：为大 M 场景做准备。
6. **清理 P3 文档漂移**（P3-12 注释矛盾、P3-14 docstring）：低成本高可信收益。

---

*审查方法说明：本报告全部结论来自代码阅读与实验数据核对（测试套件本轮实测
137 passed）；凡与记忆/文档冲突之处，以代码与实测数据为准。*

---

## 状态更新（2026-10-12 晚，本报告产出后落地的修复）

| 条目 | 状态 | 落点 |
|---|---|---|
| P1-1 ETA/航程判据两套几何 | ✅ 已修 | `bb9f790`：prefilter 航程与时间窗统一用三维 cost_hint 行进代价（欧氏回退），+5 回归测试 |
| P1-4 α 标定按机数而非吞吐 | ✅ 已修 | `0382747` + `ba707bd` + capability 演进：cap(t)=⌊B(t)/c̃⌋ 能力预算上界（B 只计可起飞子集，②③吞吐上限自动同步），`min{名额, cap}`（intersect）/ 能力为主（primary）双模式 |
| MCF 容量软约束可被代价差击穿（新发现） | ✅ 已修 | `f1beaa7`：溢出边结构性保底——存在容量可行解必不溢出，"容量可行最优"结构性成立 + 对抗性契约测试 |
| 双目标单标量语义（新落地） | ✅ 已定 | `99162fd`：字典序 big-M（收益优先、成本次之），DMDE 单适应度/选择机制不动；`lexicographic=False` 退历史加权和供消融 |
| 闲置/暂缓表达（新落地） | ✅ 已补 | `99162fd`：解码端合并修复（任务少时并入更少机、只改派不删任务）；任务暂缓仅由可行性修复产生；机巢不可用全量改降 |
| P1-3 prefilter 误杀 / P1-2 航程证据 / P2-5 prune 封顶 / P3 清单 / baselines | ⏳ 仍开放 | 见原文。注：字典序下经济性剔除已自动关闭，P2-5 的适用范围收窄至 `lexicographic=False` 消融口径 |
| 退出建议 #4（baselines 落地）/ #6（P3 文档漂移清理） | ⏳ 仍开放 | — |

文档同步：`Chapter3_README.md`（3.3 双重上界公式 / 3.5 口径与乐观代理声明 / 4.3 字典序 / 5(2) 结构性保底 / 5(3) 选择性语义 / 6 完成闸门·改降·能力记账）、`guides/1整体方法.md`、`TODO.md`、根 `README.md` 同步更新。
