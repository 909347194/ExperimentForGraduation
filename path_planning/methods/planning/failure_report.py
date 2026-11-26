# -*- coding: utf-8 -*-
"""失败记账：把一次失败压成"能自查、能画图"的两份材料。

职责：
    * ``failure_detail``：一行 detail（断在第几段、哪类成因、卡在哪），
      写进 ``PlanResult.detail`` / ``loop_trace.json``，事后不必重跑规划；
    * ``prefix_report``：失败航次的几何报告（已解前缀折线 + 断段初值），
      供 ``failures.json`` 与 PP01 绘图区分"飞成的段"与"断在哪"。

边界：**只报告、不交付**。``PlanResult.trajectory`` 仍为 ``None``，prefix 不当作
      交付物，也不删点——丢点是上层（task_allocation）职权，见仓库根 README §4.1。
相关：``planning/tour_planner.py`` 的失败分支；``orchestration/adapters/planner_side.py``
      的 ``_jsonable_diag``（``prefix_nodes`` / ``failed_leg_path`` 按点保留）。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from path_planning.common.plan_types import HermiteSegment, PlanStop


def failure_detail(index: int, stop: Any, leg_diag: dict[str, Any]) -> str:
    """把航段失败压成一行可读 detail：带"哪一段、哪类成因、卡在哪"。

    契约只要求 detail 非空；这里刻意塞进排查必需的三四个数字，让
    ``loop_trace.json`` 不必回头重跑规划也能判断下一步查什么。
    """
    base = f"第 {index} 段（→ stop={stop.id}）规划失败"
    broken = leg_diag.get("rrt_no_path") or {}
    if not broken:
        return base
    gen = broken.get("reason", "")
    closest = broken.get("closest_to_goal_min_m")
    parts = [base, gen]
    if broken.get("start_sdf_m") is not None:
        # 端点被判不自由时，把"实测 vs 实际阈值"写进 detail：一眼看出是点位贴楼还是阈值偏严
        parts.append(
            "起点净空 {:.2f}/{:.2f} m、终点净空 {:.2f}/{:.2f} m（实测/阈值）".format(
                float(broken["start_sdf_m"]), float(broken["start_threshold_m"]),
                float(broken["goal_sdf_m"]), float(broken["goal_threshold_m"]),
            )
        )
    if closest is not None:
        parts.append(f"树距目标最近 {float(closest):.1f} m")
    parts.append(
        "节点≤{}｜净空拒 {}｜窗界拒 {}｜步长拒 {}".format(
            int(broken.get("nodes_max", 0)),
            int(broken.get("reject_clearance", 0)),
            int(broken.get("reject_window", 0)),
            int(broken.get("reject_steer", 0)),
        )
    )
    return "｜".join(p for p in parts if p)


def prefix_report(
    segments: list[HermiteSegment],
    index: int,
    stop: PlanStop,
    leg_diag: dict[str, Any],
    cursor: float,
) -> dict[str, Any]:
    """失败航次的几何报告：已解好的前缀折线 + 失败段的空间初值。

    ``completed_prefix_len`` = 断点前已解好的段数（= 已到达的站数），
    ``failed_stop_index`` 是断掉的那一段的目标站在航次里的序号。两者都只是
    **报告**：不交付 prefix、不删点、不让本航次变成部分成功。

    ``fallback_path`` 是空间层已经判过无碰撞的折线（只是时间与净空没解出来），
    正好当"不可行段"的示意几何；它不进交付、不进净空记账。
    """
    flights = [s for s in segments if not s.is_hover]
    nodes: list[list[float]] = []
    if flights:
        nodes = [[round(float(v), 2) for v in flights[0].p0]]
        nodes += [[round(float(v), 2) for v in s.p1] for s in flights]
    path = leg_diag.get("fallback_path")
    failed_path = (
        None if path is None
        else [[round(float(v), 2) for v in row] for row in np.asarray(path, dtype=float)]
    )
    return {
        "completed_prefix_len": int(index),
        "failed_stop_index": int(index),
        "failed_stop_id": int(stop.id),
        "prefix_end_t": round(float(cursor), 3),
        "prefix_nodes": nodes,
        "failed_leg_path": failed_path,
    }
