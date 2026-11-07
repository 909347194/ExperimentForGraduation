# -*- coding: utf-8 -*-
"""统一采纳判据契约（2026-11-07 收口，定稿 §11.9）。

背景：交付后成本 polish 曾有 ``path``（航程分量门）与 ``f``（完整目标）
双视图。冒烟 / mid / 正式三档规模共 9 组同 seed 配对（定稿 §11.8）证明
两档交付解逐位相同、``f_only_gain`` 全 0 ⇒ 双视图属于不需要的复杂性，
``path`` 实现分支已删；``cost_polish_accept_mode="path"`` 退化为**兼容
别名**，行为与 ``f`` 完全相同（历史实验产物因此仍可复现）。

本测试用注入的 tour 代价构造最小分歧场景（不依赖 DEM/建筑/DE 搜索）：

====================  ==========  =============
tours                  航程(m)     迟到(s)
====================  ==========  =============
基线 [0, 1]            1000        200
2-opt 候选 [1, 0]      1005        0
====================  ==========  =============

统一 f 评分：``0.1×1005 + 0.01×0 = 100.5 < 0.1×1000 + 0.01×200 − 1e-6 = 102``
⇒ 接受。历史上 path 档会拒绝该移动；现在 path 别名也必须接受，且两档
交付解逐位相同（§11.8 的「零数值变化」在单元级反证）。
"""

from __future__ import annotations

from types import SimpleNamespace

from task_allocation.methods.muas.solvers.route_polish import RoutePolishMixin
from task_allocation.methods.muas.solvers.stage_config import MUASStageConfig

BASE = (0, 1)
CAND = (1, 0)
COST = {BASE: 1000.0, CAND: 1005.0}
LATE = {BASE: (1, 200.0), CAND: (0, 0.0)}


def _polisher(mode: str) -> RoutePolishMixin:
    """最小替身：只提供 _cost_polish_routes 需要的入口，不建场景。"""
    obj = RoutePolishMixin()
    obj.config = MUASStageConfig(
        cost_polish_accept_mode=mode,
        cost_polish_eps=1.0,
        cost_polish_eps_f=1e-6,
        cost_polish_max_rounds=4,
    )
    obj.problem = SimpleNamespace(
        config=SimpleNamespace(lambda_cost=0.1),
        late_weight=lambda: 0.01,
    )
    obj._task_by_id = {
        0: SimpleNamespace(sequence_group=None),
        1: SimpleNamespace(sequence_group=None),
    }
    obj._tour_cost = lambda uid, tids, end: COST[tuple(tids)]  # type: ignore[attr-defined]
    obj._late_key = lambda uid, tids: LATE[tuple(tids)]  # type: ignore[attr-defined]
    obj._range_limit = lambda uid: 1e9  # type: ignore[attr-defined]
    return obj


def _run(mode: str):
    obj = _polisher(mode)
    return obj._cost_polish_routes({0: list(BASE)}, {0: 0})


def test_default_is_f_and_effective_mode_is_always_f():
    """默认档翻 f；无论 requested 写什么，生效判据恒为 f。"""
    assert MUASStageConfig().cost_polish_accept_mode == "f"
    for mode in ("f", "path"):
        cfg = MUASStageConfig(cost_polish_accept_mode=mode)
        assert cfg.cost_polish_accept_mode_effective == "f"


def test_criterion_is_always_f_with_f_units():
    """局部搜索目标恒为 f 评分，阈值恒为 f 单位（不随别名变化）。"""
    for mode in ("f", "path"):
        m, s, eps = _polisher(mode)._cost_polish_criterion()
        assert (m, eps) == ("f", 1e-6)
        assert abs(s(1000.0, 200.0) - 102.0) < 1e-9
        assert abs(s(1005.0, 0.0) - 100.5) < 1e-9
        assert s(1005.0, 0.0) < s(1000.0, 200.0), "航程略增但迟到降更多 ⇒ f 更优"


def test_f_mode_accepts_late_reducing_move():
    """f 判据接受「航程 1000→1005、迟到 200→0」的 2-opt，并留归因量。"""
    work, ends, stats = _run("f")
    assert work == {0: [1, 0]}
    assert ends == {0: 0}
    assert stats["n_2opt"] == 1
    assert stats["n_f_only_gain"] == 1


def test_legacy_path_alias_accepts_same_move():
    """path 只是兼容别名，不再走「航程严格下降」的旧门。"""
    work, _, stats = _run("path")
    assert work == {0: [1, 0]}, "path 别名必须与 f 档行为一致"
    assert stats["n_f_only_gain"] == 1


def test_two_modes_produce_identical_delivered_solution():
    """零数值变化：两档交付解逐位相同（§11.8 正式档结论的单元级佐证）。"""
    assert _run("path")[0] == _run("f")[0]
