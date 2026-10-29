# path_planning 模块地图：职责定义与拆分

> 逐模块的职责、输入→输出、边界与论文章节映射。分层与依赖规则见 [architecture.md](architecture.md)。
> 禁飞区时空扩展的算法设计（阶段一：可行性约束，TPR 权重 0）见 [design_nofly_zones.md](design_nofly_zones.md)。
> 本模块地图覆盖两类已着手实现的方法：`control_network_planning/`（第 4 章正文方法，含 TPR 与闭环）
> 与 `visibility_graph/`（放弃 TPR、仅几何约束的替代性预研，由 exp02_vg 验证）。文件 docstring 以代码为准；
> 若拆分有变，先改本文再动代码。

## 1. 拆分原则

1. **一条编排线只做编排**：`offline/pipeline.py`、`online/pipeline.py` 不承载算法细节。
2. **模型与算法分离**：`constraints/`、`cost/`、`tpr/` 回答"是什么、代价多少"；
   `offline/`、`online/` 回答"怎么算"。
3. **一个几何内核、两个入口**：碰撞/禁飞判定唯一收口在 `constraints/query.py`——
   无时间入口 `link_feasible`（建筑/静态永久禁飞）、带时间入口 `edge_occupancy_free`
   （动态禁飞，按时窗端点切段 + 每段静态求交），二者共用同一膨胀体与求交几何。
4. **每个文件可独立测试**：见 [../tests/README.md](../tests/README.md) 的测试规划——
   若一个文件无法单测，说明拆分没到位。
5. **docstring 三要素**：职责 / 输入→输出 / 不负责什么（+ 论文映射）。

## 2. 模块清单

### 2.1 common/（L0 领域模型）

| 文件 | 职责 | 输入 → 输出 | 不负责 | 映射 |
|------|------|-------------|--------|------|
| `types.py` | 核心类型：MotionState(0..6)、ControlPoint、VirtualLink、ExpandedNetwork、Route/Path/Trajectory、ODRequest、PlanResult | — | 算法、IO | Lin2021 §2.1 |
| `config.py` | UAVParams / OfflineParams / OnlineParams / RiskParams | — | 文件解析 | Lin2021 §3.1、README §4.3 |
| `events.py` | TriggerEvent（3 类）、InfeasibilityReason 枚举 | — | 事件检测逻辑 | README §4.4.2 |
| `scenario.py` | 原始数据解析 → CityEnvironment（建筑 cuboid 化、DEM、人口栅格、禁飞区） | data/raw → CityEnvironment | 几何判定、布点 | Lin2021 §2.2.1、README §4.3.1 |

### 2.2 utils/（L1 通用工具）

| 文件 | 职责 | 输入 → 输出 | 不负责 | 映射 |
|------|------|-------------|--------|------|
| `geometry.py` | 线段-平面/长方体求交、圆弧-障碍距离 | 几何元素 → 判定/距离 | 领域对象 | Lin2021 Fig.6 |
| `math3d.py` | 位姿矩阵、3D→2D 投影、曲率半径估计 | 向量/点列 → 变换 | 业务语义 | Lin2021 Eq.(5)、§2.4 |

### 2.3 methods/tpr/（L2 第三方地面风险）

| 文件 | 职责 | 输入 → 输出 | 不负责 | 映射 |
|------|------|-------------|--------|------|
| `crash_model.py` | 坠毁概率 | 弧段+飞行状态 → p_crash | 暴露/时变 | README §4.3.2 |
| `ground_exposure.py` | 地面暴露人口/财产 | 链路+人口栅格 → E | 时变切换 | README §4.3.2 |
| `risk_field.py` | 昼/夜时变风险场、链路 TPR 预计算/查询 | (p_crash, E) → TPR(link) | 加权合成 | README §4.3.2 |

### 2.4 methods/control_network_planning/constraints/（L2 约束模型）

| 文件 | 职责 | 输入 → 输出 | 不负责 | 映射 |
|------|------|-------------|--------|------|
| `buildings.py` | 建筑 cuboid 碰撞查询 | 点/线段/圆弧 → bool | 禁飞区 | Lin2021 §2.2.1 |
| `nofly_zones.py` | 静态永久/动态临时禁飞模型与相交查询 | 同上 → bool | 在线剪枝 | README §4.3.1 |
| `query.py` | 统一 CollisionQuery：link/point/arc feasibility | 三类查询 → bool | 策略与迭代 | README §4.3.4 |

### 2.5 methods/control_network_planning/cost/（L2 代价模型）

| 文件 | 职责 | 输入 → 输出 | 不负责 | 映射 |
|------|------|-------------|--------|------|
| `link_cost.py` | 4 种虚拟链路情形最小飞行时间（Eq.1-4），含动力学加减速与限速 | VirtualLink → time | TPR、加权 | Lin2021 §2.2.2 |
| `composite.py` | 时间-风险加权综合代价 | (time, TPR, λ) → cost | 分量计算 | README §4.3.3 |
| `cost_matrix.py` | C(i,j,k) 结构、fill/update（不可行 → ∞+原因） | 组合 → 代价 | 事件判定 | README §4.4.1 |

### 2.6 methods/control_network_planning/offline/（L2 离线线）

| 文件 | 职责 | 输入 → 输出 | 不负责 | 映射 |
|------|------|-------------|--------|------|
| `control_points.py` | 布点（d1/d2/d3、禁飞缓冲外、虚拟点补设） | CityEnvironment → [ControlPoint] | 连线 | Lin2021 Fig.5 |
| `link_builder.py` | d* 截断 + 碰撞/静态禁飞剪枝 → 原始网络 | [ControlPoint] → 控制网络 | 虚拟扩展 | Lin2021 Fig.6 |
| `virtual_network.py` | 7 状态虚拟节点、4 对虚拟连线 | 控制网络 → ExpandedNetwork | 代价 | Lin2021 Fig.8-9 |
| `precompute.py` | 批量回填时间 + 昼/夜 TPR | ExpandedNetwork → 带代价网络 | 公式、落盘 | README §4.3.5 |
| `network_io.py` | 序列化/加载 + 参数指纹校验 | 网络 ↔ data/processed | 构建 | README §4.3.5 |
| `pipeline.py` | 离线编排门面 | data/raw → 产物 | 算法细节 | README §4.3.5 |

### 2.7 methods/control_network_planning/online/（L2 在线线）

| 文件 | 职责 | 输入 → 输出 | 不负责 | 映射 |
|------|------|-------------|--------|------|
| `dynamic_prune.py` | 动态禁飞剪枝视图（乐观/保守）+ 连通性校验，撤销即恢复 | (网络, 禁飞) → 剪枝视图 | 禁飞数据模型 | README §4.3.1 |
| `spacetime.py` | 时空网络扩展（**本阶段暂不实现，占位保留**：事件驱动时间轴 + 多标签） | 空间网络 → 时空图 | 判定/搜索 | design_nofly_zones §10/§11 |
| `route_search.py` | 综合代价 Dijkstra；网络视图迭代修正 L1/L2/L2.5 | (网络, OD) → Route | 代价定义、复核 | Lin2021 §2.3 |
| `dubins_smoother.py` | 3D Dubins 平滑 + 过近点偏差收缩 | Route → Path | 碰撞正确性 | Lin2021 §2.3 |
| `post_check.py` | 二次碰撞校验 + 偏移修正迭代 | Path → 可行 Path / 不可行原因 | 弧段生成 | README §4.3.4 |
| `time_optimal.py` | 时间最优轨迹（前向推进+反向回溯） | Path → Trajectory | 几何、TPR | Lin2021 §2.4 |
| `pipeline.py` | 在线编排门面 → PlanResult | (网络, OD, 禁飞) → PlanResult | 算法细节 | README §4.3.5 |

### 2.8 interface/（L3 对外契约）

| 文件 | 职责 | 输入 → 输出 | 不负责 | 映射 |
|------|------|-------------|--------|------|
| `planner.py` | TrajectoryPlanner 门面：plan / update_dynamic_nofly / build_cost_matrix / update_cost_matrix | OD → PlanResult、组合 → C(i,j,k) | 重调度 | README §4.4 |
| `feedback.py` | 事件检测 + 反馈组装（真实代价 / ∞+原因） | PlanResult → TriggerEvent/CostUpdate | 上层策略 | README §4.4.2-4.4.3 |

### 2.9 baselines/（对照）

| 文件 | 职责 | 输入 → 输出 | 不负责 | 映射 |
|------|------|-------------|--------|------|
| `registry.py` | 对照方法注册/获取 | 名称 → 方法 | — | 与 ch3 同模式 |
| `lin_original.py` | Lin2021 原始基线（仅建筑+仅时间） | OD → PlanResult | 本章改进项 | README §4.2 |
| `euclidean_cost.py` | 欧氏距离代价预估（开环对照口径） | OD → 估价 | 真实规划 | README §4.5.4 |

### 2.10 methods/visibility_graph/（L2 可见性图/稀疏路图规划器，替代方案）

> 与控制网络方法**相互独立、不共享代码**：仅依赖 common/scenario 与 utils/geometry。放弃 TPR，
> 仅用几何约束（建筑 + 禁飞区）；动态禁飞按**选项 A** 在查询期按时间窗检查（图只建一次、长期复用）。由 exp02_vg 验证。

| 文件 | 职责 | 输入 → 输出 | 不负责 | 映射 |
|------|------|-------------|--------|------|
| `params.py` | VisibilityGraphParams（简化模式/节点密度/边长/机动保护区净空/巡航速度） | dict → 参数 | 算法 | exp02_vg |
| `obstacles.py` | `SimplifiedObstacles`（建筑外扩+简化+STRtree）+ `NoFlyField`（静态并入/动态时间窗门） | CityEnvironment → 查询对象 | 节点/边生成 | exp02_vg |
| `nodes.py` | 节点生成（轮廓采样 + 离散高度层 + 网格骨干 + 预算填充，剔除障碍/禁飞内落点） | env/params/障碍 → (nodes, meta, stats) | 边/搜索 | exp02_vg |
| `edges.py` | 边连接（网格哈希邻域 + LOS + 离线碰撞 + 机动保护区 prune + 竖向边） | nodes/障碍/参数 → 邻接表 | 搜索 | exp02_vg |
| `search.py` | 堆优化 Dijkstra/A*（三维欧氏代价，支持动态时间窗门） | 邻接表/节点/起终点 → 路径/代价 | 图构建 | exp02_vg |
| `pipeline.py` | `VisibilityGraph` 门面：`build_visibility_graph` / `plan(origin, dest, depart_time)` | env/params → 图；OD → poly/cost | 场景解析 | exp02_vg |

## 3. 关键接口契约（实现时按此签名，改动先改本文）

```python
# offline/pipeline.py
def build_network(env: CityEnvironment, cfg: OfflineParams) -> Path:   # 返回产物路径

# online/pipeline.py
def plan_od(net: ExpandedNetwork, od: ODRequest,
            dyn_nofly: DynamicNoFlyView, cfg: OnlineConfig) -> PlanResult

# interface/planner.py
class TrajectoryPlanner:
    def __init__(self, network_path: str, cfg: Config): ...
    def plan(self, od: ODRequest, dynamic_nofly=None) -> PlanResult: ...
    def update_dynamic_nofly(self, zones) -> None: ...
    def build_cost_matrix(self, pairs: list[tuple[int, int, int]]) -> CostMatrix: ...
    def update_cost_matrix(self, matrix: CostMatrix, results: list[PlanResult]) -> CostMatrix: ...

# interface/feedback.py
def detect_events(result: PlanResult, cfg: Config) -> list[TriggerEvent]: ...
def build_feedback(results: list[PlanResult], events: list[TriggerEvent]) -> CostUpdate: ...
# ↑ CostUpdate 定义于 common/types.py（产出方定义，经 interface 再导出）；
#   closed_loop/protocol 只引用，不重复定义（architecture.md 依赖规则 8）

# constraints/query.py
class CollisionQuery:
    def link_feasible(self, p: Vec3, q: Vec3, *, include_dynamic: bool) -> bool: ...
    def point_feasible(self, p: Vec3) -> bool: ...
    def arc_feasible(self, arc: Arc, *, include_dynamic: bool) -> bool: ...
```

`PlanResult` 必含字段：`route`、`path`、`trajectory`、`total_time`、`total_tpr`、
`feasible: bool`、`infeasible_reason: InfeasibilityReason | None`、`events: list[TriggerEvent]`。

## 4. 实验映射（README §4.5）

| 实验目录 | 论文小节 | 依赖模块 | 对照臂 |
|----------|----------|----------|--------|
| `exp01_smoke` | —（冒烟） | 全链路最小配置 | — |
| `exp02_ablation` | §4.5.2 消融 | online 全链 + tpr | 完整 vs 去禁飞/去 TPR/去二次校验 vs lin_original |
| `exp03_sensitivity` | §4.5.2 灵敏度 | offline 全链 | d3 / d* / d1 / λ / 阈值扫描 |
| `exp04_closed_loop` | §4.5.3 闭环验证 | interface + task_allocation/rolling + 根 closed_loop | 闭环 vs 开环 |
| `exp05_joint_sim` | §4.5.4 联合仿真 | 全部 | 闭环真实代价 vs 欧氏预估开环 |
| `exp02_vg` | （可见性图替代方案验证，非 §4.5.2 消融） | methods/visibility_graph + common/scenario | 固定节点集 保护区开/关 + 直线基线 + 独立碰撞审计 + 动态时间窗演示 |
