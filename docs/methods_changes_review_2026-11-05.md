# methods 近期改动审查（2026-11-05）

**范围**：`task_allocation/methods/` 最近 5 次提交的改动：

| 提交 | 日期 | 主题 | 触及 |
|---|---|---|---|
| `fd52ba3` | 11-03 | 事件机制假开关/电平触发修复（F1-F6） | rolling/event_trigger.py、rolling/horizon.py |
| `33255e9` | 11-04 | 异构 per-UAV 化 + 服务休眠态 DORMANT | rolling/horizon.py、selection/feasibility.py、selection/pipeline.py、task_pool.py |
| `a8bad17` | 11-04 | prefilter cost_hint 口径修复 | selection/feasibility.py |
| `23e387c` | 11-05 | 池空收敛判据 + 顺序组原子性 | rolling/horizon.py、rolling/sequence_group.py（新）、selection/feasibility.py、muas/constraints/coop_constraints.py |
| `51ee4c6` | 11-05 | 吞吐分母统一（运营周期判据） | rolling/horizon.py（纯函数） |

**方法**：逐提交读 diff + 当前态交叉验证 + 测试面核查（全量回归 370 过，
6 挂均为 test_task_region 缺 DEM/geojson 数据文件，预存在）。只审查不修改。

---

## 结论速览

改动质量整体良好：判据单源 + 一致性测试、无组旁路零行为变化、先算后改的
组原子性收口都是好模式。**无阻断级缺陷**。发现 3 项中级（均为口径文档与
实现分裂类——本仓库视为一等公民的问题）+ 4 项低级。

---

## Medium

### M1. 「兜底防时变能力永不恢复空转」的自述目的与实现矛盾

- **位置**：`methods/rolling/horizon.py` `_converged()`（「池里还有休眠任务 =
  正在等条件恢复，忠实『一直下去』，禁用兜底」分支）；`HorizonConfig.stop_when_stable`
  注释；`exp01_smoke/PIPELINE.md` 收敛早停段。
- **描述**：注释称 `stable_patience` 兜底「防止时变能力永不恢复时空转到
  n_cycles 硬上限」。但兜底的触发条件之一是「**池内无休眠任务**」，而
  「时变能力永不恢复」恰恰表现为任务**永久休眠**（唤醒判据永不满足）——
  该场景下兜底永远不触发，最终仍空转到 `n_cycles` 硬上限。注释在同一句里
  自相矛盾。
- **行为溯源**：该行为被 `test_converged_dormant_never_early_stops` 刻意
  钉住（休眠 = 「正在等」≠「零进展」，是 2026-11-04 废弃旧稳态口径的初衷）。
  问题在**文档表述**而非逻辑：兜底实际覆盖的是「无休眠的僵死池」（如能力
  短缺任务反复装不下），不是「时变能力永不恢复」。
- **影响**：审稿人/协作读者按注释理解停机语义会得到错误预期；两处注释
  （HorizonConfig / PIPELINE.md）同病。
- **建议**：二选一——(a) 文档改为如实声明：「休眠在池时忠实一直下去、以
  n_cycles 硬上限止损（用户裁定优先）；兜底只覆盖无休眠任务的零进展池」；
  (b) 若确要兜底覆盖永久休眠，按 progressed 信号扩展（连续 patience 周期
  无完成/唤醒/休眠变化即停），与「条件恢复中」区分。

### M2. exp05 的收敛早停注释停留在旧口径（与 exp01 分裂）

- **位置**：`experiments/exp05_baselines/config.yaml` ~L415-419（`stop_when_stable`
  注释）。
- **描述**：仍写着旧「监测 len(pool)，连续 stable_patience 周期不变 ⇒ 稳态、
  剩余为物理不可达/**淘汰**的死任务」。`23e387c` 只更新了 exp01 的
  config.yaml/PIPELINE.md，exp05 漏改：(1) 停机判据已是「池空 + 零进展兜底」；
  (2) 「淘汰」是 2026-10-24 已废概念（无超期淘汰）。
- **影响**：exp05 是论文基线对比实验台；按此注释引用停机口径会与实际
  （含休眠任务时永不早停）不符，实验复现描述出错。
- **建议**：照 exp01 config.yaml 同步措辞（池空自然收敛 / no_progress 兜底 /
  n_cycles 硬上限三分）。

### M3. `_serviceable`（休眠/唤醒判据）与预筛拒绝判据不同尺，docstring 称「同源同尺」不准确

- **位置**：`rolling/horizon.py` `_serviceable()` / `_update_service_stall()`
  docstring vs `selection/feasibility.py` `prefilter()` 的 `served` 判据。
- **描述**：
  - 休眠/唤醒：`2·c(t,u) ≤ u.full_charge_range(...)` —— **满电航程、无 margin**；
  - 预筛拒绝（`no_uav_in_range`）：`2·c(t,u) > range_margin · remaining_range(u)`。
  两者只在「range_margin=1.0 且可用机剩余航程=满电」时等价。`_update_service_stall`
  docstring 声称「与预筛 no_uav_in_range 同源同尺」不成立。
- **现实取值**：exp01/exp05 均 `range_margin: 1.0` + `recharge_at_nest: true`
  （换电回满）⇒ 当前生产配置下两判据等价，**不触发**。
- **影响**：一旦关掉换电回满或调小 range_margin，缝隙即现形：任务被判
  「可服务」而唤醒/清 stall，却每周期被预筛拒 → 停在 PENDING 既不完成也
  不回休眠（churn——恰是休眠机制要消灭的现象），最终以 no_progress 停机。
  指标可解释（remain 入分母），但「可服务」承诺与可选性脱节。
- **建议**：`_serviceable` 改用与预筛同参数（`range_margin·remaining_range`），
  或 docstring 如实写「满电能力判据，与预筛剩余航程判据在换电回满 +
  margin=1.0 下等价」。

---

## Low

### L1. 准入门禁两处注释对 deferred_ids 的说法互相矛盾

- **位置**：`rolling/horizon.py` run_cycle 步骤 6.2 注释（「使步骤 8 的
  deferred_ids 记账天然包含被暂缓的组」）vs 紧随其后的落盘注释
  （「它们不在 selected_ids / deferred_ids 的求解口径里」）。
- **实况**：步骤 8 `deferred_ids = tasks − executed` 确实**包含**准入暂缓 id
  （它们在候选全集 `tasks` 里、未执行）。后一句应仅指「不在选择层
  selected_ids 口径」。本仓库以注释为口径规格，建议统一措辞。

### L2. `_seq_delivery_gate` 撤下成员后 tour.est_cost 不重算（已声明，提示审计口径）

- docstring 已如实声明「组原子性优先于航程记账精度」。提醒：`total_cost`
  （final_path_m）与 `infeasible_wasted_path_m`（按任务数比例分摊 est_cost）
  在发生组撤下的周期略偏大。论文引用这两个数时若要求精确，需注明该近似。

### L3. 顺序组「同批到达」不变量无运行时断言

- 组原子性依赖 `common.task_scenario._assign_sequence_chains` 的不变量：
  组内成员同批、共享 arrival_time。`sequence_group.py` docstring 已声明
  「不存在组内成员跨周期到达；未进池的任务不在组映射里」。若未来出现跨批
  链，`prune_incomplete_groups` 会静默放行「先到的部分组」（原子性退化为
  部分组调度，无报错）。建议在分组处或测试里钉一条不变量断言。

### L4. exp05 summary.csv 列序变化

- `51ee4c6` 在 `n_cycles` 与 `n_active_cycles` 之间插入 `n_oper_cycles` 列。
  按列名取数（pandas 等）无碍；若有按列位置解析的旧脚本会错位。提示。

---

## 逐项确认（无发现 / 正面）

- **`fd52ba3`（F1-F6 事件机制修复）**：假开关/电平触发各条修复与问题一一
  对应，`detect_events.enabled` 与 `HorizonConfig.event_replan` 双层开关在
  docstring 划清「检测」vs「反应」。无发现。
- **`a8bad17`（cost_hint 回退修复）**：修复的正是「docstring 声称 cost_hint
  回退、实现却直落 distance_fn/欧氏」的文档-实现分裂；四级回退链
  （pairwise > cost_hint > distance_fn > 欧氏）现与文档一致，且有
  test_prefilter_* 4 项钉住。修得好。
- **`23e387c` 顺序组原子性**：
  - `_seq_delivery_gate` / 完成闸门①b 的 `seq_deferred` **一次算好再走
    tour 循环**，不随遍历顺序变化——核查过 tour 处理顺序无「先完成后回滚」
    竞态 ✓；
  - 已出池成员不计入组映射（防永久阻塞）、`mark_dormant`/`release` 自带
    状态护栏、连坐不计废飞（与真违约分账）均有测试钉住 ✓；
  - `_wake_dormant` 用「仅休眠成员」组映射是**正确**取舍（若用全活动池
    映射，PENDING 成员会使 `prune_unwakeable_groups` 判整组不可唤醒 →
    死锁），已核对语义自洽。
- **`51ee4c6`（运营周期判据）**：record/dict 双实现有一致性测试钉住；
  `n_oper_cycles` 按列表位置取（与绘图裁剪同基），全空回退 n_cycles 无除零。
  自审无发现。
- **收敛判据四路**（pool_empty / 到达阶段 / 休眠禁兜底 / no_progress 兜底）
  与 `stop_reason` 回填均有测试；`pool_empty` 判据含执行中任务（pool 保留
  执行中任务），不会在在途任务未交付时误停 ✓。

---

## 建议优先级

1. **短期**：M2（exp05 config 注释同步，5 分钟）→ M1（注释/PIPELINE 措辞
   如实化，或裁定是否扩展兜底）→ M3（docstring 改口或判据对齐）。
2. **随手**：L1 注释统一；L3 在 task_scenario 分组处加不变量断言。
3. **引用注意**：L2/L4 在论文取数与下游脚本处注明。

（本快照只记录审查结论，不含代码修改；如需修复另起任务。）
