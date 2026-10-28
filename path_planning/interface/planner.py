# -*- coding: utf-8 -*-
"""对上层的唯一入口：TrajectoryPlanner 门面（README §4.4 闭环接口）。

拟接口：

    TrajectoryPlanner(network_path, cfg)
        加载带参数指纹的扩展控制网络；指纹不符直接报错
    .plan(od: ODRequest, dynamic_nofly=None) -> PlanResult
        单 OD 求解（委托 online/pipeline）
    .update_dynamic_nofly(zones) -> None
        滚动窗口下发新的临时禁飞区（委托 online/dynamic_prune 的剪枝视图）
    .build_cost_matrix(pairs) -> CostMatrix
        按 (无人机, 任务, 机巢) 组合批量规划并构建 C(i,j,k)（README §4.4.1）
        （CostMatrix 定义于 methods/cost/cost_matrix.py，经本门面导出）
    .update_cost_matrix(matrix, results) -> CostMatrix
        事件触发后回填真实代价 / 不可行置 ∞

坐标口径：OD 坐标系须与上层 task_allocation/common/scenario_data.py 一致
（NED / 本地笛卡尔，见 common/scenario.py）。
"""
