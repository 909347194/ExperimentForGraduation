# -*- coding: utf-8 -*-
"""环驱动器：update–reassign–replan 循环的调度主体（Lu2026 Fig.2 的 loop 本体）。

职责（拟接口）：

    LoopDriver(planner: PlannerPort, allocator: AllocatorPort, cfg)
    .run(horizon) -> LoopTrace
        每个滚动周期：
          ① 取上层 OD 集 → 下层规划（plan / build_cost_matrix）
          ② 事件检测（interface/feedback.detect_events）
          ③ 代价增量回填（update_cost_matrix → CostUpdate，不可行置 ∞+原因）
          ④ 触发事件非空 → 上层增量重调度（reassign）→ NewODSet
          ⑤ 终止判定：任务全部完成 / 推迟出视野 / 达滚动上限
    .tick() / .on_event(...)   供实验做细粒度单步驱动（exp04 闭环验证用）

不负责：两侧算法（task_allocation/methods/、path_planning/methods/）；
消息字段定义（protocol.py）；两侧差异适配（adapters/）。
"""
