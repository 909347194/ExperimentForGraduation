# -*- coding: utf-8 -*-
"""闭环事件与不可行原因枚举（L0，下层↔上层的交换词汇表）。

拟枚举：

    TriggerEvent        DYNAMIC_NOFLY_BLOCK   动态禁飞造成路径不可行
                        TPR_EXCEEDED          轨迹 TPR 超出安全阈值
                        ROLLING_TICK          滚动窗口调度周期到达
                        —— 对应 README §4.4.2 三类触发事件
    InfeasibilityReason NO_CONNECTIVITY       动态禁飞剪枝后 OD 不连通
                        NO_ROUTE              网络上无可行 route
                        SMOOTH_INFEASIBLE     平滑后二次校验迭代超上限
                        TPR_EXCEEDED          可行但风险超阈值

不负责：事件检测与反馈组装逻辑（interface/feedback.py）。
"""
