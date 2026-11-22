# -*- coding: utf-8 -*-
"""RRT 运行参数：结构 + 校验 + 从 ``PlanParams`` 派生，**不含独立默认值**。

职责：定义 ``RRTConfig``（步长、采样预算、净空阈值、端点豁免），以及地面高程
      查询协议 ``GroundHeightFn``。
边界：不做采样、不建场、不判碰撞（``rrt_path_search.py`` 用它跑树）。
相关：``common/plan_params.py``（唯一参数来源）、``rrt_path_search.py``。

为什么单独成文件：``RRTConfig`` 自带 ``__post_init__`` 十条校验与
``from_params`` 派生，和"跑树"的代码混在一起会把搜索器推到 370 行；参数
结构随 ``PlanParams`` 一起演进，单独成文件后改参数只动一处。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from path_planning.common.plan_params import PlanParams

__all__ = ["GroundHeightFn", "RRTConfig"]


class GroundHeightFn(Protocol):
    """地面高程查询协议（``Scenario.ground_height`` 天然满足）。"""

    def __call__(self, xs, ys) -> np.ndarray: ...


@dataclass(frozen=True)
class RRTConfig:
    """RRT 运行参数。

    **没有独立默认值**：字段全部必填，只能由 ``from_params`` 从 ``PlanParams``
    派生——算法参数只在一处定义，否则默认值散成第二份、改 ``PlanParams``
    不生效。

    Attributes:
        step_m / min_step_m:      步长上下限（米）
        sdf_step_scale:           SDF→步长比例（(0,1]，越小越谨慎）
        max_iters:                单次采样预算
        goal_sample_rate:         GoalBias 概率
        clearance_m:              全程净空阈值（SDF 口径，米）
        seed:                     基础种子；多初值用 ``seed + i``
        sample_step_m:            段内净空采样间隔（米）
        shortcut_iters:           捷径化预算（软上限，见 ``rrt_path_search`` 模块说明第 3 条）
        endpoint_clearance_m:     端点豁免圈内的净空阈值；None = 不豁免
        endpoint_exempt_radius_m: 豁免圈半径（米，0 = 不豁免）
    """

    step_m: float
    min_step_m: float
    sdf_step_scale: float
    max_iters: int
    goal_sample_rate: float
    clearance_m: float
    seed: int
    sample_step_m: float
    shortcut_iters: int
    endpoint_clearance_m: float | None = None
    endpoint_exempt_radius_m: float = 0.0

    def __post_init__(self) -> None:
        errs: list[str] = []
        if self.min_step_m <= 0.0:
            errs.append(f"min_step_m 必须为正：{self.min_step_m}")
        if self.step_m <= self.min_step_m:
            errs.append(f"step_m({self.step_m}) 必须大于 min_step_m({self.min_step_m})")
        if not (0.0 < self.sdf_step_scale <= 1.0):
            errs.append(f"sdf_step_scale 应在 (0,1]：{self.sdf_step_scale}")
        if self.max_iters <= 0:
            errs.append(f"max_iters 必须为正：{self.max_iters}")
        if not (0.0 < self.goal_sample_rate < 1.0):
            errs.append(f"goal_sample_rate 应在 (0,1)：{self.goal_sample_rate}")
        if self.clearance_m <= 0.0:
            errs.append(f"clearance_m 必须为正：{self.clearance_m}")
        if self.sample_step_m <= 0.0:
            errs.append(f"sample_step_m 必须为正：{self.sample_step_m}")
        if self.shortcut_iters < 0:
            errs.append(f"shortcut_iters 不能为负：{self.shortcut_iters}")
        if self.endpoint_clearance_m is not None and self.endpoint_clearance_m < 0.0:
            errs.append(f"endpoint_clearance_m 不能为负：{self.endpoint_clearance_m}")
        if self.endpoint_exempt_radius_m < 0.0:
            errs.append(f"endpoint_exempt_radius_m 不能为负：{self.endpoint_exempt_radius_m}")
        if errs:
            raise ValueError(f"RRTConfig 非法（{len(errs)} 项）：\n  " + "\n  ".join(errs))

    @property
    def effective_endpoint_clearance_m(self) -> float:
        """端点实际使用的净空阈值（未配置豁免时等于全程阈值）。"""
        return (
            self.clearance_m
            if self.endpoint_clearance_m is None
            else float(self.endpoint_clearance_m)
        )

    @classmethod
    def from_params(
        cls,
        params: PlanParams,
        *,
        clearance_m: float,
        seed: int,
        endpoint_clearance_m: float | None = None,
        endpoint_exempt_radius_m: float = 0.0,
    ) -> RRTConfig:
        """从 ``PlanParams`` + 净空派生；算法参数只在 ``PlanParams`` 定义一次。"""
        return cls(
            step_m=params.rrt_step_m,
            min_step_m=params.rrt_min_step_m,
            sdf_step_scale=params.rrt_sdf_step_scale,
            max_iters=params.rrt_max_iters,
            goal_sample_rate=params.rrt_goal_sample_rate,
            clearance_m=float(clearance_m),
            seed=int(seed),
            sample_step_m=params.clearance_sample_step_m,
            shortcut_iters=params.rrt_shortcut_iters,
            endpoint_clearance_m=endpoint_clearance_m,
            endpoint_exempt_radius_m=float(endpoint_exempt_radius_m),
        )
