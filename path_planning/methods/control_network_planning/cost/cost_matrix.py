# -*- coding: utf-8 -*-
"""三维代价矩阵 C(i,j,k) —— 下层对上层的交换物（README §4.4.1）。

职责：(无人机 i, 任务 j, 机巢 k) → 真实综合代价；不可行组合置 +∞ 并携带
InfeasibilityReason。提供 fill / update 语义供 interface 侧回填。

不负责：事件判定与反馈消息组装（interface/feedback.py）；矩阵的消费方是
上层动态滚动任务分配（task_allocation/methods/rolling）。
"""
