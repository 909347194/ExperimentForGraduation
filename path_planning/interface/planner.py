# -*- coding: utf-8 -*-
"""对上层的唯一入口：PathPlanner 闭环门面（README §4.4）。

基于 visibility_graph（第 4 章正文方法，已验证）实现，取代原 control_network_planning
的对应实现（已弃用/存档，其空桩不再补全）。

本门面是**路径规划器（path planner）**：交付物是几何可行路径（path，可平滑），
不生成、不 claim 时间最优轨迹（trajectory）；后者由下游 UAV 飞行控制器完成，
不在本研究范围内。匀速时间占位仅作可视化时间色标的辅助，非 claimed 轨迹。

对上层（exp04_closed_loop / exp05_joint_sim 滚动调度）只暴露：

    PathPlanner.from_env(env, params=None)
        离线建图一次 → 持有 VisibilityGraph（图只建一次、长期复用，选项 A 核心优势）
    .plan(od: ODRequest) -> PlanResult
        单 OD 求解（委托 VisibilityGraph.plan，含平滑；返回 route + path）
    .update_dynamic_nofly(zones) -> None
        滚动窗口下发新的临时禁飞区（图不变，查询期按时间窗生效，选项 A）
    .build_cost_matrix(pairs) -> list[PlanResult]
        按 (无人机, 任务, 机巢) 组合批量规划并构建 C(i,j,k)
    .update_cost_matrix(matrix, results) -> list[PlanResult]
        事件触发后回填真实代价 / 不可行置 ∞

坐标口径：OD 坐标系须与上层 common/scenario_data.py 一致（本地笛卡尔 NED，见 common/scenario.py）。

不负责：场景解析（common/scenario）、建图与搜索细节（methods/visibility_graph）、
轨迹生成（time-optimal trajectory 不在本研究范围内，交给下游飞控）。
"""

from __future__ import annotations

from typing import Optional, Sequence

from path_planning.common.types import (
    DynamicNoFly,
    ODRequest,
    PlanResult,
)
from path_planning.methods.visibility_graph import (
    VisibilityGraph,
    VisibilityGraphParams,
    build_visibility_graph,
)


class PathPlanner:
    """基于可见性图的路径规划门面（闭环对接点，交付物为 path）。"""

    def __init__(self, graph: VisibilityGraph):
        self.graph = graph
        self.build_stats: dict = getattr(graph, "meta", {}) or {}

    # ── 构造 ───────────────────────────────────────────────────────────────

    @classmethod
    def from_env(
        cls,
        env,
        params: Optional[VisibilityGraphParams] = None,
        verbose: bool = True,
    ) -> "PathPlanner":
        """由 CityEnvironment 离线建图一次，返回门面。

        params=None 时使用 VisibilityGraphParams 默认参数（精细验证口径）；
        全城/示意性大图应传入调粗后的参数（见 experiments/exp01_smoke/full_city.py）。
        """
        if params is None:
            params = VisibilityGraphParams()
        graph, stats = build_visibility_graph(env, params, verbose=verbose)
        obj = cls(graph)
        obj.build_stats = stats
        return obj

    # ── 单 OD 规划 ─────────────────────────────────────────────────────────

    def plan(self, od: ODRequest) -> PlanResult:
        """单 OD 求解 → PlanResult（route + path 为交付物；trajectory 仅匀速 ETA 辅助）。"""
        return self.graph.plan(
            od.origin, od.dest, depart_time=od.t_dep,
            smooth=True, smooth_method="chaikin",
        )

    # ── 滚动窗口：动态禁飞下发 ─────────────────────────────────────────────

    def update_dynamic_nofly(self, zones: Sequence[DynamicNoFly]) -> None:
        """滚动窗口下发新的临时禁飞区。

        选项 A：图（建筑 + 静态禁飞）不变，动态禁飞仅追加到查询期动态场，
        按到达时刻窗逐边判定 —— 图无需重建，因此滚动成本低。
        """
        for z in zones:
            self.graph.nofly.dynamic.append(z)
        if zones:
            self.graph.nofly.has_dynamic = True

    # ── 代价矩阵（闭环 C(i,j,k) 交换物）────────────────────────────────────

    def build_cost_matrix(self, pairs: Sequence[tuple]) -> list[PlanResult]:
        """按 (origin, dest, t_dep) 组合批量规划，返回与 pairs 对齐的 PlanResult 列表。

        pairs 元素含义：每个元素是 (origin, dest, t_dep) 三元组，或 ODRequest。
        （CostMatrix 的 (i,j,k) → (无人机,任务,机巢) 组合由上层组装；此处只负责批量 plan。）
        """
        results: list[PlanResult] = []
        for item in pairs:
            if isinstance(item, ODRequest):
                results.append(self.plan(item))
            else:
                o, d, t = item
                results.append(self.graph.plan(o, d, depart_time=float(t)))
        return results

    def update_cost_matrix(
        self,
        matrix: Sequence[PlanResult],
        results: Sequence[PlanResult],
    ) -> list[PlanResult]:
        """事件触发后回填：可行 → 保留真实 PlanResult；不可行 → 标记 infeasible。

        当前可见性图方法为无重调度回路的静态规划，故此处为「就地更新可行性标记」
        的占位实现（真实的代价回填由上层闭环协议驱动，不在本门面内）。
        """
        out = list(matrix)
        for i, r in enumerate(results):
            if i < len(out):
                out[i] = r
        return out
