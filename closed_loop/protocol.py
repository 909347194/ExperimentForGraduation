# -*- coding: utf-8 -*-
"""跨层消息契约（集成层的数据结构）。

职责：定义闭环两侧交换的全部消息类型与字段口径，与 Lu 等 (2026)
event-triggered update–reassign–replan 接口逐项对应：

    ODRequest 集合      上层 → 下层（起点=当前巢；终点=任务点/候选终点巢）
    PlanResult           下层 → 上层（route/path/trajectory/总时间/总 TPR/可行性）
    CostMatrix C(i,j,k)  下层 → 上层（真实综合代价；不可行 = ∞ + InfeasibilityReason）
    CostUpdate           事件触发的**增量**回填（行/列粒度——借鉴 Lu2026 Algorithm 2
                         的行/列增量语义，不做全量重算）
    ReassignRequest      环内：重调度请求（换巢 / 改派 / 推迟）
    NewODSet             上层 → 下层：重调度后的新 OD 集合

约定：只定义数据结构与字段口径，不含处理逻辑；能复用
path_planning/common/types 的类型（ODRequest、PlanResult、事件枚举）就复用，不重复定义。

不负责：消息的产生与消费（path_planning/interface/feedback.py、orchestrator.py、adapters/）。
"""
