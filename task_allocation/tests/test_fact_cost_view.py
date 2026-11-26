# -*- coding: utf-8 -*-
"""``FactCostView`` 的聚焦测试：事实回灌只剩"写数值"这一条路。

钉死的口径：
  1. 实测长度按**底表同一权重**折成代价：``c = ℓ_real · w_目标``；
  2. 不可飞边写成 :data:`INFEASIBLE_COST`，且优先于实测值；
  3. ``reset`` 清空两者，回到底表原值。
"""

from __future__ import annotations

import pytest

from task_allocation.methods.muas.cost.fact_cost_view import (
    INFEASIBLE_COST,
    FactCostView,
)


class _Base(dict):
    """带 ``weight_of`` 的底表替身：权重只依赖目标节点。"""

    def __init__(self, values: dict[tuple[int, int], float], weights: dict[int, float]):
        super().__init__(values)
        self._weights = dict(weights)

    def weight_of(self, node: int) -> float:
        return float(self._weights.get(int(node), 1.0))


def _view(weights: dict[int, float] | None = None) -> FactCostView:
    base = _Base(
        {(0, 1): 100.0, (1, 2): 200.0, (2, 0): 50.0},
        weights or {2: 3.0},
    )
    return FactCostView(base)


def test_measured_length_is_scaled_by_target_weight() -> None:
    """实测长度不是直接的代价：必须乘上目标节点权重。"""
    view = _view()
    view.apply_facts({(1, 2): 400.0})

    assert view[(1, 2)] == pytest.approx(400.0 * 3.0)
    assert view.base_estimate((1, 2)) == pytest.approx(200.0), "底表原值不被改写"


def test_measured_length_without_weight_table_is_identity() -> None:
    """底表没有 ``weight_of``（无收益加权）时，长度即代价。"""
    view = FactCostView({(1, 2): 200.0})
    view.apply_facts({(1, 2): 400.0})

    assert view[(1, 2)] == pytest.approx(400.0)


def test_infeasible_edge_outranks_measured_value() -> None:
    """同一条边既量到长度又判不可飞时，不可飞优先（否则会被"绕过"）。"""
    view = _view()
    view.apply_facts({(1, 2): 400.0}, infeasible={(1, 2)})

    assert view[(1, 2)] == pytest.approx(INFEASIBLE_COST)
    assert view.infeasible == frozenset({(1, 2)})


def test_reset_clears_facts_and_infeasible() -> None:
    view = _view()
    view.apply_facts({(1, 2): 400.0}, infeasible={(0, 1)})
    assert view.as_dict()["n_facts"] == 1
    assert view.as_dict()["n_infeasible"] == 1

    view.reset()

    assert view[(1, 2)] == pytest.approx(200.0), "回到底表原值"
    assert view[(0, 1)] == pytest.approx(100.0)
    assert view.as_dict()["n_facts"] == 0
    assert view.as_dict()["n_infeasible"] == 0


def test_as_dict_lists_infeasible_edges() -> None:
    view = _view()
    view.apply_facts({}, infeasible=[(2, 0), (0, 1)])

    assert view.as_dict()["infeasible"] == ["0->1", "2->0"]
