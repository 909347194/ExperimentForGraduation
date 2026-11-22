# -*- coding: utf-8 -*-
"""多初值候选：评估、去重、按 NLP 初值优先级排序。

职责：把 ``RRT3D.plan_many`` 的原始结果变成带几何指标、去重、排好序的
      ``PathCandidate`` 列表，供 NLP 挑初值。
边界：真几何净空（shapely 棱柱距离，不经栅格）不在此处——那是
      ``timeline/feasibility.py`` 的交付复核口径；这里只读窗内 SDF，
      与 RRT 同一真值。
相关：``rrt_path_search.py``（候选来源）、``corridor_window.py``
      （走廊半宽判定）、``timeline/initial_guess.py``（选中后转 NLP 初值）。

排序口径——"NLP 好不好解"优先于"几何更短"：

1. 折线在走廊半宽内（NLP 的半宽是硬约束，带外初值第一步就违反）；
2. 几何长度更短（最短时间目标的下界代理）；
3. SDF 净空更大、路径点更少（同长度时更稳）。

多初值不是替 NLP 找最终最优，只是给它一个更稳的起点。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from path_planning.methods.spatial.corridor_window import CorridorWindow
from path_planning.methods.spatial.occupancy_sdf import SdfField
from path_planning.methods.spatial.rrt_path_search import RRT3D, RRTResult

__all__ = [
    "PathCandidate",
    "evaluate_candidate",
    "generate_initial_paths",
    "dedup_candidates",
]

_EPS = 1e-9
#: 去重比较时每条路径按弧长重采样的点数
_RESAMPLE_POINTS = 32


@dataclass(frozen=True)
class PathCandidate:
    """一条初始路径候选 + 选优需要的几何指标（全部 SDF / 走廊口径）。

    Attributes:
        result:          原始 RRT 结果（折线 + 迭代统计）
        length_m:        折线几何长度
        min_sdf_m:       沿折线（按采样间隔加密）的最小 SDF
        max_lateral_m:   折线到走廊中心轴的最大水平侧向偏移
        inside_corridor: 侧向偏移是否在半宽内（NLP 硬约束的初值可行性）
    """

    result: RRTResult
    length_m: float
    min_sdf_m: float
    max_lateral_m: float
    inside_corridor: bool

    @property
    def path_enu(self) -> np.ndarray:
        return self.result.path_enu


def evaluate_candidate(
    result: RRTResult,
    field: SdfField,
    window: CorridorWindow,
    *,
    sample_step_m: float,
) -> PathCandidate:
    """量一条候选的长度 / 最小 SDF / 最大侧向偏移。

    最小 SDF 按 ``sample_step_m`` 加密采样（与 RRT 的 ``_segment_free``
    同一步长），保证"RRT 判它自由"与"这里量出的净空"是同一口径。
    """
    path = np.asarray(result.path_enu, dtype=np.float64)
    length_m = float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())
    pts = _densify(path, sample_step_m)
    min_sdf_m = float(field.at(pts).min())
    max_lateral_m = float(window.lateral_of(path).max())
    return PathCandidate(
        result=result,
        length_m=length_m,
        min_sdf_m=min_sdf_m,
        max_lateral_m=max_lateral_m,
        inside_corridor=bool(max_lateral_m <= window.half_width_m + _EPS),
    )


def generate_initial_paths(
    rrt: RRT3D,
    start: Sequence[float],
    goal: Sequence[float],
    *,
    initializations: int,
    dedup_tol_m: float,
) -> list[PathCandidate]:
    """多初值全流程：``plan_many`` → 评估 → 排序 → 去重。

    返回按 NLP 初值优先级**降序**的候选（第 0 个最好）；空列表的含义对上层
    是 ``RRT_NO_PATH``，这里不抛异常——"初值全灭"是正常失败分支。
    """
    results = rrt.plan_many(start, goal, initializations=initializations)
    candidates = [
        evaluate_candidate(
            r, rrt.field, rrt.window, sample_step_m=rrt.cfg.sample_step_m
        )
        for r in results
    ]
    candidates.sort(key=_rank)
    return dedup_candidates(candidates, tol_m=dedup_tol_m)


def dedup_candidates(
    candidates: list[PathCandidate], *, tol_m: float
) -> list[PathCandidate]:
    """去掉彼此"几乎同一条"的候选，保留排名更优者（输入须已按 ``_rank`` 排序）。

    比较方式：两条路径各按弧长重采样 ``_RESAMPLE_POINTS`` 点后逐点最大距离。
    多初值若只是同一条路加了抖动，NLP 从它们出发只会收敛到同一个局部解，
    白费初值名额。
    """
    kept: list[PathCandidate] = []
    kept_pts: list[np.ndarray] = []
    for cand in candidates:
        pts = _resample(cand.path_enu, _RESAMPLE_POINTS)
        if kept_pts and min(
            float(np.max(np.linalg.norm(pts - p, axis=1))) for p in kept_pts
        ) < tol_m:
            continue
        kept.append(cand)
        kept_pts.append(pts)
    return kept


def _rank(candidate: PathCandidate) -> tuple[int, float, float, int]:
    """NLP 初值优先级：走廊内 > 更短 > 净空更大 > 点更少。"""
    return (
        0 if candidate.inside_corridor else 1,
        candidate.length_m,
        -candidate.min_sdf_m,
        len(candidate.path_enu),
    )


def _densify(path: np.ndarray, step_m: float) -> np.ndarray:
    """把折线按 ``step_m`` 补点（含两端）。"""
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    n = np.maximum(2, np.ceil(seg / max(step_m, _EPS)).astype(int) + 1)
    chunks = [
        np.linspace(a, b, k)
        for (a, b), k in zip(zip(path[:-1], path[1:]), n)
    ]
    return np.vstack(chunks) if chunks else path


def _resample(path: np.ndarray, k: int) -> np.ndarray:
    """把折线按弧长均匀重采样为 ``k`` 点（含两端）；退化折线返回重复首点。"""
    path = np.asarray(path, dtype=np.float64)
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(s[-1])
    if total <= _EPS:
        return np.repeat(path[:1], k, axis=0)
    targets = np.linspace(0.0, total, k)
    out = np.empty((k, path.shape[1]), dtype=np.float64)
    for d in range(path.shape[1]):
        out[:, d] = np.interp(targets, s, path[:, d])
    return out
