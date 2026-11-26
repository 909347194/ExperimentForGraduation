# -*- coding: utf-8 -*-
"""独立验证器测试：真轨迹全项通过、逐项能抓到手写违规、豁免与口径细节。

覆盖点（每条都对应一个真实会出错的判据）：
  1. 优化器产出的轨迹被独立复核为 ok（两条路径互不共用约束代码）
  2. 手写超速段 → 速度违规
  3. 手写穿墙直线 + 全豁免 → 豁免后不再判净空（与优化器同口径）
  4. 长悬停 + 短飞行：⑤ 必须用 flight_duration，不能误用 duration
  5. Δt 越界 / 名义半宽只告警 / 越出计算域判死 / 端点偏离 / 竖直加速度不足
"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.failure_codes import FailureCode
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import HermiteSegment, Trajectory, TimeSource
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.spatial.corridor_window import CorridorRule, corridor_window
from path_planning.methods.spatial.occupancy_sdf import (
    ObstaclePrism,
    SdfField,
    build_occupancy,
    build_sdf,
)
from path_planning.methods.timeline.dt_floor import floor_from_length
from path_planning.methods.timeline.feasibility import (
    VerificationLimits,
    verify_trajectory,
)
from path_planning.methods.timeline.trajectory_optimizer import NlpConfig, optimize_trajectory

SPACING = (2.0, 2.0, 2.0)
ALT = (4.0, 24.0)
START, GOAL = (0.0, 0.0, 10.0), (40.0, 0.0, 10.0)

FAR_PILLAR = ObstaclePrism(
    np.array([[0.0, -13.0], [2.0, -13.0], [2.0, -11.0], [0.0, -11.0]]), 0.0, 10.0
)
WALL = ObstaclePrism(
    np.array([[19.0, -14.0], [21.0, -14.0], [21.0, 14.0], [19.0, 14.0]]), 0.0, 14.0
)


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="test", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=50000.0, max_time_s=3000.0,
    )


def _window_field(prisms):
    window = corridor_window(
        START, GOAL, CorridorRule(half_width_m=10.0, margin_m=2.0), SPACING, *ALT
    )
    occ = build_occupancy(prisms, window.grid)
    return SdfField(window.grid, build_sdf(occ, SPACING)), window


def _limits(window, **over) -> VerificationLimits:
    kw = dict(
        clearance_m=2.0,
        half_width_m=window.half_width_m,
        corridor_lateral_m=window.lateral_m,
        corridor_normal_xy=tuple(float(c) for c in window.normal),
        corridor_pivot_xy=window.start_xy,
        expected_start=START,
        expected_end=GOAL,
    )
    kw.update(over)
    return VerificationLimits(**kw)


def _straight(segments, path_len_m=40.0) -> Trajectory:
    return Trajectory(uav_id=1, segments=tuple(segments), path_len_m=path_len_m,
                      time_source=TimeSource.TRAJECTORY)


class TestRealTrajectoryPasses(unittest.TestCase):
    """优化器产出的真轨迹必须被独立复核为 ok —— 两边代码路径不共用。"""

    def test_optimized_straight_ok(self) -> None:
        field, window = _window_field([FAR_PILLAR])
        res = optimize_trajectory(
            np.array([START, GOAL], dtype=float), field,
            NlpConfig.from_params(PlanParams(n_nodes=20, max_iter=300), _vehicle(),
                                  clearance_m=2.0, half_width_m=window.half_width_m,
                                  corridor_normal_xy=tuple(float(c) for c in window.normal),
                                  corridor_pivot_xy=window.start_xy),
        )
        self.assertIsNotNone(res.trajectory)
        out = verify_trajectory(res.trajectory, _vehicle(), field, _limits(window),
                                params=PlanParams(n_nodes=20))
        self.assertTrue(out.ok, [v.detail for v in out.violations])
        self.assertGreaterEqual(out.metrics["worst_clearance_m"], 2.0 - 1e-3)

    def test_optimized_wall_ok(self) -> None:
        field, window = _window_field([WALL])
        params = PlanParams(n_nodes=30, max_iter=500, retreat_node_ladder=(20,))
        res = optimize_trajectory(
            np.array([START, (20.0, 0.0, 18.0), GOAL], dtype=float), field,
            NlpConfig.from_params(params, _vehicle(), clearance_m=2.0,
                                  half_width_m=window.half_width_m,
                                  corridor_normal_xy=tuple(float(c) for c in window.normal),
                                  corridor_pivot_xy=window.start_xy),
        )
        self.assertIsNotNone(res.trajectory)
        out = verify_trajectory(res.trajectory, _vehicle(), field, _limits(window), params=params)
        self.assertTrue(out.ok, [v.detail for v in out.violations])


class TestDetectsViolations(unittest.TestCase):
    def test_speed_over_limit(self) -> None:
        field, window = _window_field([FAR_PILLAR])
        seg = HermiteSegment(t0=0.0, t1=3.0, p0=START, p1=GOAL,
                             v0=(50.0, 0.0, 0.0), v1=(50.0, 0.0, 0.0))
        out = verify_trajectory(_straight([seg]), _vehicle(), field,
                                _limits(window, expected_start=None, expected_end=None))
        self.assertFalse(out.ok)
        self.assertIn(FailureCode.KINEMATIC_INCONSISTENT, out.codes())
        self.assertTrue(any("水平速度" in v.detail for v in out.violations))

    def test_clearance_through_wall(self) -> None:
        field, window = _window_field([WALL])
        seg = HermiteSegment(t0=0.0, t1=10.0, p0=START, p1=GOAL,
                             v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0))
        out = verify_trajectory(_straight([seg]), _vehicle(), field,
                                _limits(window, expected_start=None, expected_end=None,
                                        endpoint_exempt_segments=0))
        self.assertIn(FailureCode.CLEARANCE_VIOLATION, out.codes())
        self.assertLess(out.metrics["worst_clearance_m"], 0.0)

    def test_exempt_segments_skips_clearance(self) -> None:
        """全豁免时同一穿墙直线不再报净空（证明豁免口径真的生效）。"""
        field, window = _window_field([WALL])
        seg = HermiteSegment(t0=0.0, t1=10.0, p0=START, p1=GOAL,
                             v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0))
        out = verify_trajectory(_straight([seg]), _vehicle(), field,
                                _limits(window, expected_start=None, expected_end=None,
                                        endpoint_exempt_segments=1))
        self.assertNotIn(FailureCode.CLEARANCE_VIOLATION, out.codes())

    def test_hover_excluded_from_mean_speed(self) -> None:
        """⑤ 用 flight_duration：长悬停不能被算成"飞得太慢/太快"的凭据。"""
        field, window = _window_field([FAR_PILLAR])
        fly = HermiteSegment(t0=0.0, t1=20.0, p0=START, p1=GOAL,
                             v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0))
        hover = HermiteSegment(t0=20.0, t1=220.0, p0=GOAL, p1=GOAL,
                               v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0), is_hover=True)
        traj = _straight([fly, hover])
        out = verify_trajectory(traj, _vehicle(), field,
                                _limits(window, expected_start=None, expected_end=None,
                                        endpoint_exempt_segments=0), params=PlanParams())
        self.assertTrue(out.ok, [v.detail for v in out.violations])
        self.assertAlmostEqual(out.metrics["mean_speed_mps"], 2.0, delta=0.2)
        self.assertAlmostEqual(traj.duration, 220.0, places=6)

    def test_dt_upper_bound(self) -> None:
        field, window = _window_field([FAR_PILLAR])
        seg = HermiteSegment(t0=0.0, t1=25.0, p0=START, p1=GOAL, v0=(1.0, 0.0, 0.0),
                             v1=(1.0, 0.0, 0.0))
        out = verify_trajectory(_straight([seg]), _vehicle(), field,
                                _limits(window, expected_start=None, expected_end=None),
                                params=PlanParams(dt_max_s=20.0))
        self.assertTrue(any("dt_max" in v.detail for v in out.violations))

    def test_corridor_nominal_width_warns_only(self) -> None:
        """名义半宽越 3 cm：照 pp1 口径只告警，不判失败（域内 12 m 仍有 SDF）。"""
        field, window = _window_field([FAR_PILLAR])
        seg = HermiteSegment(t0=0.0, t1=10.0, p0=(0.0, 10.03, 10.0),
                             p1=(40.0, 10.03, 10.0),
                             v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0))
        out = verify_trajectory(_straight([seg]), _vehicle(), field,
                                _limits(window, expected_start=None, expected_end=None,
                                        endpoint_exempt_segments=1, half_width_m=10.0))
        self.assertTrue(out.ok, [v.detail for v in out.violations])
        self.assertFalse(out.metrics["inside_corridor"])
        self.assertTrue(any("超出名义半宽" in w for w in out.warnings))

    def test_corridor_out_of_domain(self) -> None:
        """偏 30 m 越出计算域（12 m）：域外没建 SDF，必须判死并报专属失败码。"""
        field, window = _window_field([FAR_PILLAR])
        seg = HermiteSegment(t0=0.0, t1=10.0, p0=(0.0, 30.0, 10.0), p1=(40.0, 30.0, 10.0),
                             v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0))
        out = verify_trajectory(_straight([seg]), _vehicle(), field,
                                _limits(window, expected_start=None, expected_end=None,
                                        endpoint_exempt_segments=1, half_width_m=10.0))
        self.assertIn(FailureCode.CORRIDOR_OUT_OF_DOMAIN, out.codes())
        self.assertTrue(any("越出计算域" in v.detail for v in out.violations))

    def test_endpoint_mismatch(self) -> None:
        field, window = _window_field([FAR_PILLAR])
        seg = HermiteSegment(t0=0.0, t1=10.0, p0=START, p1=(39.0, 0.0, 10.0),
                             v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0))
        out = verify_trajectory(_straight([seg]), _vehicle(), field,
                                _limits(window, endpoint_exempt_segments=1))
        self.assertTrue(any("终点偏离" in v.detail for v in out.violations))

    def test_alt_band_judges_polyline_not_curve(self) -> None:
        """z 带按**交付节点折线**判；Hermite 曲线拱出带外只属表示误差，不进判决。

        两端节点都在 17 m（带内 10–18），两个 z 速度相反（+6 / −6）、Δt=3 s：
        曲线中点 = 17 + Δt(v_z0−v_z1)/8 = 21.5 m，越出上界。旧口径按曲线判会把这条
        判死——那正是"判一条线、交另一条线"的表示错位；交付折线恒在带内，故判 ok。
        """
        field, window = _window_field([FAR_PILLAR])
        seg = HermiteSegment(t0=0.0, t1=3.0, p0=(0.0, 0.0, 17.0), p1=(40.0, 0.0, 17.0),
                             v0=(0.0, 0.0, 6.0), v1=(0.0, 0.0, -6.0))
        out = verify_trajectory(_straight([seg]), _vehicle(), field,
                                _limits(window, expected_start=None, expected_end=None,
                                        endpoint_exempt_segments=0,
                                        alt_range_m=(10.0, 18.0)))
        # 只断言"z 带这一项不报"：这段的其他项（水平速度 20 m/s 等）本来就不合规，
        # 与本测试要锁的口径无关。
        self.assertFalse(any("越出飞行带" in v.detail for v in out.violations),
                         [v.detail for v in out.violations])

    def test_vertical_acceleration_insufficient(self) -> None:
        field, window = _window_field([FAR_PILLAR])
        # 0.3 s 内 v_z 从 0 掉到 -2（下降率没超，但加速度 -6.67 < a_z_min=-0.81）
        seg = HermiteSegment(t0=0.0, t1=0.3, p0=(0.0, 0.0, 10.0), p1=(0.0, 0.0, 9.7),
                             v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, -2.0))
        out = verify_trajectory(_straight([seg], path_len_m=0.3), _vehicle(), field,
                                _limits(window, expected_start=None, expected_end=None))
        self.assertTrue(any("竖直加速度" in v.detail for v in out.violations))


class TestShortLegDtFloor(unittest.TestCase):
    """短航段的 Δt 下界：求解侧自适应收缩，复核侧必须用同一份有效地板。

    回归 exp01_smoke seed0 周期 2 机 5 的 202→102（L=44 m）：120 节点求解时地板被收缩到
    0.032 s，解出每段 0.0496 s 的合法轨迹，复核却按配置值 0.05 s 判死 100 段，
    整条航次（10 个任务）回池、最终 0/30。这里钉住"同一条轨迹必须被判 ok"。
    """

    def test_floor_shrinks_only_for_short_length(self) -> None:
        short = floor_from_length(44.0, a_max_mps2=3.0, v_max_plan_mps=15.0,
                                  n_nodes=120, dt_min_s=0.05)
        long = floor_from_length(4000.0, a_max_mps2=3.0, v_max_plan_mps=15.0,
                                 n_nodes=120, dt_min_s=0.05)
        self.assertLess(short, 0.05)
        self.assertAlmostEqual(long, 0.05, places=9)

    def test_short_leg_passes_verification(self) -> None:
        goal = (44.0, 0.0, 10.0)
        window = corridor_window(START, goal, CorridorRule(half_width_m=10.0, margin_m=2.0),
                                 SPACING, *ALT)
        field = SdfField(window.grid, build_sdf(build_occupancy([FAR_PILLAR], window.grid),
                                                SPACING))
        params = PlanParams(n_nodes=120, max_iter=500)
        res = optimize_trajectory(
            np.array([START, goal], dtype=float), field,
            NlpConfig.from_params(params, _vehicle(), clearance_m=2.0,
                                  half_width_m=window.half_width_m,
                                  corridor_normal_xy=tuple(float(c) for c in window.normal),
                                  corridor_pivot_xy=window.start_xy),
        )
        self.assertIsNotNone(res.trajectory)
        flights = [s for s in res.trajectory.segments if not s.is_hover]
        # 前提：这段的段时长确实低于配置 dt_min（否则本测试没打到差分口径）
        self.assertLess(min(s.duration for s in flights), params.dt_min_s)
        out = verify_trajectory(res.trajectory, _vehicle(), field,
                                _limits(window, expected_start=START, expected_end=goal),
                                params=params)
        self.assertTrue(out.ok, [v.detail for v in out.violations])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
