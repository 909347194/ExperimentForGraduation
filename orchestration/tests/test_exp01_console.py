# -*- coding: utf-8 -*-
"""exp01_smoke 控制台输出回归：只验呈现顺序与阶段提示，不跑真场真规划。"""

from __future__ import annotations

from types import SimpleNamespace

from orchestration.experiments.exp01_smoke import run as run_mod
from orchestration.experiments.exp01_smoke.support import (
    estimator as estimator_mod,
    field as field_mod,
    fleet as fleet_mod,
    report as report_mod,
    tasks as tasks_mod,
    upper as upper_mod,
)
from orchestration.experiments.exp01_smoke.support import console as cli
from orchestration.protocol import CostUpdate, CyclePlan, PlannerReport, TourTicket


class _FakeAllocator:
    def __init__(self, horizon, tags=None):
        self.horizon = horizon
        self.tags = tags

    def prepare(self, new_tasks=None):
        return SimpleNamespace(executed_ids=(1, 2), orders=(SimpleNamespace(uav_id=4, task_ids=(1, 2)),),
                               est_cost={4: 100.0}, cycle=1, t_start=0.0,
                               t_end=1800.0, cost_version="v0")

    def apply_cost_update(self, update):
        return update

    def resolve(self):
        return SimpleNamespace(executed_ids=(1, 2), orders=(SimpleNamespace(uav_id=4, task_ids=(1, 2)),),
                               est_cost={4: 100.0}, cycle=1, t_start=0.0,
                               t_end=1800.0, cost_version="v1")

    def commit(self):
        return SimpleNamespace(executed_ids=(1, 2), orders=(SimpleNamespace(uav_id=4, task_ids=(1, 2)),),
                               est_cost={4: 100.0}, cycle=1, t_start=0.0,
                               t_end=1800.0, cost_version="v1")

    def allowed_stagger_s(self):
        return (10.0, 20.0)


class _FakeStagger:
    """真错峰装饰器的替身：包在打印代理外，提供 allowed_stagger_s。"""

    def __init__(self, inner, stagger_s):
        self.inner = inner
        self.stagger_s = stagger_s

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def allowed_stagger_s(self):
        return {0.0, 10.0, 20.0}


class _FakePlanner:
    def __init__(self, planner, **kwargs):
        self.last_trajectories = {}

    def plan_cycle(self, orders):
        tickets = {4: TourTicket(uav_id=4, ok=True, t0=0.0, t_end=12.0,
                                 task_ids=(1, 2),
                                 path_len_m=100.0, duration_s=12.0)}
        return PlannerReport(tickets=tickets)

    def reset_cycle(self, uav_ids):
        return None

    def busy_until(self, uav_id):
        return 12.0

    def prune(self, now):
        return ()


class _FakeDriver:
    def __init__(self, allocator, planner, **kwargs):
        self.allocator = allocator
        self.planner = planner

    def resolve_blocker(self):
        return None

    def step_one(self, new_tasks=None):
        snap = self.allocator.prepare(new_tasks)
        first = self.planner.plan_cycle(snap.orders)
        update = CostUpdate(facts={(1, 2): 100.0}, infeasible=frozenset({(2, 3)}))
        self.allocator.apply_cost_update(update)
        snap = self.allocator.resolve()
        final = self.planner.plan_cycle(snap.orders)
        return CyclePlan(
            cycle=1, t_start=0.0, t_end=1800.0, report=final,
            est_cost={4: 100.0}, busy_until={4: 12.0},
            executed_ids=(1, 2), assignments={4: (1, 2)},
            attempts=(SimpleNamespace(tickets=first.tickets),
                      SimpleNamespace(tickets=final.tickets)),
            cost_update=update, cost_version="v1",
        )


def test_console_has_setup_process_and_result_sections(monkeypatch, capsys):
    tasks = [
        {"id": 1, "cycle": 1, "x": 0.0, "y": 0.0, "z": 12.0, "service_time_s": 15.0},
        {"id": 2, "cycle": 1, "x": 1.0, "y": 1.0, "z": 12.0, "service_time_s": 15.0},
    ]
    nest = SimpleNamespace(id=0, x=0.0, y=0.0, z=15.0,
                           meta={"ground_z": 0.0, "active": True})

    monkeypatch.setattr(tasks_mod, "load_tasks", lambda cfg: tasks)
    monkeypatch.setattr(tasks_mod, "arrivals", lambda cfg, tasks, nests: [tasks])
    monkeypatch.setattr(fleet_mod, "load_uav_config",
                        lambda: [{"id": 4, "enabled": True}])
    monkeypatch.setattr(fleet_mod, "lower_vehicles", lambda rows: ["v4"])
    monkeypatch.setattr(fleet_mod, "upper_uavs", lambda rows, nests: [nest])
    monkeypatch.setattr(field_mod, "load_guangzhou",
                        lambda cfg: SimpleNamespace(nests=[nest], buildings=None))
    monkeypatch.setattr(field_mod, "load_or_build_field",
                        lambda cfg, sc: ("field", {"source": "cache", "elapsed_s": 0.1}))
    monkeypatch.setattr(field_mod, "prisms_of", lambda sc, field, cfg: [])
    monkeypatch.setattr(field_mod, "build_planner",
                        lambda cfg, sc, field, prisms, vehicles: ("planner", None, {}))
    monkeypatch.setattr(estimator_mod, "build_estimator",
                        lambda cfg, sc: ("estimator", {"building_count": 0}))
    monkeypatch.setattr(upper_mod, "build_nests", lambda cfg, sc: ([nest], {0: nest}))
    monkeypatch.setattr(upper_mod, "build_horizon", lambda cfg, nests, uavs, est: "horizon")
    monkeypatch.setattr(upper_mod, "DepartStaggerAllocator", _FakeStagger)
    monkeypatch.setattr(run_mod, "RollingHorizonAdapter", _FakeAllocator)
    monkeypatch.setattr(run_mod, "PathPlannerAdapter", _FakePlanner)
    monkeypatch.setattr(run_mod, "LoopDriver", _FakeDriver)
    seen = {}

    def _audit(trace, cfg, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(run_mod, "audit_trace", _audit)
    monkeypatch.setattr(report_mod, "write_meta", lambda *a, **k: {})
    monkeypatch.setattr(report_mod, "write_trace", lambda *a, **k: {})
    monkeypatch.setattr(report_mod, "write_all", lambda *a, **k: {
        "cycles": 1, "tickets_ok": 1, "tickets_total": 1,
        "assigned_tasks": 2, "flown_tasks": 2, "completion_rate": 1.0,
        "task_total": 2, "never_assigned_ids": [], "unflown_ids": [],
        "idle_uavs": [], "never_dispatched_uavs": [],
        "total_resolves": 1, "infeasible_edges_written": 1,
        "measured_edges_written": 1, "unresolved_infeasible_edges": 0,
    })

    assert run_mod.main(["--max-cycles", "1"]) == 0
    assert seen["allowed_offsets"] == {0.0, 10.0, 20.0}, (
        "错峰值必须来自 horizon 本体；打印代理只实现 AllocatorPort，不转发这个属性"
    )
    text = capsys.readouterr().out
    for piece in (
        "1. 实验设定", "任务", "任务分配", "航迹规划",
        "2. 准备", "[1/5] 载入场景数据", "[5/5] 编排检查",
        "3. 逐周期过程", "任务分配  冻结周期并求解中",
        "航迹规划  首版", "事实回灌  写入不可飞边 1 条",
        "航迹规划  回灌后终版", "分配结果", "周期结果",
        "4. 实验结果", "任务到达", "5. 审计", "结论", "PASS",
    ):
        assert piece in text, piece


def test_progress_proxies_are_transparent_to_real_driver(capsys):
    """代理只加打印：真 LoopDriver 的能力探测与周期推进必须照常工作。"""
    from orchestration import LoopDriver

    alloc = cli.AllocatorProgress(_FakeAllocator("h"), {1: "T1", 2: "T2"})
    planner = cli.PlannerProgress(_FakePlanner("p"), {1: "T1", 2: "T2"})
    driver = LoopDriver(alloc, planner, max_in_cycle=1)
    assert driver.resolve_blocker() is None
    plan = driver.step_one([])
    assert plan.assignments == {4: (1, 2)}
    text = capsys.readouterr().out
    assert "任务分配  冻结周期并求解中" in text
    assert "航迹规划  首版完成" in text


def test_compact_ids_and_assignments():
    tags = {i: f"T{i}" for i in range(1, 13)}
    assert cli.compact_ids([1, 2], tags) == "T1,T2"
    assert cli.compact_ids(list(range(1, 13)), tags, limit=3) == "T1,T2,T3… 另 9 个"
    assert cli.assignments_text({4: (1, 2)}, tags) == "机4 2站[T1,T2]"


def test_real_depart_stagger_staggers_same_start_orders():
    """真装饰器必须把同起点机组按机号错开，且给审计提供允许偏移集合。"""
    from orchestration.experiments.exp01_smoke.support.upper import (
        DepartStaggerAllocator,
    )
    from orchestration.protocol import CycleSnapshot, TourOrder, TourStop

    stops = (TourStop(id=1, kind="task", position=(0.0, 0.0, 12.0)),
             TourStop(id=0, kind="nest", position=(0.0, 0.0, 15.0)))
    orders = tuple(
        TourOrder(uav_id=uid, t0=0.0, start_position=(0.0, 0.0, 15.0),
                  stops=stops, meta={"cycle": 1}, task_ids=(1,))
        for uid in (6, 4, 5)
    )
    snap = CycleSnapshot(cycle=1, t_start=0.0, t_end=1800.0, orders=orders)

    class _Inner:
        def prepare(self, new_tasks=None):
            return snap

    wrapped = DepartStaggerAllocator(_Inner(), 10.0)
    out = wrapped.prepare([])
    assert {o.uav_id: o.t0 for o in out.orders} == {4: 0.0, 5: 10.0, 6: 20.0}
    assert 0.0 in wrapped.allowed_stagger_s()
    assert 20.0 in wrapped.allowed_stagger_s()
