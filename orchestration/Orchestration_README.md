# Orchestration —— 集成编排层

把第 3 章的“派活”与第 4 章的“排航迹”按周期串起来。**本层不含算法**：只做消息转换、逐周期驱动、记账与可行性回灌。

## 1. 定位与依赖方向

- `task_allocation` 决定“执行哪些任务、谁飞、什么顺序、回哪个巢”。本层消费这个决定，不改它。
- `path_planning` 对给定航次交付带时间的无碰撞轨迹。本层消费这条轨迹，不改几何、不替它协调机队。
- 两层互不 import，也都不 import 本层；只有本层同时看见两侧。守卫：`tests/test_module_independence.py`（AST 扫描）。场景数据只读仓库根 `data/`。

```text
orchestration ──► task_allocation.methods.rolling.horizon.RollingHorizon
             └─► path_planning.api.PathPlanner

task_allocation ✗ path_planning      path_planning ✗ task_allocation
两侧 ✗ orchestration
```

## 2. 与两侧怎么接

| 侧 | 公开入口 | 本层接触点 | 交换内容 |
|---|---|---|---|
| 上层 | `RollingHorizon.run_cycle` | `adapters/allocator_side.py` | 取 `CycleSnapshot`（航次、`est_cost`、执行集）；启用重解时用 `prepare/apply_cost_update/resolve/commit` 透传；失败回池走 `reopen` |
| 下层 | `PathPlanner.plan_fleet / busy_until / prune` | `adapters/planner_side.py` | `TourOrder` → `PlanRequest`；收回 `TourTicket` / `PlannerReport`；周期末摘掉已落地航迹 |

两个适配器是唯一接触点，环不碰 `methods/`。`protocol.py` 另立消息类型，不直接传 `PlanRequest` / `UAVTour`，避免一层依赖另一层的字段语义。

端口形状：`AllocatorPort.step(new_tasks)`；`PlannerPort.plan_cycle(orders) / busy_until(uid) / prune(now)`；启用周期内重解时上层还需 `prepare(new_tasks) / apply_cost_update(update) / resolve() / commit()` 四个方法，下层还需 `reset_cycle(uav_ids)`（缺任一个就退回一拍完成）。

"为什么不能重解"只有一处判据：`LoopDriver.resolve_blocker()` 返回具体原因（`max_in_cycle=0` / 缺哪个端口方法 / 缺 `reset_cycle`），`can_resolve()` 只读它；实验层据此在启动时直接报错，而不是跑到中途才发现静默退化。

## 3. 交换物（`protocol.py`）

| 消息 | 方向 | 关键字段 |
|---|---|---|
| `TourOrder` | 上 → 下 | `uav_id`、冻结 `t0`、`start_position`、`stops`（首项起飞点、末项终点机巢）、`task_ids` |
| `TourTicket` | 下 → 上 | `t0/t_end/duration_s/path_len_m`、`time_source`（planned / estimate / failed）、`gate`、`failure/detail/diagnostic`、`task_ids`、`leg_costs` / `failure_severity`、`start_nest_id` / `end_nest_id`（这一程从哪个机巢起飞、收在哪个机巢） |
| `PlannerReport` | 下 → 上 | 一批航次 + 让路留痕 `moved` + 冲突前后计数 + `unplanned` |
| `CostUpdate` | 上 → 环 → 上 | `facts`（已飞航段的实测长度）、`infeasible`（几何不可飞边）、`nest_ends`（其中哪几条是返航边）、`source_attempt`、`reason`。**一律下层 id**，翻译只在适配器内部发生 |
| `CycleSnapshot` / `CyclePlan` | 上 → 环 / 环记账 | 周期区间、执行集、`est_cost`、`snapshot_id`/`attempt`；`busy_until`、`overruns`、`pruned`、`rolled_back`、`assignments`、`attempts`/`n_resolves`/`unresolved_infeasible_edges`/`infeasible_but_survived`/`resolve_error` |
| `LoopTrace` | 环产出 | 逐周期账、失败码、超框；`attempts` / 逐周期 `n_resolves` |

`task_ids` 是回池与“飞完”计数的唯一身份来源，不从 `stops` 反推（机巢也在 `stops` 里）。

## 4. 一个周期做什么

`LoopDriver.step_one()`：

```text
① allocator.prepare()    上层推进一周期并冻结快照；给出航次（t0 已冻结）
② planner.plan_cycle()   下层排一批 + 协调，给出真实时长、航程与逐段事实
③ 事实提取                实测长度（已飞航段）+ INFEASIBLE_CODES 命中的几何不可飞边
④ 回灌重解（可选，最多一次） 有不可飞边 → reset_cycle → apply_cost_update → resolve → 再规划
⑤ allocator.commit()     一次性回写任务池/机队；只提交最终版
⑥ planner.prune(t_end)   摘掉已落地航迹（留着会凭空造冲突）
```

无三段式能力或 `max_in_cycle=0` 时自动走旧路径 `step()`，行为与旧实现逐句一致。重解抛异常按"可选增强失败"处理：退回首解交付、`resolve_error` 落原文、`commit` 仍执行一次。

时序是**后置规划**：上层 `run_cycle` 内部已把任务判完成、机位挪回巢；下层真实航迹是周期结束后量出来的。所以超框只能如实上报，不能阻止上层结算。失败航次的 `task_ids` 进 `rolled_back`；是否真正回灌上层任务池由实验驱动调 `reopen` 完成，失败航次不占 `busy_until`、不进时长账。

## 5. 事实回灌（不做校准）

`feedback.build_cost_update` 把交付解的逐段事实折成一张 `CostUpdate`，**接口只有"写数值"一条**：

- 已飞航段：`facts`（实测长度，米），上层按底表同一权重口径重定该边代价
  \(c_{ij}=\ell^{real}_{ij}w_j\)。同一冻结快照、同一批 OD，所以是事实，不是校准。
- 几何不可飞边：`infeasible`，上层写成 `INFEASIBLE_COST = 1e7`；含该边的航次由**任务分配
  内部已有的航程检查**判死（`tour.feasible=False` → 任务回池），不另设可行性闸门。
- 白名单（`INFEASIBLE_CODES`）：`CORRIDOR_TOO_NARROW` / `CORRIDOR_OUT_OF_DOMAIN` / `RRT_NO_PATH` / `CLEARANCE_VIOLATION` / `ENDPOINT_OUT_OF_BAND`。
- 不白名单：`RANGE_EXCEEDED` / `ENDURANCE_EXCEEDED`（整条航次预算判定，写成不可飞会误杀可行 OD）、`OPT_FAILED`（求解器偶发失败）；`degraded` / `internal` 一律不写。
- 重解触发只看 `infeasible`：有实测长度不构成重解理由（"量到多长"不改变可行性）。
- 跨周期不写、不缓存、不做 \(\lambda\) 倍率校准：任务点逐周期变化时"旧 OD 的实测/估计比"不可迁移。
- `feedback=None` 语义已并入 `max_in_cycle`；`residuals` 只作诊断，不参与触发。

## 6. 当前边界

- 不处理在飞中改派、同周期撤单重派：需要上层把“指派”与“结算”拆开、让 `busy_until` 接管，本层才能把新指派交给下层。
- 不替第 3 章做选择/MUAS/滚动，不替第 4 章做轨迹优化、冲突协调或交付闸；事件触发属于上层。
- 不声明分配全局最优或轨迹全局最优；本层只保证交换契约与账目自洽。

## 7. 实验与跑法

`experiments/exp01_smoke/` 是唯一同时启动两个求解器的实验：真广州 DEM + 建筑场，3 巢 / 6 机 / 30 任务一次投放；验证“派活 → 真航迹 → 事实回灌 → 失败回池 → 下一周期补飞”。配置唯一来源是 `config.yaml`、`uav_config.json`、`tasks.json`；结果在 `results/seed_<N>/`（六 JSON + 四 CSV + 八图）。只有一档（周期内事实回灌），没有对照档与跳审计开关。**引用任何数字前先核对结果里的 `run_meta.config` 与 `tasks_sha256` 是否和这三个文件一致**；绘图只读结果，不重跑规划。建场按 `field.world.max_od_length_m`（城市对角线）经 `required_pad_m()` 派生 pad，请求期超长段报 `OD_TOO_LONG` 并回池；改上限会换场缓存键，须重建一次场。

```powershell
# 仓库根
$env:PYTHONPATH="."
& ".\.venv\Scripts\python.exe" -m pytest orchestration/tests tests -q
& ".\.venv\Scripts\python.exe" -u -m orchestration.experiments.exp01_smoke.run
```

长跑串行，不并发渲图。

`run` 的控制台按五段输出，每行即时刷新：**1 实验设定**（场景/机队/任务/两侧方法/输出目录，
建场之前就打印）、**2 准备**（场景 → 全城场缓存 → 规划器 → 分配器 → 编排检查，五步各报耗时）、
**3 逐周期过程**（任务分配首版 → 航迹规划首版 → 事实回灌 → 重解终版 → 周期小结）、
**4 实验结果**（到达/航次/排程/闲置/回灌/失败票）、**5 审计**（PASS 或逐条 FAIL）。
默认只打摘要；`--detail` 再追加逐航次明细表，完整账仍以 `results/seed_<N>/` 里的 JSON 为准。

## 8. 目录

| 文件 | 职责 |
|---|---|
| `protocol.py` | 交换物与两个端口，不含逻辑 |
| `orchestrator.py` | `LoopDriver`：逐周期驱动、超框、prune、不可飞边清单、记账 |
| `feedback.py` | `INFEASIBLE_CODES` 白名单、`infeasible_edges_of` / `flown_legs_of` / `build_cost_update`、`per_uav_residuals` |
| `adapters/` | `allocator_side.py` / `planner_side.py`：两侧唯一接触点 |
| `allocators/cyclic.py` | 测试用投放器（定点投放 + 最近空闲机），不是生产派活 |
| `experiments/exp01_smoke/` | 集成冒烟：真派活 + 真航迹 + 事实回灌 + 回池 |
| `tests/` | 端口映射、驱动语义与场景装配的假端口测试 |
