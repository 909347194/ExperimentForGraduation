# -*- coding: utf-8 -*-
"""上层侧适配器：把 task_allocation 的求解入口包成 AllocatorPort。

职责：统一接口形态——

    initial_assign(tasks, fleet) -> ODRequest 集合
    reassign(cost_update, events) -> NewODSet（换巢 / 改派 / 推迟）

隔离 task_allocation 内部演进对环驱动的影响；重调度策略本身留在上层实现
（task_allocation/methods/rolling），本模块只做调用与结果收集。

不负责：任何分配算法。
"""
