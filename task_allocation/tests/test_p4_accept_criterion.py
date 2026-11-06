# -*- coding: utf-8 -*-
"""P4 A1 修复契约：f 档局部搜索必须能看到 path 档看不到的候选。

背景（审查 A1）：修复前 ``post_cost_polish`` 的成本门恒为「航程严格下降」，
迟到门又要求迟到不增，于是产出的候选必然同时满足 ``accept_cost_polish_view``
与 ``accept_f_view`` ⇒ 两个采纳判据永远同真同假，A/B 在结构上测不出任何东西。

修复：``mode="f"`` 时局部搜索改按 ``λ·航程 + w_late·迟到`` 判改进，允许
「航程略增、迟到降得更多」的移动；``mode="path"`` 走原代码路径（bit-exact）。

本测试用注入的 tour 代价构造一个最小分歧场景（不依赖 DEM/建筑/DE 搜索）：

====================  ==========  =============
tours                  航程(m)     迟到(s)
====================  ==========  =============
基线 [0, 1]            1000        200
2-opt 候选 [1, 0]      1005        0
====================  ==========  =============

* path 档：``1005 >= 1000 - 1`` ⇒ 拒绝，零接受；
* f 档：``0.1×1005 + 0.01×0 = 100.5 < 0.1×1000 + 0.01×200 - 1e-6 = 102``
  ⇒ 接受，且 ``n_f_only_gain = 1``。

两档交付解因此不同 ⇒ 判据对照不再冗余。
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


def test_criterion_units():
    """两档评分函数与阈值单位不同，禁止混用。"""
    p = _polisher("path")
    f = _polisher("f")
    m_p, s_p, e_p = p._cost_polish_criterion()
    m_f, s_f, e_f = f._cost_polish_criterion()
    assert (m_p, e_p) == ("path", 1.0)
    assert (m_f, e_f) == ("f", 1e-6)
    # path 档评分与迟到无关
    assert s_p(1005.0, 0.0) == 1005.0
    assert s_p(1005.0, 200.0) == 1005.0
    # f 档：0.1·m + 0.01·s
    assert abs(s_f(1000.0, 200.0) - 102.0) < 1e-9
    assert abs(s_f(1005.0, 0.0) - 100.5) < 1e-9
    assert s_f(1005.0, 0.0) < s_f(1000.0, 200.0), "航程略增但迟到降更多 ⇒ f 更优"


def test_path_mode_rejects_and_is_bit_exact():
    """path 档零接受、解逐位不变（与修复前语义一致）。"""
    work, ends, stats = _run("path")
    assert work == {0: [0, 1]}
    assert ends == {0: 0}
    assert stats["n_2opt"] + stats["n_oropt"] == 0
    assert "n_f_only_gain" not in stats, "path 档不得新增诊断键（bit-exact）"


def test_f_mode_accepts_path_would_reject():
    """f 档接受该移动，并如实记为「只有 f 判据会采纳」。"""
    work, ends, stats = _run("f")
    assert work == {0: [1, 0]}, "f 档应接受航程 1000→1005、迟到 200→0 的 2-opt"
    assert stats["n_2opt"] == 1
    assert stats["n_f_only_gain"] == 1


def test_two_modes_produce_different_delivered_solution():
    """结构性结论：两档交付解不同 ⇒ 判据对照不再冗余。"""
    path_work, _, _ = _run("path")
    f_work, _, _ = _run("f")
    assert path_work != f_work
