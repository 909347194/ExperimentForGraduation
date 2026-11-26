# 周期内事实回灌（in-cycle fact feedback）

> 一句话：**下层只报事实，上层把它们写成代价数值，编排层管循环与记账**；同一周期内至多把
> 一张已有事实叠加一遍、至多重解一次，只有最终版进入交付与完成/不可行口径。

## 0. 边界：回灌就是写数值，不是校准

上层那把 `est_cost` 是**排序用的近似尺子**，不是场景模拟器。它只需要满足一件事：
别把明确不可行的边当成可行。因此本模块的接口只有一个——**把事实写成代价数**：

- 已飞航段：按实测长度、与底表同一权重口径重定该边代价；
- 几何不可飞航段：写成一个极大数值，由任务分配内部已有的航程检查判死。

**不做**的事（已删，不是省略）：

- 不做在线估计校准（无全局 \(\lambda\) 跨周期更新、无 `measured/estimated` 倍率收敛）；
- 不留跨周期缓存、不做跨周期边覆盖（`persistent` / `scope` 已删除，周期末即清）。

为什么同周期写、跨周期不写：同一冻结快照里，实测长度描述的正是这张矩阵上的那批边，
没有"迁移"这一步，所以它是事实；换个周期任务点就变了，同一批 OD 往往不再出现，
"旧边的实测"没有可迁移的对象——那是校准。

## 1. 三个包各管什么

| 包 | 职责 | 明确不做 |
| -- | -- | -- |
| `path_planning` | 逐段事实：`LegCostRecord`（端点、实测长度/时长、`time_source`、失败码 + `severity`） | 不改分配、不乘倍率、不决定重解 |
| `task_allocation` | 代价与求解：`FactCostView`（`base` + 实测覆盖 + 不可飞值）；`prepare` / `solve` / `commit` 三段式 | 不判"要不要重解"、不记 attempt |
| `orchestration` | 循环与记账：交付解 → 事实 → `CostUpdate` → 一次回灌重解 → `ResolveAttempt` → 只提交最终版 | 不改算法、不改几何、不校准 |

## 2. 三条写死的口径

**① 代价视图只叠数值，不叠判据**

\[
c(a,b)=\begin{cases}
c_{\text{infeasible}}, & (a,b)\in \mathcal{I}\ \text{（几何不可飞）}\\
\ell^{\,real}_{ab}\cdot w_b, & (a,b)\in \mathcal{F}\ \text{（已飞，实测长度）}\\
\text{base}(a,b), & \text{其余（逐位等于上层估计器原值）}
\end{cases}
\]

- \(\mathcal{F}\) 存**实测长度（米）**，视图按目标节点权重折成代价；不是把米数直接塞进
  \(c_{ij}\)，也不是"倍率×估计"。
- \(\mathcal{I}\) 的实现值 `INFEASIBLE_COST = 1e7`（米量级）。用 \(10^7\) 而不是 `inf`：
  DE 适应度要对候选求和，`inf − inf` 变 `NaN` 会污染整个种群。
- **没有第二套判据**：不另设可行性闸门、不另存一份"不可飞边集合"。含大值边的航次在
  `muas` 内部已有的航程检查（`cons.range`）里必然超限 →
  `tour.feasible=False` → 任务回池。判死是代价检查的**结果**，不是并列的第二条规则。
- `FactCostView.__setitem__` 对底表能算出的键**丢弃写入**——否则
  `build_pairwise_costs` 的回写会把这层视图变成"谁读谁改"的二次表。

**② 只有几何类 `infeasible` 才许写成不可飞**

`orchestration/feedback.INFEASIBLE_CODES` 白名单：

| 进白名单 | 不进白名单 |
| -- | -- |
| `CORRIDOR_TOO_NARROW` / `CORRIDOR_OUT_OF_DOMAIN` / `RRT_NO_PATH` / `CLEARANCE_VIOLATION` / `ENDPOINT_OUT_OF_BAND` | `RANGE_EXCEEDED` / `ENDURANCE_EXCEEDED`（整条航次预算判定，写成不可飞会误杀可行 OD） |
|  | `OPT_FAILED`（求解器偶发失败，不是地形事实） |

`degraded`（`TIME_NOT_OPTIMAL` / `TIME_EXCEEDS_BUDGET` / `NO_LOCAL_SOLUTION` /
`CYCLE_MISALIGN` / `CAPABILITY_BELOW_REQUEST`）与 `internal`（`KINEMATIC_INCONSISTENT` /
`CONFLICT_UNRESOLVED`）**一律不写不可飞**：前者是"有交付、只是降级"，后者是下层 bug，
把 bug 写成"任务不可行"会让任务被静默丢弃。

**③ 边的角色由航次结构给定，不靠 id 数值猜**

上下层共用一套整数编号：机巢 `0/1/2`、任务 `1..30` —— **撞号**。若按"id 在不在机巢集"
翻键，内部段 `1 → 5` 会被翻成 `机巢1 → 任务5`，写到的是一条根本不存在的边（静默失效，
审计也看不出来）。

角色由结构给出：`TourTicket.end_nest_id` 说明这一程收在哪个机巢，
"末任务 → `end_nest_id`"那一段才是返航边；`CostUpdate.nest_ends` 把它标出来，
`edge_keys.translate_by_role` 据此决定谁加 `nest_offset`，其余端点一律按任务翻，
翻不出来的边直接丢——宁可少写一条，也不记到错的节点上。

**键空间只有一个**：环里的账（`cost_update` / `unresolved_infeasible_edges` /
`infeasible_but_survived`）一律存**下层 id**；翻译只发生在适配器内部（只有它知道
`nest_offset`），出了适配器不外流。这样 `pending ∩ leftover` 可以直接求交，
不必在两套键之间来回换算。

## 3. 时序

```
prepare(冻结快照: t_start/t_end, 可用机, 事件上下文, 代价视图)
   → plan_cycle            # S0（首解，交付候选）
   → build_cost_update     # 实测长度 + INFEASIBLE_CODES 命中的边
   → 若无不可飞边：直接 commit   # 有实测长度也不重解（量到多长不构成理由）
   → 若有不可飞边且 resolve_blocker() 为空（唯一一次）：
        reset_cycle(已发布航迹的机) → apply_cost_update → resolve → plan_cycle(S1)
   → commit                 # 任务池 / 机队 / 执行推进 / 记账，只写一次
```

四条纪律：

1. 触发条件只有一个：`S0` 交付解里出现白名单命中的几何不可飞边。
   （判据是 `pending.infeasible`，不是"回灌单非空"——回灌单里几乎总有已飞长度。）
2. 至多一次（`max_in_cycle` 是上限不是轮询）：这是回灌动作，不是迭代优化。
3. 仍有不可飞边未消 ⇒ 写 `CyclePlan.unresolved_infeasible_edges`，不重试，交下一周期任务池。
4. 重解前 `planner.reset_cycle(uav_ids)` 把上一版**已发布**航迹从注册表撤下，
   否则第二版会躲自己上一版的航迹。

能力判据只有一处：`LoopDriver.resolve_blocker() -> str | None`。缺 `prepare` /
`apply_cost_update` / `resolve` / `commit` 任一入口、或下层缺 `reset_cycle`，都返回
"不可用 + 原因"，环退回一拍完成；**exp01 只有这一档，启动即报错**，不许跑成
"看着像回灌、其实没重解"。

异常纪律：重解是可选增强，它失败不得把本周期拖成"没提交"。resolve 分支整段包在
`try` 里，失败即退回**首解交付**（`CyclePlan.resolve_error` 落原文，审计判 FAIL），
`commit` 无论如何执行一次。

## 4. 代码地图

| 文件 | 职责 |
| -- | -- |
| `path_planning/common/leg_cost_records.py` | `LegCostRecord` 构造/编解码、码表分级 |
| `path_planning/methods/planning/leg_recorder.py` | 逐段追加器（每条 return 路径同一处写账） |
| `task_allocation/methods/muas/edge_keys.py` | 边键约定、逐边翻译（`translate_by_role`，角色由 `nest_ends` 给定） |
| `task_allocation/methods/muas/cost/fact_cost_view.py` | `base` + 实测覆盖 + 不可飞值的唯一取数口 |
| `task_allocation/methods/rolling/cycle_stages.py` | `prepare` / `solve` / `commit` 实现体 |
| `task_allocation/methods/rolling/horizon.py` | 对外薄包装：`begin_cycle` / `resolve_with` / `commit` / `cost_state` |
| `orchestration/feedback.py` | `INFEASIBLE_CODES` 白名单、`infeasible_edges_of` / `flown_legs_of` / `nest_ended_edges_of`、`build_cost_update` |
| `orchestration/orchestrator.py` | `resolve_blocker` 能力判据、`_step_with_resolve` 单次回灌与异常回退、attempt 记账 |
| `orchestration/adapters/planner_side.py` | `leg_costs` / `failure_severity` / `start_nest_id`+`end_nest_id` 透传、`reset_cycle` |
| `orchestration/experiments/exp01_smoke/support/report.py` | `write_meta`（周期 0 前立账）、`write_trace`（每周期追加，tmp+`os.replace` 原子写） |

## 5. 怎么用

### exp01（单档：事实回灌）

```powershell
# 仓库根；结果写 results/seed_0/
python -u -m orchestration.experiments.exp01_smoke.run
```

`config.yaml` 的 `resolve` 段只有一处口径：

```yaml
resolve:
  max_in_cycle: 1   # 同周期最多重解一次；只接受 1，不接受别的次数
```

本实验只验证方法档，不设 `off` 对照、也没有 `--arm` 开关：把"不重解"当基线属于
另外的实验，不能靠改这一处口径在同一批结果里拼出来。

`feedback` 段只剩一次性初值 `initial_estimate_scale: 1.0`（保守取值，不要下调）：
它不随实测更新，`rel_threshold` / `confirm_cycles` / `min_scale` / `max_scale` 已废弃。

### 直接驱动三段式

```python
record = horizon.begin_cycle(new_tasks)     # 首解（预览，不写状态）
horizon.resolve_with(cost_update)           # 同快照重解（同周期只调一次）
final = horizon.commit()                    # 一次性回写，落 records
```

## 6. 审计字段

| 层 | 字段 | 含义 |
| -- | -- | -- |
| 每次 attempt | `attempt_id` / `snapshot_id` / `cost_version` / `est_cost` / `residuals` / `failures` | 这一版看见了什么 |
| 每周期 | `attempts` / `n_resolves` / `n_intermediate_failures` / `cost_version` / `cost_update` / `n_measured_edges` / `unresolved_infeasible_edges` / `infeasible_but_survived` / `resolve_error` | 写了哪条边、中间坏了几次、还剩几条、**写了却没落地**的几条、重解是否炸过 |
| 汇总 | `total_resolves` / `intermediate_failures` / `infeasible_edges_written` / `measured_edges_written` / `final_cost_version` / `resolve_errors` | 整场横向指标 |

闭环账的核对分两类，**只有后一类能真失败**：

- 结构性对账（查不出 bug）：有不可飞边 \(\Rightarrow\) 有重解；重解 \(\le 1\)；无不可飞边的周期不得记重解。
- 可证伪判据（两条）：
  1. `infeasible_but_survived = ∅`。写进代价的边若原封不动留在终版里，说明回灌没落地
     （多半是下层 id 没翻成上层键，写到了一条不存在的边），审计直接 FAIL。
  2. `resolve_error = ""`。重解抛过异常的周期等于"没重解成、退回首解交付"，
     不能因为它交付了一个有效解就当成成功的一版。
  两条都由环在周期内算好落账，`reaudit` 只读不算——避免"从落盘数据重算"时踩到
  键空间或缺失字段的坑。

`residuals` 只作诊断，不参与任何触发判断。

## 7. 落盘与已知边界

结果目录**增量写**：`run_meta.json` 在周期 0 之前先写（先说清"这场是什么"），
`loop_trace.json` 每完成一个周期重写一次（tmp + `os.replace`，不留半截 JSON）。
跑到第 k 周期崩掉，前 k−1 个周期的账仍在盘上；`failures.json` / `metrics.json` /
`audit.json` / `trajectories.json` 仍只在收尾写一次（它们要全量 trace 才有意义）。

- `degraded` 段有实测长度，但**不写不可飞**：时间来源不可信不等于地形不可行。
- 失败段 `length_m=None`，写 0 会把"没飞成"记成"飞了 0 米"。
- 覆盖只对**飞过的边**成立，终版里其余边仍是估计；因此 facts 档与 off 档的差别是
  "实测覆盖 + 不可飞回灌"两件事一起，不能单独归因到覆盖。
- 重解版号是 `S0/S1`，不是新周期；`CycleSnapshot.snapshot_id` 保证同一快照可比。
- 论文口径写"**失败回池补飞 + 硬不可行剔除**"，不写"倍率收敛"。
