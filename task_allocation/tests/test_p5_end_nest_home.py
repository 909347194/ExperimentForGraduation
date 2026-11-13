# -*- coding: utf-8 -*-
"""P5 exp11：终点机巢「强制返原巢」臂（``home``）的契约测试。

背景：exp09/exp10 的两臂（最近合法巢 / MCF）**都允许异巢降落**，
缺的正是「必须返原巢」这一对照臂。本测试守护新增的
``end_nest_l1_mode`` / ``end_nest_l3_mode`` = ``"home"``：

1. 配置层接受 ``home``、仍拒绝非法取值，``replace()`` 幂等；
2. ``home`` 在 ``phase="l1"`` / ``"l3"`` 上精确分流，且**优先于**
   ``projection_only``（复合档不得改写对照臂语义）；
3. 终点恒等于 ``uav.nest_id``（不比较费用、不改派），原巢缺失时置 None；
4. 与 MCF 臂可分辨：两巢场景下 MCF 会改投更近的巢，``home`` 不会。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

from task_allocation.common.types import Nest, Task, UAV  # noqa: E402
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.decode_repair import (  # noqa: E402
    DecodeRepairMixin,
)
from task_allocation.methods.muas.solvers.evaluator import (  # noqa: E402
    MUASAssignmentEvaluator,
)
from task_allocation.methods.muas.solvers.stage_config import (  # noqa: E402
    MUASStageConfig,
)

NEST_KEY = 1_000_000


# ---------------------------------------------------------------------------
# 1. 配置契约
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["end_nest_l1_mode", "end_nest_l3_mode"])
def test_config_accepts_home(key):
    for valid in ("auto", "nearest", "mcf", "home"):
        cfg = MUASStageConfig(**{key: valid})
        assert getattr(cfg, key) == valid
    one = replace(MUASStageConfig(**{key: "home"}), end_nest_l1_mode="home")
    assert one.end_nest_l1_mode == "home"
    with pytest.raises(ValueError, match="auto"):
        MUASStageConfig(**{key: "origin"})


# ---------------------------------------------------------------------------
# 2. 分流逻辑（最小替身，不构造完整场景）
# ---------------------------------------------------------------------------


def _stub(cfg, nests=(), uavs=()):
    """只提供 ``_choose_end_nests`` 各分支所需的最小状态。"""
    obj = SimpleNamespace(
        config=cfg,
        nests=list(nests),
        fleet_uavs=list(uavs),
        _uav_by_id={u.id: u for u in uavs},
        excluded_nest_ids=set(),
    )
    obj._end_nest_mode_for_phase = (
        lambda phase: DecodeRepairMixin._end_nest_mode_for_phase(obj, phase)
    )
    obj._choose_end_nests_projection = lambda routes: "PROJECTION"
    obj._choose_end_nests_home = (
        lambda routes: DecodeRepairMixin._choose_end_nests_home(obj, routes)
    )
    obj._led_record = lambda *a, **k: None
    return obj


def test_home_routes_before_projection_and_solver():
    uav = UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=10000.0, nest_id=0)
    obj = _stub(
        MUASStageConfig(end_nest_l1_mode="home", end_nest_l3_mode="home"),
        uavs=[uav],
    )
    assert DecodeRepairMixin._choose_end_nests(obj, {0: [1]}, phase="l1") == {0: 0}
    assert DecodeRepairMixin._choose_end_nests(obj, {0: [1]}, phase="l3") == {0: 0}


def test_home_overrides_projection_only():
    """``projection_only`` 是复合档；显式 home 不得被它改写成「最近合法巢」。"""
    uav = UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=10000.0, nest_id=1)
    obj = _stub(MUASStageConfig(projection_only=True, end_nest_l1_mode="home"),
                uavs=[uav])
    got = DecodeRepairMixin._choose_end_nests(obj, {0: [1]}, phase="l1")
    assert got == {0: 1}


def test_home_ignores_idle_uavs_and_keeps_missing_home():
    uav_ok = UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=10000.0, nest_id=2)
    uav_no_home = UAV(id=1, x=0.0, y=0.0, z=0.0, remaining_range=10000.0,
                      nest_id=None)
    obj = _stub(MUASStageConfig(end_nest_l1_mode="home"),
                uavs=[uav_ok, uav_no_home])
    got = DecodeRepairMixin._choose_end_nests(
        obj, {0: [1], 1: [2], 2: []}, phase="l1"
    )
    assert got == {0: 2, 1: None}  # 空航次不占终点；原巢缺失置 None


# ---------------------------------------------------------------------------
# 3. 真实 evaluator：home 与 mcf 可分辨（MCF 会改投更近的巢）
# ---------------------------------------------------------------------------


def _scenario():
    nests = [
        Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=2, available=True),
        Nest(id=1, x=4000.0, y=0.0, z=0.0, capacity=2, available=True),
    ]
    tasks = [
        Task(id=1, x=3900.0, y=0.0, z=0.0, reward=5.0, earliest=0.0, latest=2000.0),
        Task(id=2, x=100.0, y=0.0, z=0.0, reward=5.0, earliest=0.0, latest=2000.0),
    ]
    uavs = [
        UAV(id=0, x=nests[0].x, y=nests[0].y, z=0.0, remaining_range=10000.0,
            speed=12.0, nest_id=0),
        UAV(id=1, x=nests[1].x, y=nests[1].y, z=0.0, remaining_range=10000.0,
            speed=12.0, nest_id=1),
    ]
    cb = build_pairwise_costs(
        tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY
    )
    problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests=nests, pairwise_cost=cb.provider.pairwise,
        config=MUASProblemConfig(),
    )
    return problem, uavs


def test_home_keeps_origin_where_mcf_would_switch():
    problem, uavs = _scenario()
    mixin = DecodeRepairMixin()
    mixin.problem = problem
    mixin.nests = list(problem.nests)
    mixin.fleet_uavs = uavs
    mixin._uav_by_id = {u.id: u for u in uavs}
    mixin.excluded_nest_ids = set()
    mixin.config = MUASStageConfig(end_nest_l1_mode="home")
    mixin._led_record = lambda *a, **k: None

    routes = {0: [1], 1: [2]}
    home = mixin._choose_end_nests(routes, phase="l1")
    assert home == {0: 0, 1: 1}

    mixin.config = MUASStageConfig(end_nest_l1_mode="mcf", end_nest_l3_mode="mcf")
    mcf = mixin._choose_end_nests(routes, phase="l1")
    # UAV0 末任务在巢 1 旁 ⇒ MCF 允许它落到巢 1（异巢），home 不允许
    assert mcf[0] == 1
