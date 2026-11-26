# 周期内代价回填与有限重解：跨包实施规划

> **状态：已被 v3 裁决取代（2026-11-25）。** 本文的 P1/P2 主体（逐段事实、三段式、有界重解、
> 逐版记账）已落地；但 P1 的"边覆盖 + 全局 λ"与 P2 的"残差触发、多版轮询"、P4 的
> "`scope=persistent` 缓存"已按裁决**整体删除**，不是修补。
>
> 裁决要点：上层估计只是排序用的近似尺子，只须不把明确不可行的边当成可行；任务点逐周期
> 变化时"旧 OD 的 实测/估计 比"没有可迁移对象，在线校准的前提不成立。
>
> 现行口径见 `docs/in_cycle_resolve.md`（v3 裁剪版）：代价 = `base` + 实测长度覆盖 + 不可飞边极大值，不可飞边按几何类
> 白名单，同周期至多剔一次，闭环 = "失败回池补飞 + 硬不可行剔除"。

**归属一句话**：下层只交付精确事实，上层只负责代价与重解，编排层负责“何时回灌、回灌几次、以哪一版为交付”。

## 1. 归属总表

| 包 | 负责 | 不负责 | 关键落点 |
|---|---|---|---|
| `path_planning` | 把每条已规划航段的真实长度/时长、失败码与严重性、交付闸结论作为**事实**报出；同一 `t0` 下可对新手航次重规划 | 不改分配、不改 λ、不决定重解次数 | `common/plan_types.py` 新增 `LegCostRecord`；`methods/planning/tour_planner.py` 把 `diagnostics["legs"][i]["length_m"]` 与失败段提升为稳定字段（含垂直段和返航段）；`api.py` 原样透出 |
| `task_allocation` | 维护 `c_ij` 覆盖层、全局 λ、硬不可行边；在**同一 prepared snapshot** 上重解；只在最终解上回写任务池/机队 | 不调用下层、不读真实轨迹、不驱动周期 | `methods/muas/cost/` 新增 `FeedbackCostView`；`rolling/horizon.py` 拆 `prepare / solve / commit`，`run_cycle` 保留为薄包装；新增 `resolve_with(update)` |
| `orchestration` | 计算残差、构造 `CostUpdate`、按预算触发有限次重解、记录每版尝试、决定最终交付 | 不算代价、不排轨迹、不改两侧算法 | `protocol.py` 加 `CostUpdate / ResolveAttempt / CyclePlan.attempts`；`feedback.py` 拆“边回填”和“λ 校准”；`orchestrator.py` 加有界重解循环；`adapters/` 透传新端口 |

依赖方向不变：两侧互不 import，也都不 import `orchestration`；只有编排层同时看见两者。

## 2. 单周期时序（新）

```text
prepare（上层，t0 冻结、事件上下文锁定；不回写）
  → plan_fleet 第 0 版 → 精确 LegCostRecord + 票
  → 编排层算残差，构造 CostUpdate（边覆盖 + 硬不可行边 + λ 修正）
  → 仅当触发条件成立且预算未用尽才进入重解；否则直接提交第 0 版
       apply_cost_update → resolve（同 snapshot，不推进时间）
       → plan_fleet 第 1 版 → 再算残差 … 直到收敛或次数用尽
  → commit 最终版（任务池/机队只写一次）
  → prune / 记账 / 审计（attempts 全留痕）
```

**主节奏仍锚定周期边界**：重解不改 `t_start/t_end`，不改 `t0`，不把机位挪回巢两次；下层每次都按同一批冻结 `t0` 重排。配置 `resolve.max_in_cycle` 默认 **1** 是**上限，不是每拍必做**。

**触发条件（默认 `threshold_or_hard`）**：
1. 当前交付解用到了 `INFEASIBLE` 硬边 → **必须**重解，否则硬不可行没被消掉；
2. 实测/估计残差同时超过相对阈值与绝对阈值（是否要 `confirm_cycles` 由配置定）→ 触发；
3. 只回填、不触发：残差在阈值内、预算为 0、或仅有 λ 微调而没有边覆盖/硬边。

回灌本身 **不等于** 重解：只落 `CostUpdate` 时，新代价从下一自然周期生效。即使触发了重解，若新解的任务归属/顺序/终点巢与旧解逐位相同，下层也不重规划，直接复用当前票。当前 `LoopDriver` 只有 `apply_cost_scale`、没有 `resolve`，上述触发逻辑是新增能力。现有单侧实验不走编排层，行为零变化。

## 3. 三条硬口径

1. **硬不可行进代价**：用 `path_planning/common/failure_codes.py` 已有的 `Severity` 分级。
   - `INFEASIBLE` → 对应有向边 `(from_id,to_id)` 标为硬禁边；语义 `+∞`，实现用大 M **并**在可行性闸门显式拒绝含该边的航次，不把裸 `inf` 丢进 DE。
   - `DEGRADED` → 用实测值作有限代价，票带 `time_source=estimate`。
   - `INTERNAL` → 不改代价，按 bug 处理（否则会把代码错误误判成任务不可行）。
   仅改 λ、继续认为原匹配合法的做法禁止。

2. **λ 与 `c_ij` 不双重计算**：`FeedbackCostView` 的唯一取值规则是
   `cost(a,b) = override[(a,b)] if present else λ × base_estimate(a,b)`。
   已被边覆盖的边不再乘 λ；校准 λ 时把已覆盖边从 `est/real` 求和里剔除。边覆盖默认 `scope=cycle`，周期末清空；`persistent` 为显式选项，键含精确坐标哈希、地形/水域版本与参数版本，位置或场一变即失效。

3. **指标按最终交付解算**：`CyclePlan.attempts` 逐版记录 `S0/S1/...` 的票、残差、失败码、代价版本；完成/不可行/`executed_ids`/`rolled_back`/`busy_until` 只取最终 commit 那一版。中间失败票只进诊断计数 `n_intermediate_failures`，不得计入完成，也不得占机位。

## 4. 接口增量

- `TourTicket` 增加 `leg_costs: tuple[LegCostRecord,...]` 与 `failure_severity`；`LegCostRecord = (from_id, to_id, length_m, duration_s, time_source, failure_code, severity)`。失败票也要带已解前缀的 leg 记录，失败段给 `severity` 与 `length_m`（无实测长度时为空，不用猜测值）。
- `CostUpdate = {edge_overrides, hard_edges, scale_delta, scope, source_attempt}`；`ResolveAttempt = {attempt_id, snapshot_id, tickets, est_cost, residuals, cost_version}`。
- 端口新增可选能力：`AllocatorPort.apply_cost_update(update)` / `resolve()` / `commit()`；旧端口只有 `step` 时 `LoopDriver` 走原有一拍路径，零行为变化。
- 事件上下文（`forced_task_ids`、`unavailable_nest_ids`、`rehome`）在 `prepare` 时锁定并重用到每次重解，不在周期中途重新检测事件。

## 5. 分阶段安排

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 契约 | 新增 `LegCostRecord` / `CostUpdate` / `ResolveAttempt`；`FeedbackCostView` 骨架；`FailureCode.severity` 透传到票 | 现有测试与 exp01 单周期结果逐位不变；新增字段可 JSON 序列化 |
| P1 上层拆分 | `run_cycle` 拆成 `prepare/solve/commit`；`run_cycle` 包装三者；代价视图注入 `LazyPairwiseCost` 与 `build_pairwise_costs` | 无回灌时对拍旧账逐位一致；重复 `solve` 不重复推进时间/不重复写池 |
| P2 编排闭环 | `LoopDriver` 有界重解循环、`apply_cost_update`、`resolve`、最终 `commit`；默认 1 次 | 用假端口做单测：0/1/2 次重解、硬边拒绝、预算耗尽、中间失败不算完成 |
| P3 exp01 验证 | `exp01_smoke` 加配置与审计：重解前后各记一次；对照“不重解 / λ-only / c_ij-only / c_ij+λ” | 单 seed 先跑通；禁止宣称收益，除非受控对照有统计结论 |
| P4 持久化与收口 | `scope=persistent` 缓存及失效；λ 排除逻辑；性能与审计文档 | 固定点位切换场版本能正确失效；无双重计价证据 |

## 6. 风险与边界

- 下层 `RRT_NO_PATH` / `OPT_FAILED` 虽标 `INFEASIBLE`，但可能是预算不足；若没有可复核的几何证书，先按大 M 有限罚并单列，不直接永久删边。
- 重解会改变航次与站序，下层冲突协调结果也会变；每版都要走完整交付闸，不能复用上一版轨迹。
- `resolve` 只在编排层驱动的上层端口提供；`task_allocation` 独立实验不得因此多解一次。
- 完成率分母仍排除真几何不可达；eliminating 必须为 0。
- 论文措辞只写“同一周期内用实测代价重解一次”，不写“全局最优”“性能提升”，收益需 P3 受控对照支撑。
