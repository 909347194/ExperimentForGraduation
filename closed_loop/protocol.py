# -*- coding: utf-8 -*-
"""跨层消息契约（集成层的数据结构）。

职责：定义闭环交换的消息类型与字段口径，与 Lu 等 (2026) 的 event-triggered
update–reassign–replan 接口逐项对应。**类型归属三分法（防反向依赖）**：

1. 下层契约类型 —— 产出方定义、经 path_planning.interface 再导出，本包**只引用不重复定义**：
   ODRequest（入参）、PlanResult、CostMatrix C(i,j,k)、CostUpdate（行/列粒度增量回填，
   借鉴 Lu2026 Algorithm 2）、TriggerEvent / InfeasibilityReason；
2. 上层契约类型 —— 同理由 task_allocation 公开入口定义/导出，本包只引用；
3. 环内消息 —— 由本包定义：ReassignRequest（重调度请求：换巢/改派/推迟）、
   NewODSet（重调度结果，即新 ODRequest 集合）。

约定：只定义数据结构与字段口径，不含处理逻辑。
不负责：消息的产生与消费（path_planning/interface/feedback.py、orchestrator.py、adapters/）。
"""
