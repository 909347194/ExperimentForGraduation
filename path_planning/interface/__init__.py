# -*- coding: utf-8 -*-
"""L3 对外契约层：TrajectoryPlanner 门面 + 事件/反馈协议。

约定：上层任务分配（task_allocation/）与根 closed_loop/ 只经本层交互，
不得直接 import methods/ 内部模块。
"""
