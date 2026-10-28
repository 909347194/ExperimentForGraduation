# -*- coding: utf-8 -*-
"""下层侧适配器：把 path_planning.interface.TrajectoryPlanner 包成 PlannerPort。

职责：统一接口形态——

    plan(od, dynamic_nofly) -> PlanResult
    build_cost_matrix(pairs) -> CostMatrix
    update_cost_matrix(matrix, results) -> CostUpdate
    update_dynamic_nofly(zones)

隔离 path_planning 内部演进对环驱动的影响；坐标口径若与上层有差异在此收口
（正常应一致，见 path_planning/common/scenario.py）。

不负责：任何规划算法（属 path_planning/methods/）。
"""
