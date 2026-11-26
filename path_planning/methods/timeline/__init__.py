# -*- coding: utf-8 -*-
"""时间层：轨迹优化（CasADi+IPOPT，时空联合）、初值、Hermite 量、独立复核、降级剖面。

职责：把空间层给的纯几何折线变成**带时间的可行轨迹**（Hermite 段序列），并提供
      交付前的独立复核与优化失败时的保守降级产物。
边界：不做几何搜索（``spatial``）、不做冲突检测（``conflict``）、不做协调
      （``coordination``）。
相关：``docs/time_mapping.md``（口径 B、逐节点 Δt、弧长 Gauss 重积分、两预算）。

本包文件分工：

* ``splines.py``               Hermite 求值 / 解析导数（一、二阶）/ Gauss 弧长 / 按时刻查询
* ``initial_guess.py``         弧长重采样 + 逐节点 (v,a,Δt) 初值
* ``nlp_config.py``            NLP 旋钮（``NlpConfig``，含几何引导管半径）
* ``trajectory_optimizer.py``  主 NLP（min ΣΔt，三级退让，输出 Hermite Trajectory）
* ``feasibility.py``           独立复核（不依赖优化器；速度/加速度/推力/净空/走廊/端点/Δt/弧长相容）
* ``fallback.py``              ``OPT_FAILED`` 时的定速巡航剖面（``time_source=estimate``，保守 1.2）
"""

from path_planning.methods.timeline.fallback import CONSERVATIVE_FACTOR, cruise_profile
from path_planning.methods.timeline.feasibility import (
    VerificationLimits,
    VerificationResult,
    Violation,
    verify_trajectory,
)
from path_planning.methods.timeline.initial_guess import (
    initial_guess,
    initial_guess_from_limits,
    resample_path,
)
from path_planning.methods.timeline.splines import (
    position_at,
    segment_acceleration,
    segment_command_acceleration,
    segment_arc_length,
    segment_position,
    segment_velocity,
    trajectory_arc_length,
    trajectory_nodes,
    velocity_at,
)
from path_planning.methods.timeline.nlp_config import NlpConfig
from path_planning.methods.timeline.trajectory_optimizer import (
    NlpResult,
    optimize_trajectory,
)

__all__ = [
    "CONSERVATIVE_FACTOR",
    "cruise_profile",
    "VerificationLimits",
    "VerificationResult",
    "Violation",
    "verify_trajectory",
    "initial_guess",
    "initial_guess_from_limits",
    "resample_path",
    "position_at",
    "segment_acceleration",
    "segment_command_acceleration",
    "segment_arc_length",
    "segment_position",
    "segment_velocity",
    "trajectory_arc_length",
    "trajectory_nodes",
    "velocity_at",
    "NlpConfig",
    "NlpResult",
    "optimize_trajectory",
]
