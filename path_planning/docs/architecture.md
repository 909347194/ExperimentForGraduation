# path_planning 分层架构

> 本文定义目录分层、依赖规则与三条数据流。模块级职责与接口契约见 [module_map.md](module_map.md)。

## 1. 分层总览

```text
┌────────────────────────────────────────────────────────────────────┐
│ experiments/   L4 实验编排层                                        │
│   expNN_xxx/：run.py 只做编排；config / metrics / plot 分离          │
├────────────────────────────────────────────────────────────────────┤
│ interface/     L3 对外契约层                                        │
│   planner.py（TrajectoryPlanner 门面） · feedback.py（事件/反馈）    │
├───────────────────────────────┬────────────────────────────────────┤
│ methods/        L2 算法层     │ baselines/    对照层（独立注册）     │
│   tpr/（第三方风险）          │   registry / lin_original /         │
│   control_network_planning/   │   euclidean_cost                    │
│     (constraints/cost/       │   （只依赖 L0/L1，不碰 methods/）   │
│      offline/online/)         │                                     │
│   visibility_graph/          │                                     │
│     (仅几何约束·放弃TPR)      │                                     │
├───────────────────────────────┴────────────────────────────────────┤
│ utils/         L1 通用工具层：geometry / math3d（纯函数）            │
├────────────────────────────────────────────────────────────────────┤
│ common/        L0 领域模型层：types / config / events / scenario     │
└────────────────────────────────────────────────────────────────────┘
  data/（数据资产） · tests/（测试） · docs/（文档）—— 不在依赖链上
```

`methods/control_network_planning/` 包内再分一层：

```text
        constraints/   cost/        ← 共用模型层（offline/online 都依赖）
             ▲            ▲
             │            │
   offline/ ─┘            └─ online/     ← 两条编排线，互不依赖
   （只执行一次）           （每 OD 一次）
```

## 2. 依赖规则（硬约束）

1. **只允许上层依赖下层**：L4 → L3 → L2 → L1 → L0；禁止反向与跨层跳依赖。
2. **offline/ 与 online/ 互不依赖**：online 只消费 offline 的产物（扩展控制网络文件），
   不 import offline 的模块；两者共用 constraints/ 与 cost/。
3. **baselines/ 不依赖 methods/**：对照组只依赖 L0/L1，避免实现细节泄入对照口径。
4. **interface/ 是唯一对外出口**：task_allocation/ 与根 closed_loop/ 不得绕过
   interface/ 直接 import methods/；同理 path_planning 不 import task_allocation 的算法模块
   （仅可在 common/scenario 保持场景/坐标口径一致）。
5. **common/ 不依赖本包任何模块**；utils/ 只依赖 common（能不依赖就不依赖）。
6. **禁止循环依赖**；experiments/ 与 tests/ 不被任何层依赖。
7. 每个模块 docstring 必须写明：职责 / 输入→输出 / **不负责什么** / 论文映射（防职责蔓延）。
8. **跨包契约类型由产出方定义**：下层交换类型（PlanResult、CostMatrix、CostUpdate、事件枚举）
   在 path_planning 侧定义并经 `interface/` 再导出，closed_loop 只引用不定义；上层同理。
   交换类型不得定义在集成层（否则产出方被迫反向 import 集成层）；
   closed_loop/protocol 只放环内消息（ReassignRequest / NewODSet）。

## 3. 三条数据流

### 3.1 离线预处理流（不依赖任务，只执行一次）

```text
data/raw ─► common/scenario ─► offline/control_points ─► offline/link_builder
        （建筑/DEM/人口/禁飞）      （d1/d2/d3 布点、         （d* 截断、碰撞+静态禁飞
                                     禁飞缓冲外、补虚点）       剪枝 → 原始控制网络）
   ─► offline/virtual_network ─► offline/precompute ─► offline/network_io
      （7 状态虚拟节点、            （链路时间 +              （data/processed/，
        4 对虚拟连线）               昼/夜 TPR 回填）          含参数指纹）
```

### 3.2 在线规划流（每 OD 一次，实时）

```text
ODRequest + 动态临时禁飞 ─► online/dynamic_prune ─► online/route_search
                              （剪枝视图+连通性校验）   （综合代价 Dijkstra → route）
   ─► online/dubins_smoother ─► online/post_check ─► online/time_optimal
      （3D Dubins → path，         （二次碰撞校验，        （曲率/限速/反向回溯
        过近点偏差收缩）             冲突则调偏移修正）      → trajectory）
   ─► PlanResult（route/path/trajectory/总时间/总 TPR/可行性）
```

### 3.3 闭环反馈流（事件触发，跨层）

```text
PlanResult ─► interface/feedback.detect_events
                （①动态禁飞阻断 ②TPR 超阈值 ③滚动周期到达）
   ─► build_feedback ─► cost_matrix C(i,j,k) 回填（真实代价 / ∞+原因）
   ─► 上层任务分配重调度（task_allocation/methods/rolling，属第 3 章）
   ─► 新 OD 集合 ─► 回到 3.2
```

### 3.4 可见性图流（visibility_graph，替代方案，仅几何约束）

```text
data/raw ─► common/scenario ─► obstacles.SimplifiedObstacles（建筑外扩+简化+STRtree）
        （建筑/禁飞）                ─► generate_nodes（轮廓采样+高度层+网格骨干）
                                       ─► build_edges（LOS+离线碰撞+机动保护区 prune）
                                          ─► VisibilityGraph（邻接表，build 一次，长期复用）

OD + depart_time ─► plan（端点吸附+增边）─► search.shortest_path（A*，动态时间窗门）
   ─► polyline（world 坐标）+ 三维代价 + 可行性原因
```

> 与 3.1/3.2 的差别：本流**放弃 TPR**，动态禁飞不在建图时处理，而在 `plan` 的 A\* 松弛阶段按
> `depart_time + 路长/巡航速度` 换算的时间窗逐边检查（选项 A）；图（建筑+静态禁飞）只建一次、长期复用。
> 该流与控制网络流互不依赖，共用 common/scenario 与 utils/geometry。

## 4. 与兄弟模块的边界

| 模块 | 归属 | 边界 |
|------|------|------|
| `task_allocation/` | 第 3 章（上层） | 任务选择/指派/排序/终点机巢；其 `methods/rolling/event_trigger.py` 是上层侧事件处理。**不实现轨迹算法** |
| `path_planning/` | 第 4 章（下层） | 单 OD 轨迹求解 + 下层侧事件/代价反馈。**不做任务重调度** |
| 根 `closed_loop/` | 跨层集成层 | 事件触发 update–reassign–replan 环驱动 + 联合仿真编排，不含任何算法（见根 `closed_loop/README.md`） |

场景/坐标口径：两章共用同一场景数据时，坐标系（NED / 本地笛卡尔）与 DEM/建筑来源
必须统一在 `common/scenario.py`，与 `task_allocation/common/scenario_data.py` 保持一致。

### 4.1 闭环归属决策（2026-10-28）

**闭环放根目录，不进 path_planning/**。理由：

1. 环驱动需同时调用两侧；放进 path_planning/ 会造成 `path_planning → task_allocation`
   反向依赖，违反 §2 规则 1/4；
2. Lu 等 (2026) 框架口径：manager 层（分配）与 UAV 层（规划）解耦，闭环是两层之上的
   第三构件（framework-level integration，event-triggered update–reassign–replan loop）；
3. 章节归属 ≠ 代码归属：机制叙述在第 4 章 §4.4；下层侧实现（事件检测/代价回填）在
   `path_planning/interface/`，环驱动在根 `closed_loop/`；
4. exp04/exp05 联合仿真编排根 closed_loop/ 即可，两个方法包保持互相零 import。

交换物（C(i,j,k) 增量回填等）与时序协议详见根 `closed_loop/README.md`。

## 5. 命名与工程惯例（对齐 task_allocation/）

| 惯例 | 说明 |
|------|------|
| 包名全小写 | `methods/TPR` → `methods/tpr`（与 `methods/muas` 一致） |
| 门面只编排 | `offline/pipeline.py`、`online/pipeline.py`——仿 `muas_stage.py`：门面不堆细节，模块地图写进 docstring |
| 注册表 | `baselines/registry.py`（与 `task_allocation/baselines/registry.py` 同模式） |
| 实验目录 | `experiments/expNN_name/`，run.py 只编排，config/metrics/plot 分离（沿用 exp01_smoke） |
| 产物落盘 | `data/processed/` 带参数指纹，禁手改（见 offline/network_io.py） |
| 文档即口径 | 与第 3 章 `Chapter3_README.md` 一样，实现落地后 README/模块 docstring 必须与代码逐条对齐 |

## 6. 目录 ↔ 论文/README 映射

| 论文/README 小节 | 目录 |
|------------------|------|
| Lin2021 §2.2.1 控制网络建立 | `offline/control_points.py`、`offline/link_builder.py`、`offline/virtual_network.py` |
| Lin2021 §2.2.2 链路时间评估 | `cost/link_cost.py` |
| Lin2021 §2.3 寻路 + Dubins 平滑 | `online/route_search.py`、`online/dubins_smoother.py` |
| Lin2021 §2.4 时间最优轨迹 | `online/time_optimal.py` |
| README §4.2 基线与局限 | `baselines/lin_original.py` |
| README §4.3.1 禁飞约束建模 | `constraints/nofly_zones.py`、`online/dynamic_prune.py` |
| README §4.3.2 TPR 风险与风险场 | `methods/tpr/` |
| README §4.3.3 综合代价函数 | `cost/composite.py` |
| README §4.3.4 二次碰撞校验 | `online/post_check.py` |
| README §4.3.5 离线-在线工作流程 | `offline/pipeline.py`、`online/pipeline.py` |
| README §4.4 闭环机制 | `interface/`、`cost/cost_matrix.py` |
| README §4.5 实验 | `experiments/` |
| 可见性图/稀疏路图（仅几何约束、放弃 TPR、选项 A 动态门） | `methods/visibility_graph/` |
