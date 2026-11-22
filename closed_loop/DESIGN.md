# closed_loop 设计（按**当前实现**重写）

> 这份文档替代旧 README 里那套"`interface/` 门面 + `CostMatrix` + `detect_events`"的设想。
> 依据是三处已经落了地的实现：`path_planning/api.py` 的五个方法（`plan`/`plan_fleet`/`coordinate`/
> `busy_until`/`prune`）、`tour_planner` 的**交付闸**、
> 以及 `exp06_cycles` 已经跑通的双机 3 拍（34 项审计全过）。

## 0. 落地状态（2026-11-21）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| `exp06_cycles` 双机 3 拍 | 已跑通（34/34） | 派活已改用本目录的 `allocators/cyclic.py` |
| 契约三处新增字段 | **已实现** | `TourOrder.task_ids` / `TourTicket.gate`（取 `diagnostics["global_gate"]`）/ `PlannerReport.unplanned` + `CyclePlan.rolled_back`；测试 32 项通过 |
| 期一（`allocators/cyclic.py`） | **已完成（2026-11-22）** | 算法从实验层迁入环；`LoopTask` 自有类型，不再 import `path_planning`；测试随之迁到 `closed_loop/tests/test_cyclic_allocator.py` |
| 期二（上层六处） | 未动 | `task_allocation` 的 `busy_until` / 在飞过滤 / 结算拆分全部原样 |
| `exp07_closed_loop` | 未建 | 只有本节 §9 的设计，无 `config/run/audit` |

一句话：**契约与派活已按本设计落地，上层尚未接入（期二）。** 下面的约束都来自已经落地的实现，
新增项都是"可审计性"，不是新算法。

## 1. 事实基线（写在设计前面的硬边界）

| 事实 | 来源 | 对环的约束 |
| --- | --- | --- |
| 下层只暴露 `plan` / `plan_fleet` / `coordinate` / `busy_until` / `prune` 五个入口 | `api.py` | 环不许摸 `_plan_leg`、不许自带 A*/RRT |
| 交付前必须过**全域 0.2 m 稠密净空 + 全域真几何** | `methods/planning/delivery_gate.py` | 票的 `planned` 已经隐含"可交付"，环不必再验净空 |
| 交付闸不过 → `PlanResult.failed(CLEARANCE_VIOLATION)`，**不带航迹** | `tour_planner.py` | 失败票没有几何可落盘，只能记码 |
| 求解留余量（`local_replan_clearance_fix_m=2` 重解）、验收按 6 m | `tour_planner._limits` / `_plan_leg` | 环不要拿"评审阈值"去裁票 |
| 同机未结束航迹不可注册；`busy_until` 未注册返 0.0 | `trajectory_registry.py` | 派活的空闲判据与 `busy_until` 必须同口径 |
| 多机协调在 `plan_fleet` 内部完成（垂直/横向档位、轮次、局部重规划、全局复检） | `api.coordinate` | 环不重复协调；只记 `moved` 与冲突前后数 |
| 实验层已跑通"投放 → 派活 → 真航迹 → `busy_until` → prune" | `exp06_cycles`（34/34） | 环的形态已有实证，设计只需把它收进 `closed_loop/` |

## 2. 环只做四件事

1. **时间轴**：把上层的"第 k 拍"翻译成冻结的 `t0`，不重算出发时刻；
2. **交换**：订单（去哪、几点走）→ 票（真实时长、时间来源、失败码）；
3. **记账**：`busy_until` 累计、超框判定、prune；
4. **判定**：本拍哪些任务真落地、哪些回池、下一拍谁能接活。

不做的：不排航迹、不做冲突消解、不做几何校验、不改 `t0`、不估时长当账。

## 3. 契约（按现有类型收敛，只加三处）

| 消息 | 方向 | 现状字段 | 本次设计新增 |
| --- | --- | --- | --- |
| `TourOrder` | 上 → 下 | `uav_id` / 冻结 `t0` / `start_position` / `stops`（首项投放点、末项机巢）/ `meta` | `task_ids`（显式任务 id，供回池） |
| `TourTicket` | 下 → 上 | `t0/t_end/duration_s/path_len_m` / `time_source` / `failure` | `gate`（`ok/worst_clearance_m/first` 三元组，取 `diagnostics["global_gate"]`） |
| `PlannerReport` | 下 → 上 | 票 + `moved` + `conflicts_before/after` + `elapsed_s` | `unplanned`（有单但没航迹的 uid 集合） |
| `CycleSnapshot` | 上 → 环 | 拍号、`[t_start,t_end]`、`orders`、`executed_ids`、`est_cost` | — |
| `CyclePlan` | 环记账 | 上述合并 + `busy_until` + `overruns` + `pruned` | `rolled_back`（本拍失败回池的任务 id） |
| `LoopTrace` | 环产出 | 逐拍 `CyclePlan` + `executed_ids` + `overrun_cycles` + `failures` | — |

新增的三处都是**可审计性**，不是算法：`task_ids` 让"任务回池"有身份，`gate` 让"为什么这条
航迹能交付"能在票里查到，`unplanned/rolled_back` 让失败不再是空票。

## 4. 时序：一拍七步

```text
① allocator.step(new_tasks)     上层推进一拍，给 CycleSnapshot（orders 里 t0 已冻结）
② 组装请求                      start = 该机当前位置（v1 只允许在巢）；stops = 任务点… + 本巢
                                服务时长显式映射；预算取机体名义能力
③ planner.plan_cycle(orders)   适配器转成 PlanRequest → `api.plan_fleet`：逐机排航迹
                                （含交付闸）→ 组内协调 → 回写 registry
④ 票落账                        planned → busy_until[uid] = t_end；failed → failures 留痕，
                                busy_until 不变、任务进 rolled_back
⑤ 航迹落盘                     **prune 之前**把 last_trajectories 写成快照（否则证据被摘掉）
⑥ prune(t_end_of_cycle)        摘 t_end ≤ 拍末的航迹（落地结算）
⑦ 超框判定                     ticket.t_end > 拍末 + overrun_tol → overrun_cycles
```

第 ⑤ 步的顺序是硬要求：`prune` 之后 `registry` 里已经没有了，实验就再也拿不到"这一拍交付了
什么"。`exp06` 曾因为适配器空拍没清 `last_trajectories`，把上一拍航迹当成这一拍重复落盘。

## 5. 派活从哪来：两期走

**期一（已完成，2026-11-22）**

- `CyclicTaskAllocator` 从 `path_planning` 实验层迁到
  `closed_loop/allocators/cyclic.py`；任务点类型改为环自有的 `LoopTask`
  （id / position / service_time_s / release_cycle），**不再 import `path_planning`**；
- 派活规则未改：按 `release_cycle` 投放、`busy_of(uid) <= t_start` 筛空闲、
  最近空闲机优先、三维往返距离当 `est_cost`、装不下记 `unassigned`；
  航次额外带 `task_ids`，失败回池才有身份；
- 实验层只剩"配置 → `LoopTask`"的转换，`path_planning` 实验层不再持有派活算法；
- 测试随迁：`closed_loop/tests/test_cyclic_allocator.py`（11 项，新增 `task_ids` 一项），
  闭环测试合计 32 项。

**期二（要动上层，改造方案 §10.3 六处）**

1. `UAV` 加 `busy_until` 与 `is_free_at(t)`；
2. `uav_state.available()` 过滤在飞；
3. `horizon._advance_execution` 拆"指派"与"结算"（结算只在 `t_end` 之后）；
4. `CycleRecord` 记 `in_flight_ids`；
5. `baselines/construct.py` 三处经 `available()`；
6. `RollingHorizonAdapter` 的 `start_nest_id=None` 才能成立（在飞改派的前提）。

期二完成的判据只有一句：**超框从"报出来的矛盾"变成"被建模的正常情形"**，而
`exp06` 的四条判据（累计 `busy_until`、空拍无残留、同机不重叠、跨周期冲突 0）仍全绿。

## 6. 三条硬规则（违反任一条，环就是假的）

1. **一个 uid 同一时刻只有一条未结束航迹**：注册表拒绝叠加，环也不许"先排后删旧的"；
2. **`busy_until` 是累计不是本拍时长**：周期 2 的 386.78 = 240 + 146.78。写成"本拍时长"，
   下一拍就会把刚飞完的机当空闲——这是多周期最容易犯的错；
3. **失败票不许写进 `busy_until`**：没航迹就没占用，否则机位被虚占，后续拍全空。

## 7. 失败与降级口径（票的四种失败）

| 失败码 | 含义 | 环的处理 |
| --- | --- | --- |
| `RRT_NO_PATH` | 走廊窗口里连初值都没有 | 任务回池，下一拍重投；连续 2 拍同码 → 记 `failures` 并停投 |
| `OPT_FAILED` | 候选全被复核/真几何/稠密净空拒 | 同上；`rejected` 里的理由进 `run_meta` 供定位 |
| `CLEARANCE_VIOLATION` | 交付闸不过（全域 0.2 m 或真几何） | 同上；**不许**用"段内判过"翻案 |
| `TIME_EXCEEDS_BUDGET` | 有航迹但超续航/航程 | 票为 `failed` 但**带航迹**：只落盘、不记账、不派下一拍 |

`time_source=estimate` 只在无解时兜底，环必须把它当"不可交付"，不能当 `planned` 用。

## 8. 冲突与跨拍检测

- 拍内：`plan_fleet` 已做协调，环只读 `moved` / 冲突前后数；
- 跨拍：上一拍未 prune 的航迹仍在 `registry` 里，`plan_fleet` 注册新航迹时会撞上——
  这正是"跨周期遗留"语义（`exp03` 验证过），环不需要额外逻辑；
- 离环审计（如 `exp06/audit.py`）用**虚拟 id** `cycle*10+uid` 才能一次检测四拍，
  因为 `detect_conflicts` 要求机号唯一；这是审计口径，不是环的口径。

## 9. 实验：`exp07_closed_loop`（本设计的验收）

真场、真派活、4 拍 2 机，**统一 240 s 节拍**（`loop.cycle_length_s` 只有一个，不搞每拍变周期——
超框必须由真实时长撞出来，而不是把周期调小凑出来）。三种拍型都要出现：

| 拍 | 设计意图 | 必须看到 |
| --- | --- | --- |
| 1 | 正常派活（沿用 `exp06` 的 210/220 坐标） | 2 票 `planned`，`gate.ok=True`，净空 ≥ 6 |
| 2 | 投一个贴着楼的任务点（实测 SDF ≈ 0.52 m @ z=50） | 票 `RRT_NO_PATH`、`rolled_back` 非空、该机 `busy_until` 仍为 0 |
| 3 | 回池任务重投：**同一 task id** 换可飞点、换一拍投放 | 该 id 第二次出现且 `planned` 落地 |
| 4 | 投一个离两巢都远的任务（单程 ≈2.3 km，真实 ≈300 s > 240 s） | `overrun_cycles=[4]`，原因可解释（`t_end` > 拍末） |

判据 = 契约三处字段可用（`task_ids` / `gate` / `rolled_back`）+ 四条环语义（失败不虚占
`busy_until`、回池可重投、同机跨拍不重叠、跨周期冲突 0）+ 超框如实。
**反例基线**：把 `busy_until` 改成"本拍时长"，同机两拍必然重叠——判据必须红，否则判据是摆设。

> 预研结论（2026-11-22）：原 `(-769.6,507.6)` 一带的 5 个候选点在整条 tour 上**都能过交付闸**
> （最差 6.08–10.71 m），那个 5.59 m 是"特定折返航段"的几何，不是点的属性；贴楼点才是稳定
> 可复现的失败源（三发全 `RRT_NO_PATH`）。所以失败拍用"任务点贴楼"，不用"复现折返低点"。

## 10. 与论文口径的对应

- 第四章"联合优化"证据：`exp01`（单段 min ΣΔt）、`exp05`（多段）；
- "协调层"证据：`exp02`（迎头让路）、`exp03`（跨周期遗留 + 注册表）、`exp04`（消融）；
- "多周期"证据：`exp06`（实验层环，34/34）、`exp07`（环独立实现 + 失败/超框拍）；
- 本章**不声称**跨机联合优化，也不声称上层编排已改造完——期二是路线图，不是结论。
