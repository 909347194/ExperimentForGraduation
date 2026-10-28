# closed_loop — 任务分配 × 轨迹规划 事件触发闭环（跨层集成层）

> **定位：集成层，不含任何算法。** 把第 3 章上层（`task_allocation/`）与第 4 章下层
> （`path_planning/`）串成 Lu 等 (2026) 的 event-triggered **update–reassign–replan** loop。
>
> 章节归属：闭环*机制*的叙述在 `path_planning/README.md` §4.4（第 4 章）；
> 下层侧实现（事件检测 / 代价回填）在 `path_planning/interface/`；**环驱动**在本目录。

## 1. 为什么在根目录（决策记录 2026-10-28）

1. 环驱动必须同时调用两侧；若放进 `path_planning/`，会造成
   `path_planning → task_allocation` 反向依赖，破坏分层单向依赖约束；
2. Lu 等 (2026) 的框架口径：manager 层（分配）与 UAV 层（规划）解耦，闭环是两层之上的
   **第三构件**（原文 "our novelty lies in the framework-level integration"），不属于任一层算法；
3. 章节归属 ≠ 代码归属：闭环机制写在第 4 章，但代码分两半——下层侧在
   `path_planning/interface/`，环驱动跨章，归本目录；
4. `exp04_closed_loop` / `exp05_joint_sim` 联合仿真直接编排本模块，两个方法包保持互相零 import。

## 2. 依赖规则

```text
closed_loop/ ──► path_planning/interface/（下层唯一出口）
           └──► task_allocation 公开入口（上层唯一出口）

task_allocation ✗ path_planning      互不 import
path_planning ✗ task_allocation      互不 import
两方法包均不得 import closed_loop
```

## 3. 闭环协议

### 3.1 交换物（`protocol.py` 定义）

| 消息 | 方向 | 内容 |
|------|------|------|
| ODRequest 集合 | 上层 → 下层 | 起点（当前巢）/ 终点（任务点 → 候选巢） |
| PlanResult | 下层 → 上层 | route / path / trajectory / 总时间 / 总 TPR / 可行性 |
| CostMatrix C(i,j,k) | 下层 → 上层 | (无人机, 任务, 机巢) 真实综合代价；不可行 = ∞ + 原因 |
| CostUpdate | 下层 → 上层 | 事件触发的**增量**回填（行/列粒度） |
| ReassignRequest | 环内 | 重调度请求（换巢 / 改派 / 推迟） |
| NewODSet | 上层 → 下层 | 重调度后的新 OD 集合 |

### 3.2 三类触发事件（`path_planning/README.md` §4.4.2）→ 统一归约为"代价变化"

| 事件 | 归约 |
|------|------|
| 动态禁飞造成路径不可行 | 相关 (i,j,k) 行/列代价 → ∞ |
| 轨迹 TPR 超安全阈值 | 相关组合置超阈值标记 / ∞ |
| 滚动窗口调度周期到达 | 全量代价刷新（周期性同步） |

与 Lu 等 (2026) 对齐：其触发是电池衰减 / 威胁 D_k 变化 / 新机加入 → cost matrix
**行或列**变化 → Algorithm 2 **增量**重分配（非全量重算）。我们回填 C(i,j,k) 同样按
组合粒度增量更新，上层做增量重调度。

### 3.3 时序（update–reassign–replan）

```text
滚动周期到 / 事件触发
  → ① 下层按当前 OD 集规划（interface: plan / build_cost_matrix）
  → ② 事件检测（interface/feedback.detect_events）
  → ③ 代价增量回填 C(i,j,k)（update_cost_matrix，不可行置 ∞+原因）
  → ④ 触发事件非空 → 上层增量重调度（换巢 → 改派 → 推迟）
  → ⑤ 产生 NewODSet → 回到 ①
终止：任务全部完成 / 推迟出视野 / 达到滚动上限
```

## 4. 与 Lu 等 (2026) Fig.2 的对应

| Lu 等 (2026) | 本仓库 |
|--------------|--------|
| Manager 层（扩展 Hungarian 分配） | `task_allocation/`（选择 + MUAS + 滚动） |
| UAV 层（SE-ABC 3D 轨迹规划） | `path_planning/`（控制网络两阶段规划） |
| event-triggered update–reassign–replan loop | `closed_loop/`（本目录） |
| cost matrix c_ij 动态更新（Algorithm 2） | C(i,j,k) 代价矩阵 + CostUpdate 增量回填 |
| 电池衰减 / 威胁变化触发 | 动态禁飞阻断 / TPR 超阈值 / 滚动周期 |

## 5. 目录

| 文件 | 职责 |
|------|------|
| `protocol.py` | 跨层消息契约（CostUpdate / ReassignRequest / NewODSet 等字段口径） |
| `orchestrator.py` | 环驱动：滚动时钟、事件调度、终止条件（LoopDriver） |
| `adapters/planner_side.py` | 适配 `path_planning.interface.TrajectoryPlanner` → PlannerPort |
| `adapters/allocator_side.py` | 适配 task_allocation 求解入口 → AllocatorPort |

实验：`path_planning/experiments/exp04_closed_loop`、`exp05_joint_sim` 编排本模块。
