# -*- coding: utf-8 -*-
"""L3 对外契约层：TrajectoryPlanner 门面 + 事件/反馈协议。

约定：
1. 上层任务分配（task_allocation/）与根 closed_loop/ 只经本层交互，
   不得直接 import methods/ 内部模块；
2. 本包同时是**契约类型再导出出口**：ODRequest / PlanResult / CostMatrix /
   CostUpdate / TriggerEvent / InfeasibilityReason（源于 common/ 与
   methods/cost/cost_matrix），外部只 import 本包取类型——交换类型由产出方
   定义（下层产物归 path_planning，上层产物归 task_allocation），
   closed_loop/protocol 只引用、不重复定义，杜绝反向依赖。
"""
