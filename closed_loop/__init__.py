# -*- coding: utf-8 -*-
"""跨层集成层：任务分配 × 轨迹规划 的事件触发闭环（update–reassign–replan）。

不含算法——只做两侧编排与消息契约。依赖方向：

    closed_loop → path_planning/interface/ + task_allocation 公开入口
    task_allocation 与 path_planning 互不 import，且均不得 import 本包

协议与决策记录见 README.md（对应 Lu 等 2026 的 framework-level integration）。
"""
