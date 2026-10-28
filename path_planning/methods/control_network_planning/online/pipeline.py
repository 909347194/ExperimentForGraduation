# -*- coding: utf-8 -*-
"""在线阶段编排门面（README §4.3.5「在线规划阶段」）。

职责：只做编排——

    (net, OD, 动态禁飞) → dynamic_prune → route_search → dubins_smoother
                        → post_check → time_optimal → PlanResult

PlanResult 聚合：route / path / trajectory、总飞行时间、总 TPR 风险、可行性标记与原因。
"""
