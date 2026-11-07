# -*- coding: utf-8 -*-
"""P4 exp09：终点机巢策略**分层**开关的契约测试（定稿 §4.1）。

背景：``projection_only`` 是复合档（同时剥 ``_consolidate`` 成本合并、
``_best_order`` 的 NN / 航程裁决、终点巢 MCF），拿它做「层 1 最近合法巢
vs 层 3 MCF」对照会污染归因。本测试守护新增的
``end_nest_l1_mode`` / ``end_nest_l3_mode`` + ``phase`` 分流：

1. 默认 ``"auto"`` ⇒ 零行为变化（等价于现行 MCF 口径，``projection_only``
   兼容语义不变）；
2. 显式 ``nearest`` / ``mcf`` 按 ``phase="l1"`` / ``"l3"`` 精确分流；
3. 解码期全部调用点标注 ``l1``，诊断性调用（不传 phase）恒走 auto；
4. 非法取值拒绝、``replace()`` 幂等。
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
ASSIGN = ((0, 0), (0, 1), (1, 2), (1, 3))


# ---------------------------------------------------------------------------
# 1. 配置契约
# ---------------------------------------------------------------------------


def test_config_defaults_are_auto():
    cfg = MUASStageConfig()
    assert cfg.end_nest_l1_mode == "auto"
    assert cfg.end_nest_l3_mode == "auto"
    # 显式分流只作用于指定层，不改另一层
    one = replace(cfg, end_nest_l1_mode="nearest")
    assert one.end_nest_l1_mode == "nearest"
    assert one.end_nest_l3_mode == "auto"
    # replace() 幂等（与 L3b 三键同纪律）
    assert replace(one, end_nest_l1_mode="nearest") == one


@pytest.mark.parametrize("key", ["end_nest_l1_mode", "end_nest_l3_mode"])
def test_config_rejects_unknown_mode(key):
    with pytest.raises(ValueError, match="auto"):
        MUASStageConfig(**{key: "banana"})
    for valid in ("auto", "nearest", "mcf"):
        assert getattr(MUASStageConfig(**{key: valid}), key) == valid


# ---------------------------------------------------------------------------
# 2. 分流逻辑（最小替身，不构造完整场景）
# ---------------------------------------------------------------------------


def _stub(cfg, nests=()):
    """只提供 ``_choose_end_nests`` 早退分支所需的最小状态。"""
    obj = SimpleNamespace(config=cfg, nests=list(nests))
    obj._end_nest_mode_for_phase = (
        lambda phase: DecodeRepairMixin._end_nest_mode_for_phase(obj, phase)
    )
    obj._choose_end_nests_projection = lambda routes: "PROJECTION"
    return obj


def test_phase_resolution():
    obj = _stub(MUASStageConfig(end_nest_l1_mode="nearest", end_nest_l3_mode="mcf"))
    assert obj._end_nest_mode_for_phase("l1") == "nearest"
    assert obj._end_nest_mode_for_phase("l3") == "mcf"
    assert obj._end_nest_mode_for_phase(None) is None
    assert obj._end_nest_mode_for_phase("unknown") is None
    assert _stub(MUASStageConfig())._end_nest_mode_for_phase("l1") is None


def test_nearest_routes_to_projection_mcf_keeps_solver_path():
    """nearest ⇒ 投影；mcf / auto ⇒ 不进投影分支。

    ``nests=[]`` 让 MCF 分支在求解器之前早退（返回 None 终点），
    因此只需判断返回值是否等于投影哨兵，即可验证分流目标。
    """
    call = lambda obj, phase=None: DecodeRepairMixin._choose_end_nests(  # noqa: E731
        obj, {0: [1]}, phase=phase
    )

    # 层 1 显式 nearest ⇒ 投影
    obj_l1 = _stub(MUASStageConfig(end_nest_l1_mode="nearest"))
    assert call(obj_l1, phase="l1") == "PROJECTION"
    # 同配置但层 3 显式 mcf ⇒ 不进投影
    obj_l3 = _stub(
        MUASStageConfig(end_nest_l1_mode="nearest", end_nest_l3_mode="mcf")
    )
    assert call(obj_l3, phase="l3") == {0: None}
    # 未标注 phase（诊断调用）⇒ auto，即使 l1 显式设了 nearest 也不受影响
    assert call(obj_l1) == {0: None}
    # projection_only 兼容语义不变
    assert call(_stub(MUASStageConfig(projection_only=True)), phase="l1") == "PROJECTION"
    # 显式 mcf 可覆盖 projection_only（实验档语义）
    assert (
        call(
            _stub(MUASStageConfig(projection_only=True, end_nest_l1_mode="mcf")),
            phase="l1",
        )
        == {0: None}
    )


# ---------------------------------------------------------------------------
# 3. 真实 evaluator：调用点标注 + 默认档等价于 MCF
# ---------------------------------------------------------------------------


def _scenario():
    nests = [
        Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=2, available=True),
        Nest(id=1, x=4000.0, y=0.0, z=0.0, capacity=2, available=True),
    ]
    uavs = [
        UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=15000.0, speed=12.0, nest_id=0),
        UAV(id=1, x=4000.0, y=0.0, z=0.0, remaining_range=15000.0, speed=11.0, nest_id=1),
    ]
    tasks = [
        Task(id=0, x=800.0, y=200.0, z=0.0, reward=5.0, earliest=0.0, latest=1500.0),
        Task(id=1, x=1600.0, y=-300.0, z=0.0, reward=4.0, earliest=0.0, latest=2000.0),
        Task(id=2, x=2400.0, y=400.0, z=0.0, reward=6.0, earliest=0.0, latest=1800.0),
        Task(id=3, x=3100.0, y=-200.0, z=0.0, reward=3.0, earliest=0.0, latest=2200.0),
    ]
    return tasks, uavs, nests


def _make_evaluator(**stage_kwargs):
    tasks, uavs, nests = _scenario()
    cb = build_pairwise_costs(
        tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY
    )
    problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests=nests, pairwise_cost=cb.provider.pairwise,
        config=MUASProblemConfig(),
    )
    ev = MUASAssignmentEvaluator(
        problem=problem, participating=uavs, tasks=tasks, nests=nests,
        fleet_uavs=uavs, config=MUASStageConfig(**stage_kwargs),
    )
    return ev


def _signature(sol):
    return [
        (t.uav_id, tuple(t.task_ids), t.end_nest_id)
        for t in sol.tours
    ]


def test_decode_call_sites_are_marked_l1(monkeypatch):
    orig = DecodeRepairMixin._choose_end_nests
    phases: list[str | None] = []

    def spy(self, routes, *, phase=None):
        phases.append(phase)
        return orig(self, routes, phase=phase)

    monkeypatch.setattr(DecodeRepairMixin, "_choose_end_nests", spy)
    _make_evaluator().build_solution(ASSIGN)
    assert phases, "解码期应至少调用一次终点巢分配"
    assert set(phases) == {"l1"}, f"解码期调用点未全部标注 l1：{phases}"


def test_auto_is_equivalent_to_explicit_mcf():
    """默认 auto 与显式双层 mcf 逐位一致 ⇒ 新开关零行为变化。"""
    sig_auto = _signature(_make_evaluator().build_solution(ASSIGN))
    sig_mcf = _signature(
        _make_evaluator(
            end_nest_l1_mode="mcf", end_nest_l3_mode="mcf"
        ).build_solution(ASSIGN)
    )
    assert sig_auto == sig_mcf
    assert sig_auto, "应构造出非空交付解"
