# -*- coding: utf-8 -*-
"""事件触发闭环的下层侧（README §4.4.2-4.4.3）。

拟接口：

    detect_events(result: PlanResult, cfg) -> List[TriggerEvent]
        三类触发事件：动态禁飞阻断 / TPR 超阈值 / 滚动周期到达
    build_feedback(results, events) -> CostUpdate
        可行任务 → 回填真实综合代价；不可行的 (i,j,k) 组合 → 置 ∞ 并携带原因
        CostUpdate 定义于 common/types.py（产出方定义），经本包再导出；
        不在 closed_loop/protocol 重复定义，避免 path_planning → closed_loop 反向依赖

不负责：上层重调度策略（更换机巢/改派/推迟，属 task_allocation/methods/rolling）。
"""
