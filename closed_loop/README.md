# closed_loop —— 任务分配 × 轨迹规划 的跨层环

> **定位：集成层，不含任何算法。** 把第 3 章上层（`task_allocation/`）与第 4 章下层
> （`path_planning/`）串成 update–reassign–replan 的一拍一拍。

## 1. 这份 README 是重写过的

旧的 README 描述的是**另一套没落地的设计**：它假设下层有 `path_planning/interface/`
门面（`detect_events` / `build_feedback` / `CostMatrix`），并指向
`path_planning/experiments/exp04_closed_loop` 与 `exp05_joint_sim` 两个实验。实际交付的
下层**没有 `interface/`**，入口是顶层 `api.py::PathPlanner`（`plan` / `plan_fleet` /
`coordinate` / `busy_until` / `prune`，环只用后四个）；实验是
`path_planning/experiments/exp01_smoke` … `exp06_cycles` 六个。

所以本目录按现状重写：**契约收窄到"一条航次的出发时刻、停留点序列、真实时长"
这一组最小交换物**，不再有 `CostMatrix` / `detect_events` 这类从"下层负责判断"
衍生出来的中间层——判断仍在 `path_planning/methods/conflict` 里，只是不通过环中转。

> 按**当前实现**（含 `tour_planner` 交付闸与 `exp06_cycles` 的实证）重做的设计见
> [`DESIGN.md`](DESIGN.md)：契约只加三处（`task_ids` / `gate` / `unplanned+rolled_back`）、
> 一拍七步的时序、派活两期走（期一已迁，期二改上层六处）、
> 以及验收实验 `exp07_closed_loop`。本 README 只讲现状与未做项。

## 2. 依赖规则（单向，两侧互不见）

```text
closed_loop ──► path_planning.api        （下层唯一门面）
            └─► task_allocation...horizon（上层求解入口）

task_allocation ✗ path_planning        path_planning ✗ task_allocation
两侧均不得 import closed_loop
```

`adapters/` 是两个唯一的接触点：`planner_side.py` 只碰 `PathPlanner.plan_fleet` /
`busy_until` / `prune`（对外统一暴露成 `plan_cycle`），
`allocator_side.py` 只碰 `RollingHorizon.run_cycle` 与它的 `pool / nests / hcfg`。

## 3. 交换物（`protocol.py`）

| 消息 | 方向 | 内容 |
| --- | --- | --- |
| `TourOrder` | 上层 → 下层 | 机号、**冻结的 `t0`**、起点、停留点序列（首项起飞点、末项终点机巢） |
| `TourTicket` | 下层 → 上层 | `t0 / t_end / duration_s / path_len_m`、`time_source`（planned / estimate / failed）、失败码 |
| `PlannerReport` | 下层 → 上层 | 一批票 + 让路留痕 `moved` + 冲突前后计数 + 检测耗时 |
| `CycleSnapshot` | 上层 → 环 | 周期号、`[t_start, t_end]`、本周期派出的航次、执行集、`est_cost` |
| `CyclePlan` | 环记账 | 上述两者的合并 + `busy_until` + `overruns` + `pruned` |
| `LoopTrace` | 环产出 | 逐周期的 `CyclePlan`、执行集、`overrun_cycles`、失败码 |

两个端口是 `typing.Protocol`：`AllocatorPort.step(new_tasks)` 与
`PlannerPort.plan_cycle(orders) / busy_until(uid) / prune(now)`。测试用假端口就能钉死
驱动语义，不必建场。

**为什么不用两侧现成的类型互传**：`PlanRequest` 带机体规格与预算、`UAVTour` 带
`est_cost` 与机巢决策，直接互传等于让一层依赖另一层的字段语义。环里只走"去哪、
几点走"和"真实用多久"，转换在适配器里做。

## 4. 时序：后置规划，以及超框

上层 `RollingHorizon.run_cycle` 内部就完成了"求解 → 回写 → 机位挪回机巢 → 任务判
完成"（`_advance_execution`）。环是在**周期结束后**拿到该周期航次快照去排航迹：

```text
每拍：
  ① allocator.step()       上层推进一个周期，给出本周期航次（t0 = t_end − cycle_length）
  ② planner.plan_cycle()   下层按冻结 t0 排一批 + 协调，给出真实 T_traj
  ③ 记账                    T_traj 与 est_cost 并列；t_end 晚于周期终点记 overrun
  ④ planner.prune(t_end)    摘掉已结束航迹（上层已判落地，留着会凭空造冲突）
```

所以它是**事后度量**，不是"先排再飞"。由此产生一条必须直视的事实：

- 若 \(T_{\mathrm{traj}} + t_0 \le\) 周期终点，环的账与上层执行一致；
- 若超出（`LoopTrace.overrun_cycles` 非空），说明上层"周期末即完成"的模型
  **盖不住真实航迹时长**——它已经把任务判完成、把机位挪回巢了。这个矛盾由环
  当场报出，不靠"通常不会超"掩盖。

## 5. 已经做了 / 还没做

**做了**（本目录代码 + `tests/`）：

- 契约与两个端口；`PathPlannerAdapter`（`TourOrder` → `PlanRequest`，逐机机体、
  冻结 `t0`、预算取机体名义能力、服务时长只从显式映射取）。上层习惯把**起点机巢也
  放进 `stops` 首项**，适配器按"首项是 nest 且与 `start_position` 重合则丢弃"去重——
  否则每条航次会多出一条 0 长度 leg（曾被 `tour_planner` 的退化段分支静默吃掉）；
- `PathPlannerAdapter.last_trajectories`（本拍交付的真实航迹缓存，供实验层在 prune
  之前落盘）。**入口即清空**：空拍不清会把上一拍航迹当代本拍产出，造成跨周期重复；
- `RollingHorizonAdapter`（周期航次 → `TourOrder`，v1 硬前提：**从巢起飞**，
  `start_nest_id` 为空直接报错，不猜）；
- `LoopDriver`（逐拍驱动、超框判定、失败票不进时长账、周期末 prune）；
- **场景级多周期证据**：`path_planning/experiments/exp06_cycles`（双机 3 拍、
  4 条真实航迹、34 项审计全过）。它用的派活已经是本目录的
  `allocators/cyclic.py`，但仍是"定点投放 + 最近空闲机"，不是 `RollingHorizon`——
  见下面第 1、4 条。
- **契约三处新增字段已落地**：`TourOrder.task_ids`、`TourTicket.gate`（取自下层
  `diagnostics["global_gate"]`）、`PlannerReport.unplanned` + `CyclePlan.rolled_back`；
  闭环测试 32 项（`test_orchestrator` / `test_adapters` / `test_cyclic_allocator`）。

**还没做**（按依赖顺序）：

1. **上层接入 `busy_until`**（改造方案 §10.3 的六处）：拆"指派"与"结算"，让
   \(T_{\mathrm{traj}} >\) 周期长度时任务不判完成、机位不前移。做完这条，超框从
   "报出来的矛盾"变成"被建模的正常情形"；
2. **在飞中改派**：`start_nest_id=None` 的航次要能从当前位置续飞（需要 1 的
   在飞状态与 `start_velocity` 非零）；
3. **代价回填**：把 `TourTicket` 的真实时长/航程回填成上层的边权（当前只并列记账，
   下一周期的 `est_cost` 仍是几何估计）；
4. **接真 `RollingHorizon` 的场景级闭环**：把 `exp06_cycles` 的派活换成
   `RollingHorizonAdapter`（依赖第 1 条先落地）。当前的多周期证据是"实验层编排 +
   真规划器"，能证明环闭合，不能证明生产侧编排已改造完。

## 6. 跑法

```powershell
# 项目根下
$env:PYTHONPATH="."
& ".\.venv\Scripts\python.exe" -m unittest closed_loop.tests.test_orchestrator closed_loop.tests.test_adapters closed_loop.tests.test_cyclic_allocator
```

## 7. 目录

| 文件 | 职责 |
| --- | --- |
| `DESIGN.md` | 按当前实现重写的设计（契约/时序/派活两期/硬规则/验收实验） |
| `protocol.py` | 交换物 + 两个端口（Protocol），不含逻辑 |
| `allocators/cyclic.py` | `CyclicTaskAllocator`：投放表 + `busy_of` + 最近空闲机（`AllocatorPort` 实现） |
| `orchestrator.py` | `LoopDriver`：逐拍驱动、超框、失败回池、prune、打印 |
| `adapters/planner_side.py` | `PathPlanner` → `PlannerPort` |
| `adapters/allocator_side.py` | `RollingHorizon` → `AllocatorPort` |
| `tests/` | 假端口的驱动语义 + 请求/票的字段映射 |
