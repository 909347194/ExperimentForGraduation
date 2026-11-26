# -*- coding: utf-8 -*-
"""exp01_smoke 实验侧装配的回归测试：配置口径、机体映射、巢高、审计。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from orchestration.experiments.exp01_smoke.support import config as cfg_mod
from orchestration.experiments.exp01_smoke.support import fleet as fleet_mod
from orchestration.experiments.exp01_smoke.support import tasks as tasks_mod
from orchestration.experiments.exp01_smoke.support import upper as upper_mod
from orchestration.experiments.exp01_smoke.support.audit import audit_trace
from orchestration.protocol import CostUpdate, ResolveAttempt, TourTicket


def test_config_loads_and_resolves_repo_root():
    cfg = cfg_mod.load_config()
    assert cfg["mission_waypoints"]["agl_range_m"] == [10.0, 120.0]
    assert cfg["mission_waypoints"]["nest_hover_agl_m"] == 15.0
    assert cfg["data"]["active_nest_ids"] == [0, 1, 2]
    assert (cfg_mod.REPO_ROOT / "data" / "guangzhou" / "dem.tif").is_file()


def test_fleet_one_schema_two_sides():
    rows = fleet_mod.load_uav_config()
    assert sorted(int(r["id"]) for r in rows) == [1, 2, 3, 4, 5, 6]
    vehicles = fleet_mod.lower_vehicles(rows)
    # 机 1 是巢 0 的短航程机：上层巡航 12 m/s、下层上限 13.5 m/s，两套口径并存
    assert vehicles[1].v_max_mps == 13.5
    assert vehicles[1].max_range_m == 5000.0


def test_nest_hover_from_csv():
    cfg = cfg_mod.load_config()
    # 按 base_id 归一：base1 → 机巢 0，不依赖 CSV 行序
    assert upper_mod.nest_hover_heights(cfg) == {0: 15.0, 1: 15.0, 2: 15.0}


def test_solver_budget_and_seed_are_wired():
    """solver 段必须落进 DMDEConfig：漏传会退到 1000 代、随机种子。"""
    cfg = cfg_mod.load_config()
    sv = upper_mod.solver_config(cfg)
    assert (sv.pop_size, sv.max_generations) == (50, 200)
    assert sv.seed == int(cfg["experiment"]["seed"])


def test_single_arm_and_run_dir():
    """只有一档：max_in_cycle 只接受 1；结果目录就是 seed_<seed>。"""
    cfg = cfg_mod.load_config()
    assert cfg["resolve"] == {"max_in_cycle": 1}
    assert cfg_mod.default_run_dir(cfg).name == "seed_0"
    bad = dict(cfg)
    bad["resolve"] = {"max_in_cycle": 2}
    with pytest.raises(ValueError):
        cfg_mod.validate(bad)


def test_tasks_full_precision_and_agl():
    cfg = cfg_mod.load_config()
    items = tasks_mod.load_tasks(cfg)
    assert len(items) == 30
    for t in items:
        assert 10.0 <= float(t["agl_m"]) <= 120.0
        # 全精度：z - ground 必须精确等于 agl_m 的记录值（不得 round）
        assert abs((float(t["z"]) - float(t["ground_z"])) - float(t["agl_m"])) < 1e-9


def test_selection_gate_is_single_not_headcount_quota():
    """阶段一必须按 min{cap(t), n_compute} 开口，而不是 ⌈max_ratio·K⌉。"""
    from task_allocation.methods.rolling.horizon import RollingHorizon
    from task_allocation.methods.selection.capability import effective_cap

    cfg = cfg_mod.load_config()
    sel = RollingHorizon.selection_config_from_dict(cfg.get("selection"))
    assert sel.capability.mode == "single"
    assert sel.capability.n_compute == 32
    # 名额仍会是 8（默认 max_ratio=2.0、K=4），但 single 下不参与合成；
    # 32 > per_cycle=30：闸门不在门口截流，窗口瓶颈留给失败回池链
    assert effective_cap(8, 44, sel.capability) == 32


def test_horizon_uses_single_gate():
    """装配出来的 horizon 必须真的接上 yaml 的 selection 段（防静默退回默认）。"""
    from task_allocation.common.types import UAV

    cfg = cfg_mod.load_config()
    horizon = upper_mod.build_horizon(
        cfg, nests=[], uavs=[UAV(id=4, x=0.0, y=0.0, max_range=12000.0)],
        estimator=None,
    )
    assert horizon.sel_cfg.capability.mode == "single"
    assert horizon.sel_cfg.capability.n_compute == 32


def _task_row(tid: int) -> dict:
    return {"id": tid, "cycle": 1, "agl_m": 20.0, "z": 23.0,
            "sdf_m": 40.0, "prism_m": 40.0}


def _ok_ticket(task_ids, t_end=100.0):
    return SimpleNamespace(
        ok=True, failure="", detail="", t0=0.0, t_end=t_end, duration_s=t_end,
        path_len_m=500.0, task_ids=tuple(task_ids), gate={"ok": True},
    )


def _trace_with(tickets_by_uav):
    plan = SimpleNamespace(
        cycle=1, t_start=0.0, t_end=1800.0,
        report=SimpleNamespace(
            tickets=tickets_by_uav, conflicts_before=0, conflicts_after=0,
            moved={}, unplanned=(),
        ),
        est_cost={int(u): 500.0 for u in tickets_by_uav},
        busy_until={int(u): 100.0 for u in tickets_by_uav},
        overruns=(), rolled_back=(), assignments={},
    )
    return SimpleNamespace(
        cycles=(plan,), executed_ids=(), overrun_cycles=(), failures={},
    )


def test_audit_flags_idle_uav_with_unflown_tasks():
    """闲置本身合法；但「有机没起飞」+「有任务从没飞」并存必须判违规。"""
    cfg = cfg_mod.load_config()
    tasks = [_task_row(i) for i in (101, 102, 103, 104)]
    trace = _trace_with({4: _ok_ticket([101, 102])})
    problems = audit_trace(
        trace, cfg, tasks=tasks, context={}, allowed_offsets={0.0},
        fleet_ids=[3, 4, 5, 6],
    )
    assert any("并存" in p and "闲置机" in p for p in problems)


def test_audit_allows_idle_when_everything_flies():
    """全部任务都飞过、只是有的机没上场 —— 合法，不得报违规。"""
    cfg = cfg_mod.load_config()
    tasks = [_task_row(i) for i in (101, 102, 103, 104)]
    trace = _trace_with({4: _ok_ticket([101, 102, 103, 104])})
    problems = audit_trace(
        trace, cfg, tasks=tasks, context={}, allowed_offsets={0.0},
        fleet_ids=[3, 4, 5, 6],
    )
    assert not any("并存" in p for p in problems)


def test_audit_rejects_endpoint_failure():
    cfg = cfg_mod.load_config()
    ticket = SimpleNamespace(
        ok=False, failure="ENDPOINT_OUT_OF_BAND", t0=0.0, t_end=0.0,
        duration_s=0.0, path_len_m=0.0, gate=None,
    )
    plan = SimpleNamespace(
        cycle=1, t_start=0.0, t_end=1800.0,
        report=SimpleNamespace(tickets={4: ticket}, conflicts_before=0,
                               conflicts_after=0, moved={}, unplanned=(4,)),
        est_cost={4: 1.0}, busy_until={4: 0.0}, overruns=(),
        rolled_back=(101,), assignments={4: (101,)},
    )
    trace = SimpleNamespace(cycles=(plan,), executed_ids=(), overrun_cycles=(),
                            failures={4: "ENDPOINT_OUT_OF_BAND"})
    problems = audit_trace(trace, cfg, context={}, allowed_offsets={0.0})
    assert any("ENDPOINT_OUT_OF_BAND" in p for p in problems)


# ---------------------------------------------------------------------------
# 回灌判据：唯一会"真失败"的两条（其余只是结构性对账）
# ---------------------------------------------------------------------------


def _infeasible_ticket(uav_id: int, edge=(101, 102), *, ok: bool) -> TourTicket:
    """一条把 ``edge`` 判成几何不可行的航次（成功版则不带不可飞边）。"""
    from path_planning.common.plan_types import LegCostRecord
    if ok:
        return TourTicket(
            uav_id=uav_id, ok=True, task_ids=(101, 102), t0=0.0, t_end=100.0,
            duration_s=100.0, path_len_m=500.0, time_source="planned",
            gate={"ok": True},
            leg_costs=(LegCostRecord(from_id=101, to_id=102, length_m=500.0,
                                     duration_s=100.0),),
        )
    return TourTicket(
        uav_id=uav_id, ok=False, task_ids=(101, 102), t0=0.0,
        time_source="failed", failure="RRT_NO_PATH", failure_severity="infeasible",
        detail="段 2/2 断在 (101→102)", gate={"ok": False},
        leg_costs=(LegCostRecord(from_id=101, to_id=102,
                                 failure_code="RRT_NO_PATH",
                                 severity="infeasible"),),
    )


def _resolve_trace(final_tickets, *, survived=(), unresolved=(), n_resolves=1,
                   resolve_error=""):
    attempts = (
        ResolveAttempt(attempt_id=0, snapshot_id="1:base",
                       tickets={4: _infeasible_ticket(4, ok=False)},
                       est_cost={4: 400.0}, residuals={4: 0.0},
                       cost_version="base", cycle=1),
        ResolveAttempt(attempt_id=1, snapshot_id="1:cycle:1",
                       tickets=dict(final_tickets), est_cost={4: 400.0},
                       cost_version="cycle:1", cycle=1),
    )
    plan = SimpleNamespace(
        cycle=1, t_start=0.0, t_end=1800.0,
        report=SimpleNamespace(tickets=dict(final_tickets), conflicts_before=0,
                               conflicts_after=0, moved={}, unplanned=()),
        est_cost={4: 400.0}, busy_until={4: 100.0}, overruns=(),
        rolled_back=(), assignments={4: (101, 102)}, executed_ids=(101, 102),
        attempts=attempts, n_resolves=n_resolves, cost_version="cycle:1",
        cost_update=CostUpdate(infeasible=frozenset({(101, 102)}),
                               source_attempt=1),
        unresolved_infeasible_edges=tuple(unresolved),
        infeasible_but_survived=tuple(survived),
        resolve_error=resolve_error,
    )
    return SimpleNamespace(cycles=(plan,), executed_ids=(), overrun_cycles=(),
                           failures={4: "RRT_NO_PATH"})


def test_audit_flags_ban_that_did_not_take_effect():
    """写进代价的不可飞边原封不动留在最终解里 = 回灌静默失效（多半是键翻错）。"""
    cfg = cfg_mod.load_config()
    tasks = [_task_row(i) for i in (101, 102)]
    trace = _resolve_trace({4: _infeasible_ticket(4, ok=False)},
                           survived=((101, 102),), unresolved=((101, 102),))
    problems = audit_trace(trace, cfg, tasks=tasks, context={},
                           allowed_offsets={0.0}, fleet_ids=[4])
    assert any("回灌未落地" in p for p in problems), problems


def test_audit_ignores_leftover_edges_when_ban_worked():
    """终版甩掉了写进代价的不可飞边；即便另有别的不可飞边残留，也不得判"回灌未落地"。"""
    cfg = cfg_mod.load_config()
    tasks = [_task_row(i) for i in (101, 102)]
    trace = _resolve_trace({4: _infeasible_ticket(4, ok=True)},
                           survived=(), unresolved=())
    problems = audit_trace(trace, cfg, tasks=tasks, context={},
                           allowed_offsets={0.0}, fleet_ids=[4])
    assert not any("回灌未落地" in p for p in problems), problems


def test_audit_flags_resolve_crash():
    """重解抛出异常 = 本周期没重解成，不能因为"解是有效的"就放过。"""
    cfg = cfg_mod.load_config()
    tasks = [_task_row(i) for i in (101, 102)]
    trace = _resolve_trace({4: _infeasible_ticket(4, ok=False)},
                           resolve_error="RuntimeError: 下层重解炸了")
    problems = audit_trace(trace, cfg, tasks=tasks, context={},
                           allowed_offsets={0.0}, fleet_ids=[4])
    assert any("重解抛出异常" in p for p in problems), problems


# ---------------------------------------------------------------------------
# 增量落盘：跑一半挂掉，前面周期的账仍在盘上
# ---------------------------------------------------------------------------


def test_write_meta_stands_before_any_cycle(tmp_path):
    """周期 0 之前先立账：配置/场/估计器/任务指纹不必等跑完才有。"""
    from orchestration.experiments.exp01_smoke.support import report as rep

    cfg = cfg_mod.load_config()
    rep.write_meta(tmp_path, cfg=cfg, tasks=[], field_info={"building_count": 0},
                   estimator_info={"building_count": 0}, context={})
    meta = json.loads((tmp_path / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["experiment"] == cfg["experiment"]["name"]
    assert meta["tasks"]["count"] == 0


def test_write_trace_persists_after_each_cycle(tmp_path):
    from orchestration.experiments.exp01_smoke.support import report as rep
    from orchestration.protocol import CyclePlan, PlannerReport

    plan = CyclePlan(
        cycle=1, t_start=0.0, t_end=1800.0,
        report=PlannerReport(tickets={4: _infeasible_ticket(4, ok=False)}),
        est_cost={4: 400.0}, busy_until={4: 100.0},
        executed_ids=(101, 102),
        unresolved_infeasible_edges=((101, 102),),
    )
    rep.write_trace(tmp_path, [plan], failures={4: "RRT_NO_PATH"})
    doc = json.loads((tmp_path / "loop_trace.json").read_text(encoding="utf-8"))
    assert [c["cycle"] for c in doc["cycles"]] == [1]
    assert doc["cycles"][0]["infeasible_but_survived"] == []
    assert doc["failures"] == {"4": "RRT_NO_PATH"}
    assert not (tmp_path / "loop_trace.json.tmp").exists(), "临时文件必须被换名掉"


def test_tour_ticket_carries_both_nest_ends():
    """起终点巢只能由航次结构给出：起点 = 首项巢，终点 = 末项巢。"""
    from orchestration.adapters.planner_side import PathPlannerAdapter
    from orchestration.protocol import TourOrder, TourStop

    order = TourOrder(
        uav_id=3, t0=0.0, start_position=(0.0, 0.0, 15.0),
        stops=(TourStop(id=0, kind="nest", position=(0.0, 0.0, 15.0)),
               TourStop(id=7, kind="task", position=(100.0, 0.0, 40.0)),
               TourStop(id=2, kind="nest", position=(300.0, 0.0, 15.0))),
        task_ids=(7,),
    )
    assert PathPlannerAdapter._nest_ids(order) == (0, 2)


def test_track_record_is_self_describing(monkeypatch):
    """trajectories.json 的一条航迹自带 起点巢 / 终点巢 / 任务点，排查不必再 join。"""
    from dataclasses import replace

    from orchestration.experiments.exp01_smoke.support import report as rep

    monkeypatch.setattr(rep.track_mod, "sample",
                        lambda traj, **_: {"t0": 0.0, "t_end": 1.0, "t": [0.0, 1.0],
                                           "x": [0.0, 1.0], "y": [0.0, 0.0], "z": [15.0, 15.0]})
    ticket = replace(_infeasible_ticket(4, ok=True), start_nest_id=0, end_nest_id=2)
    rec = rep._track(object(), ticket, {101: "T1", 102: "T2"})
    assert rec["start_nest_id"] == 0
    assert rec["end_nest_id"] == 2
    assert rec["task_ids"] == [101, 102]
    assert rec["tags"] == ["T1", "T2"]
    # 缺票时显式记 None：宁缺不猜
    bare = rep._track(object(), None, {})
    assert bare["start_nest_id"] is None and bare["end_nest_id"] is None
    assert bare["task_ids"] == [] and bare["tags"] == []
