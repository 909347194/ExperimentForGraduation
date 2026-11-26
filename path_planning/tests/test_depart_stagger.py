# -*- coding: utf-8 -*-
"""离巢错峰：同周期同起点航次按机号错开 ``t0``，以及"错开靠几何、不靠时间窗"的证据。

关键细节：C1 时间分离证书只比**整条航迹的时间窗**（``pair_certificates`` 的 a_lo/a_hi 对
b_lo/b_hi），错开 10 s 时窗口照样重叠，C1 不会触发；真正让判定变安全的是同一时刻两台已沿
航迹拉开（C3 的 CPA 距离 > 判定半径）。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import HermiteSegment, TimeSource, Trajectory
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.experiments.exp01_smoke.support.experiment_config import (
    CorridorSettings, ExperimentConfig, FieldSettings, MissionWaypointsSettings,
    ScenarioSettings,
)
from path_planning.experiments.exp01_smoke.support.fleet_spec import (
    load_fleet_input, stagger_offsets,
)
from path_planning.experiments.exp01_smoke.support.waypoint_policy import SiteClearance
from path_planning.methods.conflict.conflict_detector import DetectionConfig, detect_pair
from path_planning.methods.conflict.pair_certificates import CertificateKind
from path_planning.methods.timeline.splines import position_at

SPACING = (4.0, 4.0, 2.0)


class _FlatScenario:
    """平坦场地：地面 10 m、无建筑。"""

    def ground_height_at(self, x, y):
        return 10.0

    def roof_height_at(self, x, y):
        return 10.0


_OPEN_SITE = SiteClearance(
    sdf_at=lambda x, y, z: 99.0,
    prism_clearance_at=lambda x, y, z: 99.0,
    z_ceiling_m=123.302,
    clearance_m=8.0,  # 求解阈值 = 交付 6 + 竖直格距 2
)


def _uav(uid: int) -> dict:
    return {
        "id": uid, "model": "test", "mass_kg": 9.5, "v_max_mps": 15.0, "v_up_mps": 6.0,
        "v_dn_mps": 3.0, "a_max_mps2": 3.0, "f_max_n": 200.0, "f_min_n": 78.0,
        "max_range_m": 20000.0, "max_time_s": 1200.0,
        "budget": {"endurance_remaining_s": 900.0, "range_remaining_m": 4000.0},
    }


def _config(stagger: float) -> ExperimentConfig:
    return ExperimentConfig(
        name="test", seeds=(0,),
        scenario=ScenarioSettings(data_dir=Path("."), name=None, nest_count=None,
                                  terrain_step_m=30.0, bbox_margin_m=0.0),
        field=FieldSettings(spacing_m=SPACING, alt_max_m=120.0, pad_m=0.0,
                            include_static_nofly=False, sample_bias_m=0.0, cache_dir=None),
        corridor=CorridorSettings(half_width_m=None, width_ratio=0.2,
                                  half_width_min_m=20.0, margin_m=10.0),
        params=PlanParams(), endpoint_clearance_m=6.0, endpoint_exempt_radius_m=0.0,
        e_geo_m=0.5, cycle_length_s=240.0, depart_stagger_s=stagger,
        results_dir=Path("."),
        mission_waypoints=MissionWaypointsSettings(
            nest_hover_agl_m=15.0, task_clearance_agl_m=10.0,
        ),
    )


def _fleet(stagger: float):
    def _xy(x: float, y: float, kind: str) -> dict:
        return {"xy": [x, y], "point_type": kind}

    tours = [
        # 同周期、同起点（同巢）：机 2/3 必须错峰
        {"uav_id": 2, "cycle": 1, "t0": 0.0, "start": _xy(-650.0, -259.8, "nest"),
         "end": {"id": 21, "at": _xy(-606.6, -506.0, "nest")}},
        {"uav_id": 3, "cycle": 1, "t0": 0.0, "start": _xy(-650.0, -259.8, "nest"),
         "end": {"id": 31, "at": _xy(-606.6, -506.0, "nest")}},
        # 不同起点：不参与错峰
        {"uav_id": 1, "cycle": 1, "t0": 0.0, "start": _xy(-605.2, -193.5, "nest"),
         "end": {"id": 11, "at": _xy(-560.0, -160.0, "nest")}},
        # 跨周期同起点：周期基准本来就差 240 s，不再叠加错峰
        {"uav_id": 4, "cycle": 2, "t0": 240.0, "start": _xy(-650.0, -259.8, "nest"),
         "end": {"id": 41, "at": _xy(-606.6, -506.0, "nest")}},
    ]
    raw = {"uavs": [_uav(uid) for uid in (1, 2, 3, 4)], "tours": tours}
    tmp = Path(tempfile.mkdtemp()) / "uav_config.json"
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    return load_fleet_input(
        tmp, _config(stagger), scenario=_FlatScenario(), site=_OPEN_SITE
    )


def _line(uav_id: int, p0, p1, dur: float, t0: float) -> Trajectory:
    """合成一条匀速直线航迹（同速同向，与真实巡航段同量级）。"""
    v = tuple((b - a) / dur for a, b in zip(p0, p1))
    seg = HermiteSegment(t0=t0, t1=t0 + dur, p0=p0, p1=p1, v0=v, v1=v)
    return Trajectory(
        uav_id=uav_id, segments=(seg,),
        path_len_m=float(np.linalg.norm(np.subtract(p1, p0))),
        time_source=TimeSource.TRAJECTORY,
    )


class TestStaggerOffsets(unittest.TestCase):
    def test_only_same_start_groups_are_staggered(self) -> None:
        starts = {1: (-605.2, -193.5, 60.0), 2: (-650.0, -259.8, 60.0),
                  3: (-650.0, -259.8, 60.0)}
        self.assertEqual(stagger_offsets(starts, 10.0), {1: 0.0, 2: 0.0, 3: 10.0})
        self.assertEqual(stagger_offsets(starts, 0.0), {1: 0.0, 2: 0.0, 3: 0.0})

    def test_three_in_one_nest_get_0_10_20(self) -> None:
        starts = {uid: (0.0, 0.0, 60.0) for uid in (9, 5, 7)}
        self.assertEqual(stagger_offsets(starts, 10.0), {5: 0.0, 7: 10.0, 9: 20.0})


class TestFleetStagger(unittest.TestCase):
    def test_requests_carry_staggered_t0_only_within_same_start(self) -> None:
        fleet = _fleet(stagger=10.0)
        t0s = {r.uav_id: r.t0 for r in fleet.requests}
        self.assertEqual(t0s[1], 0.0)
        self.assertEqual(t0s[2], 0.0)
        self.assertEqual(t0s[3], 10.0)
        self.assertEqual(t0s[4], 240.0, "跨周期同起点不叠加错峰")
        offsets = {r.uav_id: r.meta["depart_offset_s"] for r in fleet.requests}
        self.assertEqual(offsets, {1: 0.0, 2: 0.0, 3: 10.0, 4: 0.0})

    def test_zero_stagger_reproduces_coincidence(self) -> None:
        fleet = _fleet(stagger=0.0)
        t0s = {r.uav_id: r.t0 for r in fleet.requests}
        self.assertEqual(t0s[2], t0s[3], "关闭错峰 ⇒ 同刻同起点")


class TestStaggerEffectOnDetection(unittest.TestCase):
    """错开 10 s 后判定变安全，且**不是** C1 时间分离放行的。"""

    def setUp(self) -> None:
        self.cfg = DetectionConfig.from_params(
            PlanParams(),
            VehicleSpec(model="test", mass_kg=9.5, v_max_mps=13.5, v_up_mps=6.0,
                        v_dn_mps=3.0, a_max_mps2=3.0, f_max_n=200.0, f_min_n=78.0,
                        max_range_m=20000.0, max_time_s=1200.0),
            e_geo_m=0.5,
        )
        self.p0 = (-650.0, -259.8, 60.0)
        self.p1 = (-606.6, -506.0, 60.0)
        self.dur = 23.0

    def test_same_start_same_time_is_a_zero_distance_conflict(self) -> None:
        a = _line(1, self.p0, self.p1, self.dur, 0.0)
        b = _line(2, self.p0, self.p1, self.dur, 0.0)
        pair = detect_pair(a, b, self.cfg)
        self.assertFalse(pair.safe)
        self.assertLess(abs(pair.min_distance), 1e-6)

    def test_ten_second_stagger_clears_it_geometrically(self) -> None:
        a = _line(1, self.p0, self.p1, self.dur, 0.0)
        b = _line(2, self.p0, self.p1, self.dur, 10.0)
        self.assertLess(b.t0, a.t_end, "时间窗仍重叠：C1 无从下手")
        pair = detect_pair(a, b, self.cfg)
        self.assertTrue(pair.safe, pair)
        self.assertEqual(pair.kind, CertificateKind.MOTION_LOWER_BOUND)
        self.assertGreater(pair.min_distance, 2.0 * pair.radius)
        gap = float(np.linalg.norm(
            np.subtract(position_at(a, b.t0), position_at(b, b.t0))))
        self.assertGreater(gap, 100.0)


if __name__ == "__main__":
    unittest.main()
