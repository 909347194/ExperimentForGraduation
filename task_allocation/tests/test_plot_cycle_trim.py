# -*- coding: utf-8 -*-
"""绘图周期轴裁剪（2026-11-05 用户裁定）的回归测试。

用户裁定：「哪周期结束就绘制到哪个周期；不要绘制空周期了」。滚动时域收敛
判定会产生零出动的空周期（新「池空」判据下为末周期 ``stop_reason=pool_empty``；
旧稳态 patience 兜底下为末尾若干个），它们在结果总览里是全零柱、在动画里是
定格空帧。``plot.make_plots`` 在**分发前**把绘图视图的周期轴裁到最后一个有
活动周期。

本文件钉住：裁剪判据、metrics / solution 同源对齐、边界（全空不清空、中间
空周期保留、solution 侧 cost_history 独立生效、缺 metrics 时退回 solution）、
原始数据与审计链（``stop_reason``）不动，以及「裁剪确实发生在分发入口」——
只测纯函数不够，接入点被绕过时纯函数仍全绿。

运行：``pytest task_allocation/tests/test_plot_cycle_trim.py -q``
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import task_allocation.experiments.exp01_smoke.plot as plot_pkg  # noqa: E402
from task_allocation.experiments.exp01_smoke.plot import (  # noqa: E402
    _cycle_has_activity,
    _trim_cycles_for_plot,
)


def _exp01_shape():
    """4 周期 exp01 形态：C1-C3 有执行/航次，C4 是 pool_empty 收敛空周期。"""
    metrics = {
        "n_cycles": 4,
        "summary": {"throughput_per_cycle": 7.5, "n_completed": 30},
        "cycles": [
            {"cycle": 1, "n_executed": 20, "n_completed": 20,
             "completed_ids": list(range(20)),
             "pool_counts": {"n_new": 20, "n_total": 20}},
            {"cycle": 2, "n_executed": 5, "n_completed": 5,
             "completed_ids": list(range(20, 25)),
             "pool_counts": {"n_new": 5, "n_total": 5}},
            {"cycle": 3, "n_executed": 5, "n_completed": 5,
             "completed_ids": list(range(25, 30)),
             "pool_counts": {"n_new": 5, "n_total": 5}},
            {"cycle": 4, "n_executed": 0, "n_completed": 0,
             "completed_ids": [], "stop_reason": "pool_empty",
             "pool_counts": {"n_new": 0, "n_total": 0}},
        ],
    }
    solution = {
        "cycles": [
            {"cycle": 1, "tours": [{"task_ids": [1]}], "cost_history": [1.0, 0.9]},
            {"cycle": 2, "tours": [{"task_ids": [21]}], "cost_history": [1.0, 0.8]},
            {"cycle": 3, "tours": [{"task_ids": [26]}], "cost_history": [1.0, 0.7]},
            {"cycle": 4, "tours": [], "cost_history": []},
        ],
        "tasks": [],
        "nests": [],
    }
    return metrics, solution


def test_trims_trailing_convergence_cycle():
    """收敛判定空周期（C4）判为空并裁掉，两份数据都止于 C3。"""
    m, s = _exp01_shape()
    assert _cycle_has_activity(m["cycles"][3], s["cycles"][3]) is False
    assert all(_cycle_has_activity(m["cycles"][i], s["cycles"][i])
               for i in range(3))
    mv, sv, n = _trim_cycles_for_plot(m, s)
    assert n == 1
    assert [c["cycle"] for c in mv["cycles"]] == [1, 2, 3]
    assert [c["cycle"] for c in sv["cycles"]] == [1, 2, 3]


def test_original_data_untouched_and_shallow_copied():
    """只动绘图视图：原 dict / cycles 列表不变，审计链 stop_reason 保留。"""
    m, s = _exp01_shape()
    m_cycles, s_cycles = m["cycles"], s["cycles"]
    mv, sv, n = _trim_cycles_for_plot(m, s)
    assert n == 1
    assert m["cycles"] is m_cycles and len(m["cycles"]) == 4
    assert s["cycles"] is s_cycles and len(s["cycles"]) == 4
    assert mv is not m and sv is not s
    assert mv["cycles"] is not m["cycles"]
    assert mv["summary"] is m["summary"]  # 浅拷贝，summary 未复制
    assert m["cycles"][3]["stop_reason"] == "pool_empty"


def test_all_idle_cycles_are_not_trimmed():
    """全空时不裁——避免把整张图清空（防御性边界）。"""
    m = {"cycles": [{"cycle": 1, "n_executed": 0},
                    {"cycle": 2, "n_executed": 0}]}
    s = {"cycles": [{"cycle": 1}, {"cycle": 2}]}
    mv, sv, n = _trim_cycles_for_plot(m, s)
    assert n == 0
    assert [c["cycle"] for c in mv["cycles"]] == [1, 2]


def test_middle_idle_cycle_is_kept_only_tail_trimmed():
    """中间零出动但有休眠状态变化的周期保留（时间轴不断），只裁尾部。"""
    m = {"cycles": [
        {"cycle": 1, "n_executed": 5, "completed_ids": [1]},
        {"cycle": 2, "n_executed": 0, "dormant_ids": [9]},
        {"cycle": 3, "n_executed": 3, "completed_ids": [2]},
        {"cycle": 4, "n_executed": 0},
    ]}
    s = {"cycles": [
        {"cycle": 1, "tours": [{"task_ids": [1]}]},
        {"cycle": 2},
        {"cycle": 3, "tours": [{"task_ids": [2]}]},
        {"cycle": 4},
    ]}
    mv, sv, n = _trim_cycles_for_plot(m, s)
    assert n == 1
    assert [c["cycle"] for c in mv["cycles"]] == [1, 2, 3]
    assert [c["cycle"] for c in sv["cycles"]] == [1, 2, 3]


def test_solution_cost_history_alone_marks_cycle_active():
    """零出动但仍有 DMDE 求解的周期不算空周期（右栏有曲线可画）。"""
    m = {"cycles": [
        {"cycle": 1, "n_executed": 5, "completed_ids": [1]},
        {"cycle": 2, "n_executed": 0},
    ]}
    s = {"cycles": [
        {"cycle": 1, "tours": [{"task_ids": [1]}]},
        {"cycle": 2, "tours": [], "cost_history": [1.0, 0.9]},
    ]}
    mv, sv, n = _trim_cycles_for_plot(m, s)
    assert n == 0
    assert [c["cycle"] for c in mv["cycles"]] == [1, 2]


def test_falls_back_to_solution_when_metrics_cycles_missing():
    """metrics 无 cycles 时以 solution 为主轴，仍能裁掉尾部空周期。"""
    m = {"summary": {}}
    s = {"cycles": [
        {"cycle": 1, "tours": [{"task_ids": [1]}]},
        {"cycle": 2, "tours": []},
    ]}
    mv, sv, n = _trim_cycles_for_plot(m, s)
    assert n == 1
    assert [c["cycle"] for c in sv["cycles"]] == [1]


def test_make_plots_applies_trim_before_dispatch(monkeypatch, tmp_path):
    """接入点：图函数收到的确实是裁剪后的周期轴，且原数据仍完整。"""
    m, s = _exp01_shape()
    seen: dict[str, list[int]] = {}

    def fake_func(metrics, solution, out_dir, formats, dpi=200, **kwargs):
        seen["cycles"] = [c["cycle"] for c in metrics["cycles"]]
        seen["sol_cycles"] = [c["cycle"] for c in solution["cycles"]]
        return [out_dir / "fake.png"]

    fake_fig = SimpleNamespace(id="fake", group="report", animated=False,
                               func=fake_func)
    monkeypatch.setattr(plot_pkg, "resolve", lambda **kw: [fake_fig])
    plot_pkg.make_plots(m, s, tmp_path, ("png",), dpi=80)
    assert seen["cycles"] == [1, 2, 3]
    assert seen["sol_cycles"] == [1, 2, 3]
    assert len(m["cycles"]) == 4 and len(s["cycles"]) == 4
