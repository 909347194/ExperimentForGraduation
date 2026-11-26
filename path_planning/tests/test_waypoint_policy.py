# -*- coding: utf-8 -*-
"""任务产生侧高度解析：表面基线 → 间隙 → 净空阶梯；合规检查全在 exp01 support。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from path_planning.common.plan_params import PlanParams
from path_planning.experiments.exp01_smoke.support.experiment_config import (
    CorridorSettings,
    ExperimentConfig,
    FieldSettings,
    MissionWaypointsSettings,
    ScenarioSettings,
    load_experiment_config,
)
from path_planning.experiments.exp01_smoke.support.fleet_spec import load_fleet_input
from path_planning.experiments.exp01_smoke.support.waypoint_policy import (
    POINT_TYPE_NEST,
    POINT_TYPE_TASK,
    SiteClearance,
    WaypointPolicyConfig,
    resolve_waypoint,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = PROJECT_ROOT / "path_planning" / "experiments" / "exp01_smoke" / "config.yaml"


class _FakeScenario:
    """最小场景：地面 10 m，(5, 6) 处是 40 m 屋顶（楼高 30 m）。"""

    def ground_height_at(self, x, y):
        return 10.0

    def roof_height_at(self, x, y):
        return 40.0 if (float(x), float(y)) == (5.0, 6.0) else 10.0


def _site(sdf=lambda x, y, z: 99.0, prism=lambda x, y, z: 99.0, ceiling=123.3,
          clearance=6.0) -> SiteClearance:
    return SiteClearance(sdf_at=sdf, prism_clearance_at=prism, z_ceiling_m=ceiling,
                         clearance_m=clearance)


def _policy() -> WaypointPolicyConfig:
    return WaypointPolicyConfig(
        nest_hover_agl_m=15.0, task_clearance_agl_m=10.0, lift_step_m=2.0,
    )


class TestHeightRule(unittest.TestCase):
    def test_nest_on_plain_ground(self) -> None:
        self.assertEqual(
            resolve_waypoint(1.0, 2.0, POINT_TYPE_NEST, _policy(), _FakeScenario(), _site()),
            (1.0, 2.0, 25.0),
        )

    def test_nest_on_rooftop_uses_roof_not_ground(self) -> None:
        """巢在楼顶：基线必须是屋顶（40 + 15），按地面会埋进楼体。"""
        self.assertEqual(
            resolve_waypoint(5.0, 6.0, POINT_TYPE_NEST, _policy(), _FakeScenario(), _site()),
            (5.0, 6.0, 55.0),
        )

    def test_task_without_building(self) -> None:
        self.assertEqual(
            resolve_waypoint(1.0, 2.0, POINT_TYPE_TASK, _policy(), _FakeScenario(), _site()),
            (1.0, 2.0, 20.0),
        )

    def test_task_with_building_uses_roof(self) -> None:
        self.assertEqual(
            resolve_waypoint(5.0, 6.0, POINT_TYPE_TASK, _policy(), _FakeScenario(), _site()),
            (5.0, 6.0, 50.0),
        )

    def test_unknown_point_type_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_waypoint(1.0, 2.0, "unknown", _policy(), _FakeScenario(), _site())

    def test_nonpositive_config_rejected(self) -> None:
        with self.assertRaises(ValueError):
            WaypointPolicyConfig(nest_hover_agl_m=0.0, task_clearance_agl_m=10.0,
                                 lift_step_m=2.0)


class TestClearanceLift(unittest.TestCase):
    def test_lifts_until_clearance_is_met(self) -> None:
        """SDF = z − 30：起点 25 m 只有 −5 m，按一格 2 m 抬到 37 m（净空 7 m）才收。"""
        site = _site(sdf=lambda x, y, z: z - 30.0)
        self.assertEqual(
            resolve_waypoint(1.0, 2.0, POINT_TYPE_NEST, _policy(), _FakeScenario(), site),
            (1.0, 2.0, 37.0),
        )

    def test_prism_gap_also_forces_lift(self) -> None:
        site = _site(prism=lambda x, y, z: 0.0 if z < 35.0 else 9.0)
        self.assertEqual(
            resolve_waypoint(1.0, 2.0, POINT_TYPE_NEST, _policy(), _FakeScenario(), site),
            (1.0, 2.0, 35.0),
        )

    def test_rejects_when_ceiling_blocks_the_lift(self) -> None:
        site = _site(sdf=lambda x, y, z: -1.0, ceiling=30.0)
        with self.assertRaisesRegex(ValueError, "净空阶梯抬"):
            resolve_waypoint(1.0, 2.0, POINT_TYPE_NEST, _policy(), _FakeScenario(), site)


class TestExperimentWiring(unittest.TestCase):
    def test_config_section_is_read(self) -> None:
        cfg = load_experiment_config(_CONFIG)
        self.assertEqual(cfg.mission_waypoints.nest_hover_agl_m, 15.0)
        self.assertEqual(cfg.mission_waypoints.task_clearance_agl_m, 10.0)

    def test_gap_below_agl_floor_rejected(self) -> None:
        text = _CONFIG.read_text(encoding="utf-8").replace(
            "task_clearance_agl_m: 10.0", "task_clearance_agl_m: 5.0"
        )
        tmp = Path(tempfile.mkdtemp()) / "config.yaml"
        tmp.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "小于真高带下限"):
            load_experiment_config(tmp)

    def test_fleet_spec_uses_policy_for_xy_points(self) -> None:
        raw = {
            "uavs": [{
                "id": 1, "model": "test", "mass_kg": 9.5, "v_max_mps": 15.0,
                "v_up_mps": 6.0, "v_dn_mps": 3.0, "a_max_mps2": 3.0,
                "f_max_n": 200.0, "f_min_n": 78.0, "max_range_m": 20000.0,
                "max_time_s": 1200.0,
                "budget": {"endurance_remaining_s": 900.0, "range_remaining_m": 4000.0},
            }],
            "tours": [{
                "uav_id": 1, "cycle": 1, "t0": 0.0,
                "start": {"xy": [1.0, 2.0], "point_type": POINT_TYPE_NEST},
                "end": {"id": 9, "at": {"xy": [5.0, 6.0], "point_type": POINT_TYPE_TASK},
                        "service_time_s": 0.0},
            }],
        }
        tmp = Path(tempfile.mkdtemp()) / "uav_config.json"
        tmp.write_text(json.dumps(raw), encoding="utf-8")
        cfg = ExperimentConfig(
            name="test", seeds=(0,),
            scenario=ScenarioSettings(data_dir=Path("."), name=None, nest_count=None,
                                      terrain_step_m=30.0, bbox_margin_m=0.0),
            field=FieldSettings(spacing_m=(4.0, 4.0, 2.0), alt_max_m=120.0, pad_m=0.0,
                                include_static_nofly=False, sample_bias_m=0.0, cache_dir=None),
            corridor=CorridorSettings(half_width_m=None, width_ratio=0.2,
                                      half_width_min_m=20.0, margin_m=10.0),
            params=PlanParams(), endpoint_clearance_m=6.0, endpoint_exempt_radius_m=0.0,
            e_geo_m=0.5, cycle_length_s=240.0, depart_stagger_s=0.0,
            results_dir=Path("."),
            mission_waypoints=MissionWaypointsSettings(15.0, 10.0),
        )
        fleet = load_fleet_input(tmp, cfg, _FakeScenario(), _site())
        req = fleet.requests[0]
        self.assertEqual(req.start_position, (1.0, 2.0, 25.0))
        self.assertEqual(req.stops[0].position, (5.0, 6.0, 50.0))


if __name__ == "__main__":
    unittest.main()
