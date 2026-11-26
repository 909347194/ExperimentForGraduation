# -*- coding: utf-8 -*-
"""降级交付审计（本实验私有）：估算时间航迹必须"带码、几何安全、被隔离"。

为什么单列一个文件：降级这一路只对本实验的一条航次开放（机7 的预算逼死），
判据也只有三条，塞进 ``audit_checks`` 会把主审计的"逐周期通用项"读乱。

三条判据（缺一即红）：
1. 带码：``cycle_trace`` 的航次里必须有 ``failure``——估算时间不是优化结果，
   没有失败码的 estimate 就是"偷偷降级"；
2. 几何安全：交付闸（``global_gate``）必须 PASS。时间可以估，几何不许估；
3. 被隔离：不进协调（``changed_by_coordination`` 为假）、不写 Registry
   （``registry_busy_until`` 无此机号）。
"""

from __future__ import annotations

from typing import Any

from .audit_checks import Check

__all__ = ["degraded_delivery_check"]


def degraded_delivery_check(
    r_idx: int, uid: int, traj: Any, trace: Any, solution: Any
) -> Check:
    """一条 estimate 航迹的降级审计。"""
    ticket = next(
        (t for c in trace["cycles"] if int(c["cycle"]) == r_idx
         for t in c["tickets"] if int(t["uav_id"]) == uid),
        None,
    )
    failure = None if ticket is None else ticket.get("failure")
    gate_ok = ticket is not None and bool(ticket["gate"]["ok"])
    busy = (solution["cycles"][str(r_idx)].get("registry_busy_until") or {}).get(str(uid))
    isolated = (
        traj.meta.get("degraded") is True
        and not bool((ticket or {}).get("changed_by_coordination"))
        # Registry 对未登记机号返回 0.0：0 / None 都算"没写进去"
        and (busy is None or float(busy) <= 0.0)
    )
    return Check(
        f"降级交付/周期{r_idx}/机{uid}",
        failure is not None and gate_ok and isolated,
        f"失败码={failure}（estimate 必须有码）、几何闸="
        f"{'PASS' if gate_ok else 'FAIL'}、隔离={'是' if isolated else '否'}"
        f"（不进协调、不写 Registry）",
    )
