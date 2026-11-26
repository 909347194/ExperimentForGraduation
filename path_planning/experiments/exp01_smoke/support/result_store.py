# -*- coding: utf-8 -*-
"""结果落盘：航迹 / 规划结果 / 协调周期 → 可复现的 JSON。

职责：把 ``Trajectory``、``PlanResult``、``CoordinationOutcome`` 转成纯 JSON 结构，
      并按"一次运行一个目录"的约定写文件（``solution.json`` / ``metrics.json`` /
      ``run_meta.json`` / ``effective_config.json``）。
边界：不算指标（``flight_metrics.py``）、不画图。
相关：``common/plan_types.py``、``api.py``。

为什么航迹要整段落盘：段端点 + 速度 + 时间戳就是 Hermite 的完整定义，有了它
就能离线重算任意时刻位置、复核净空、画图、跟别的算法逐段对比；只存"时长/长度"
的指标无法回答"它到底从哪儿绕的"。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from path_planning.common.plan_types import (
    HermiteSegment,
    PlanResult,
    TimeSource,
    Trajectory,
)

__all__ = [
    "dump_json",
    "plan_result_to_dict",
    "trajectory_from_dict",
    "trajectory_to_dict",
    "write_run_outputs",
]

#: 极端小值截断：JSON 里不写 1e-17 这种噪声
_TOL = 1e-9


def dump_json(path: str | Path, payload: Any) -> None:
    """写 JSON（自动处理 numpy / tuple / 路径）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(_plain(payload), ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )


def trajectory_to_dict(traj: Trajectory) -> dict[str, Any]:
    """航迹 → 段表（每段的端点位置/速度/加速度 + 时间戳），可完整重建。

    加速度只优化器的交付段有（五次 Hermite）；降级剖面是三次段，写 ``None``。
    落盘必须带上它，否则审计从 JSON 重建时只能拿回三次曲线，``p''`` 复核会算错。
    """
    segs = []
    for k, seg in enumerate(traj.segments):
        segs.append(
            {
                "index": k,
                "t0": _r(seg.t0),
                "t1": _r(seg.t1),
                "p0": [_r(v) for v in seg.p0],
                "v0": [_r(v) for v in seg.v0],
                "a0": None if seg.a0 is None else [_r(v) for v in seg.a0],
                "p1": [_r(v) for v in seg.p1],
                "v1": [_r(v) for v in seg.v1],
                "a1": None if seg.a1 is None else [_r(v) for v in seg.a1],
                "is_hover": bool(seg.is_hover),
                "chord_m": _r(seg.chord_m),
            }
        )
    return {
        "uav_id": int(traj.uav_id),
        "t0": _r(traj.t0),
        "t_end": _r(traj.t_end),
        "duration_s": _r(traj.duration),
        "flight_duration_s": _r(traj.flight_duration),
        "hover_duration_s": _r(traj.hover_duration),
        "weighted_duration_s": _r(traj.weighted_duration),
        "path_len_m": _r(traj.path_len_m),
        "time_source": traj.time_source.value,
        "hover_weight": _r(traj.hover_weight),
        "start_position": [_r(v) for v in traj.start_position],
        "end_position": [_r(v) for v in traj.end_position],
        "meta": dict(traj.meta),
        "segments": segs,
    }


def plan_result_to_dict(result: PlanResult, *, with_trajectory: bool = True) -> dict[str, Any]:
    """``PlanResult`` → JSON；``deliverable`` 与 ``ok`` 分开写（降级交付必须看得见）。"""
    out: dict[str, Any] = {
        "uav_id": int(result.uav_id),
        "ok": bool(result.ok),
        "deliverable": bool(result.deliverable),
        "failure": None if result.failure is None else result.failure.value,
        "severity": None if result.severity is None else result.severity.value,
        "detail": result.detail,
        "diagnostics": _plain(result.diagnostics),
    }
    if with_trajectory and result.trajectory is not None:
        out["trajectory"] = trajectory_to_dict(result.trajectory)
    return out


def trajectory_from_dict(payload: Mapping[str, Any]) -> Trajectory:
    """``trajectory_to_dict`` 的逆：段表 → ``Trajectory``（审计/画图用）。

    ``a0/a1`` 缺失（老结果文件）按 ``None`` 处理，段退化为三次——那种文件本来就
    是三次时代的产物，用它自己的定义复算才自洽。
    """
    segs = tuple(
        HermiteSegment(
            t0=float(s["t0"]),
            t1=float(s["t1"]),
            p0=tuple(float(v) for v in s["p0"]),
            p1=tuple(float(v) for v in s["p1"]),
            v0=tuple(float(v) for v in s["v0"]),
            v1=tuple(float(v) for v in s["v1"]),
            a0=None if s.get("a0") is None else tuple(float(v) for v in s["a0"]),
            a1=None if s.get("a1") is None else tuple(float(v) for v in s["a1"]),
            is_hover=bool(s["is_hover"]),
        )
        for s in payload["segments"]
    )
    return Trajectory(
        uav_id=int(payload["uav_id"]),
        segments=segs,
        path_len_m=float(payload["path_len_m"]),
        time_source=TimeSource(str(payload["time_source"])),
        hover_weight=float(payload.get("hover_weight", 1.0)),
        meta=dict(payload.get("meta", {})),
    )


def write_run_outputs(
    out_dir: str | Path,
    *,
    effective_config: Mapping[str, Any],
    run_meta: Mapping[str, Any],
    metrics: Mapping[str, Any],
    solution: Mapping[str, Any],
) -> dict[str, str]:
    """四个文件一次写齐，返回路径表（供打印/审计引用）。"""
    base = Path(out_dir)
    files = {
        "solution": base / "solution.json",
        "metrics": base / "metrics.json",
        "run_meta": base / "run_meta.json",
        "effective_config": base / "effective_config.json",
    }
    dump_json(files["solution"], solution)
    dump_json(files["metrics"], metrics)
    dump_json(files["run_meta"], run_meta)
    dump_json(files["effective_config"], effective_config)
    return {k: str(v) for k, v in files.items()}


# ---------------------------------------------------------------------------
# 类型归一
# ---------------------------------------------------------------------------


def _r(value: float) -> float:
    v = float(value)
    return 0.0 if abs(v) < _TOL else round(v, 6)


def _plain(value: Any) -> Any:
    """递归转纯 Python（numpy 标量/数组、dataclass、tuple、Path）。"""
    if isinstance(value, np.ndarray):
        return [_plain(v) for v in value.tolist()]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, float):
        return 0.0 if abs(value) < _TOL else round(float(value), 6)
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if hasattr(value, "value") and isinstance(value.value, (str, int)):  # Enum
        return value.value
    return value
