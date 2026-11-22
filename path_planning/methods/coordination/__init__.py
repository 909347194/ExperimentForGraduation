# -*- coding: utf-8 -*-
"""协调层：局部域 → 让路候选 → 局部重规划 → 全局复检。

职责：在"单机几何+时间都已解好"的既有航迹上，做**只改空间**的多机让路：定局部域、
      生成带权重衰减的候选几何、逐候选重解时间并复核、再全对复检直到清零。
边界：不做单机初规划（``planning``）、不写 Registry、不优化出发时刻与预设速度。
相关：``docs/conflict_model.md`` §10/§11/§12、``docs/architecture.md``。

本包文件分工：

* ``local_region.py``       局部域（水平圆域 + 过渡权重 + CPA 时刻）
* ``detour_candidates.py``  让路候选几何（垂直优先、端点冻结、余弦钟形衰减）
* ``local_replan.py``       候选逐一重解时间 → 独立复核 → 真几何净空 → 对手证书
* ``global_recheck.py``     检测-聚类-让路-复检的轮次编排（机号大者让路）
"""

from path_planning.methods.coordination.detour_candidates import (
    DetourCandidate,
    generate_detours,
    lateral_direction,
)
from path_planning.methods.coordination.global_recheck import (
    CoordinationOutcome,
    RoundRecord,
    coordinate_conflicts,
)
from path_planning.methods.coordination.local_region import (
    LocalRegion,
    region_from_conflict,
    transition_weight,
)
from path_planning.methods.coordination.local_replan import (
    LocalReplanRequest,
    LocalReplanResult,
    TrialRecord,
    replan_locally,
)

__all__ = [
    "DetourCandidate",
    "generate_detours",
    "lateral_direction",
    "CoordinationOutcome",
    "RoundRecord",
    "coordinate_conflicts",
    "LocalRegion",
    "region_from_conflict",
    "transition_weight",
    "LocalReplanRequest",
    "LocalReplanResult",
    "TrialRecord",
    "replan_locally",
]
