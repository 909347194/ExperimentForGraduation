# -*- coding: utf-8 -*-
"""建场契约的请求期准入：单段 OD 不得超过建场时的长度上限。

职责：在请求进流水线之前，检查起点→各停靠点的**水平**长度是否超过
      ``TourPlanningContext.max_od_length_m``；超了给可读理由，由 ``plan_tour``
      转成 ``OD_TOO_LONG`` 结果码。
边界：不建走廊、不跑 RRT、不碰 NLP；pad 的派生在
      ``methods.spatial.corridor_window.required_pad_m``，两处共用同一个上限。

为什么必须在这里拦：建场时按该上限给最坏走廊窗口留了外扩，超过这个长度的段
会让 ``corridor_window`` 抛 ``WindowOutsideField``（内部异常，上层翻译不了）。
请求期拒成业务码，上层才知道该拆站/换巢，而不是整个实验崩掉。
"""

from __future__ import annotations

import math
from typing import Sequence

__all__ = ["od_violation_of"]


def od_violation_of(
    start_position: Sequence[float],
    stops: Sequence,
    max_od_length_m: float | None,
) -> str | None:
    """返回首个超长段的说明；``None`` 表示合法（或上下文未声明契约）。

    ``stops`` 只需提供 ``id`` 与 ``position``，不依赖 ``PlanStop`` 类型，
    免得本模块与 ``tour_planner``（持有 ``TourPlanningContext``）循环导入。
    """
    if max_od_length_m is None:
        return None
    limit = float(max_od_length_m)
    if not math.isfinite(limit) or limit <= 0.0:
        raise ValueError(f"max_od_length_m 必须为正：{max_od_length_m}")
    px, py = float(start_position[0]), float(start_position[1])
    for i, stop in enumerate(stops, 1):
        pos = stop.position
        sx, sy = float(pos[0]), float(pos[1])
        length = math.hypot(sx - px, sy - py)
        if length > limit + 1e-6:
            return (
                f"第 {i} 段（终点 id={stop.id}）水平 OD {length:.1f} m 超过建场契约 "
                f"max_od_length_m={limit:.1f} m：走廊窗口将超出全域场；"
                "请拆段/换终点巢，或调大契约上限并重建场"
            )
        px, py = sx, sy
    return None
