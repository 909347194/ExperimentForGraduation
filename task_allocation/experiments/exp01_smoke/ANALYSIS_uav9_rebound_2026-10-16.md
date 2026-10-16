# uav_9 边际航程弹回链路排查报告（2026-10-16）

> 目标：论文提交前，单独深挖 `results_phys_60`（60 任务 / 收敛早停 / seed=42）中
> uav_9 被反复判为 `range_uav_9` / `tour_infeasible_uav_9`、共 **16 次**弹回的根因，
> 并给出可落地的修复与论文表述。

---

## 1. 现象（来自 `results_phys_60/metrics.json`）

- `problem.uav_max_range_km = 80.0` → 满电 `remaining_range = 80000 m`。
- 弹回仅出现在 **周期 3–6**，每周期 `n_infeasible=4`，`ids=[47,59,40,55]`，tag 均含
  `range_uav_9` + `tour_infeasible_uav_9`（55 另带 `tw_task_55`）。合计 **16 次**，每任务各 4 次。
- `results_phys_60/solution.json` 决定性证据：uav_9 在周期 3–6 的交付航次**完全固定**
  `task_ids=[47,59,40,55], end_nest=4, est_cost=80027.583 m`
  —— 超 80000 m 仅 **27.583 m = 0.034%**，极边际越限。

结论：这不是随机求解噪声，而是**同一航次在修复链末端被泄漏为"已交付"后又被完成闸门弹回**。

---

## 2. 排查方法

1. **代码链路通读**：`problem.tour_path_cost`（含返航腿）↔ `MUASAssignmentEvaluator._tour_cost`
   （同口径，已对账一致）→ `check_range_constraint(margin=1.0)` → `build_solution` 修复链
   → `horizon._violated_task_ids` → 完成闸门弹回。
2. **四阶段插桩**（`build_solution` 后处理四检查点打印 uav_9 航程/上限/OVER）：
   `post-loop-entry` → `after-complete` → `after-reorder` → `after-final-choose`。
3. **强制复现 + 重排调试**：在 `_best_order._evaluate` 注入 `[47,59,40,55]` 专用探针，
   打印预算 / 候选序 key / 返航腿 / 全航程。

---

## 3. 根因（两层）

### 3.1 主因：`_reorder_for_deadlines` 的航程预算漏算返航腿

`methods/muas/solvers/muas_stage.py` 的 `_best_order._evaluate`（约 778–795 行）
在 `length > budget` 检查前，**只累加外出链** `start → t1 → … → tk`，
**漏加返航腿 `tk → end_nest`**。

探针实测 `[47,59,40,55]`（uav_9 @ cycle 3，起点 nest 4）：

| 量 | 值 |
|---|---|
| 外出链 `start→47→59→40→55` | 48713.62 m |
| 返航腿 `55 → nest4` | 31313.96 m |
| `_evaluate` 计算 length（仅外出） | **48713.62 m**（< 80000，通过预算） |
| 交付解全航程 `problem.tour_path_cost`（含返航） | **80027.58 m**（> 80000，越限） |

`_repair_range` / 完成闸门 / `_tour_cost` 都用**全航程口径**（含返航腿），
唯独 `_evaluate` 用外出链口径 → 预算门形同虚设。

修复链末端序列（`post-loop-entry` 已可行 `[59,40,55,47]` @ 77686.91 m）：
```
post-loop-entry    [59,40,55,47] end=4 cost=77686.91  OVER=False   ← 可行
after-complete     [59,40,55,47] end=4 cost=77686.91  OVER=False   ← 可行
after-reorder      [47,59,40,55] end=4 cost=80027.58  OVER=True    ← 重排破坏
after-final-choose [47,59,40,55] end=4 cost=80027.58  OVER=True    ← 泄漏交付
```
`_reorder_for_deadlines` 仅按"迟到数 / 迟到量 / 航程"字典序重排（不改任务集），
把可行序换成 EDD 序 `[47,59,40,55]`（返航腿更长）。尾随的最终 `_choose_end_nests`
（line 675）**只重选终点巢、无航程复检**，越限航次直接漏进交付解。

### 3.2 放大层：奖励记账口径虚高（幽灵奖励）

`record.muas = dict(stage.diagnostics)`，`f1_reward` 来自 `problem.f1_reward(final)`，
即**求解器"交付前"解**（`executed_task_ids` 含越限航次）。完成闸门虽正确弹回（inf=4, ncomp=0），
但 metrics 的 `f1_reward` 已按"已执行"计入那 4 个任务：
- 修复前 cycles 5/6：`ncomp=0` 却 `f1_reward=9.98` → **幽灵奖励**（从未真正交付却记功）。
- `total_reward` 因此被虚高：`353.34`（含幽灵）vs 修复后 `291.90`（真实交付奖励）。

---

## 4. 修复（已落地 `muas_stage.py`）

**(a) 根因修复**——`_best_order._evaluate` 航程预算计入返航腿：
```python
length += self._return_leg_cost(seq[-1], None)   # 保守退化到最近巢
if length > budget + 1e-6:
    return None
```

**(b) 防御性兜底（Fix 2）**——`build_solution` 后处理在重排后、最终选巢后各补一道
`_repair_range`（用全航程口径），保证交付解任意航次不越剩余航程：
```python
routes = self._reorder_for_deadlines(routes)
routes, end_nests = self._repair_range(routes, end_nests)   # 重排后复修
if routes:
    end_nests = self._choose_end_nests(routes)
routes, end_nests = self._repair_range(routes, end_nests)   # 选巢后复修
```

**(c) 回归测试**——`tests/test_repair_reflow.py` 新增两条对抗性用例：
- `test_reorder_range_guard_counts_return_leg`：构造可行序 [p,q] 与越限序 [q,p]，
  末任务换人拉长返航腿；验证 `_best_order` 预算必须含返航腿，否则选错序。
- `test_build_solution_delivered_tours_within_range`：构造 uav_9 的 [47,59,40,55] 级
  越限航次，验证 `build_solution` 交付解不出现超剩余航程航次。
- 两者均**无修复时失败、修复后通过**（已验证锁死漏洞）。

---

## 5. 端到端验证（`results_phys_60` vs `results_phys_60_fixed`，同 seed=42）

| 指标 | 修复前 | 修复后 | 变化 |
|---|---|---|---|
| `n_infeasible_delivery`（废飞弹回） | 16 | **0** | −16 |
| `n_completed` | 59 | **60** (100%) | +1 |
| `n_eliminated`（超期淘汰） | 1 | **0** | −1 |
| `on_time_rate` | 0.667 | **0.800** | +0.133 |
| `avg_lateness_s` | 889.86 | **691.39** | −198.5 |
| 全局越限交付航次 | 4（uav_9 × 周期3–6） | **0** | 全堵死 |
| `total_reward` | 353.34 | 291.90 | **注：修复前含幽灵奖励** |
| 收敛周期数 | 11 | **9** | 任务不再滞留弹回、池更快清空 |

> 注：`total_reward` 下降是**修复虚假高报**的结果——修复前把被闸门弹回的任务亦计入奖励；
> `291.90` 才是 60 个真实交付任务的奖励，指标口径因此更可信。完成率、按时率、迟到深度
> 三项均显著改善，说明修复在正确性上净正、无副作用。

---

## 6. 论文表述建议

- 在 §6「完成反馈可行性闸门」补充：重排阶段（`_reorder_for_deadlines`）的航程预算
  必须覆盖返航腿，否则 EDD 重排会把可行航次推过剩余航程，泄漏为"已交付"后被闸门弹回
  （表现为 `range_uav_*` / `tour_infeasible_uav_*`，属决策器内部修复缺口而非任务本身不可行）。
- 该边界案例（超限仅 27.58 m = 0.034%）可量化说明"完成闸门 + 修复链闭环"的必要性：
  阈值级误差即足以触发整段航次废飞，凸显全航程口径（含返航腿）在每一道闸门的一致性要求。
- `results_phys_60_fixed/` 为修复后官方结果（60/60 完成、0 弹回），替代 `results_phys_60` 作论文主证据。

---

## 7. 残留 / 注意

- 插桩（`_DEBUG_UAV9` / `_uav9_probe` / `DBG-uav9-reorder`）排查后已**全部移除**，
  `muas_stage.py` 语法与 213 测试（211 原 + 2 新增）全绿。
- `config.yaml` 已恢复为 `results_phys_60` 生成态（`output_dir: results`），未指向 `_fixed` 目录。
- 复跑脚本：`probe_uav9.py`（四阶段探针 + 重排调试）、`measure_div.py`（travel/cost 口径差，已证 ≈0）保留作复现证据。
