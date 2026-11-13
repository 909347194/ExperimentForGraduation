# -*- coding: utf-8 -*-
"""exp03_event_rolling 配置装配：config.yaml → 各模块配置对象 + 事件因子解析。"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import paths  # noqa: F401

try:
    import yaml
except ImportError:
    yaml = None

from task_allocation.methods.muas.problem import ConstraintConfig, MUASProblemConfig
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig
from task_allocation.methods.rolling.event_trigger import EventTriggerConfig
from task_allocation.methods.rolling.horizon import HorizonConfig, RollingHorizon
from task_allocation.methods.selection.pipeline import SelectionConfig

__all__ = ["ConfigBundle", "load_config", "build_config_bundle", "apply_factor"]


def load_config(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("需要 PyYAML")
    with Path(path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"config.yaml 顶层必须是映射：{path}")
    return data


def _section(raw: Mapping[str, Any], name: str) -> dict[str, Any]:
    v = raw.get(name)
    return dict(v) if isinstance(v, Mapping) else {}


# ---------------------------------------------------------------------------
# 事件因子定义
# ---------------------------------------------------------------------------

#: 因子 → 事件注入配置（写入 events.schedule + rolling.events 开关）
FACTOR_DEFS: dict[str, dict[str, Any]] = {
    "E0": {
        "description": "无事件对照",
        "events": {"enabled": False},
        "schedule": {"faults": [], "burst_tasks": [], "nest_closures": []},
    },
    "E1": {
        "description": "UAV 故障（t=6，busiest）",
        "events": {"enabled": True, "on_uav_fault": True, "on_nest_unavailable": False, "on_urgent_task": False},
        "schedule": {
            "faults": [{"cycle": 6, "uav": "busiest"}],
            "burst_tasks": [],
            "nest_closures": [],
        },
    },
    "E2": {
        "description": "突发高优先级任务（t=6，2 个 burst）",
        "events": {"enabled": True, "on_uav_fault": False, "on_nest_unavailable": False, "on_urgent_task": True},
        "schedule": {
            "faults": [],
            "burst_tasks": [{"cycle": 6, "n": 2, "reward": 9.5, "deadline_cycles": 1.0}],
            "nest_closures": [],
        },
    },
    "E3": {
        "description": "机巢不可用（t=6，巢 0）",
        "events": {"enabled": True, "on_uav_fault": False, "on_nest_unavailable": True, "on_urgent_task": False},
        "schedule": {
            "faults": [],
            "burst_tasks": [],
            "nest_closures": [{"cycle": 6, "nest_id": 0}],
        },
    },
    "E4": {
        "description": "组合（E1@6 + E3@10）",
        "events": {"enabled": True, "on_uav_fault": True, "on_nest_unavailable": True, "on_urgent_task": False},
        "schedule": {
            "faults": [{"cycle": 6, "uav": "busiest"}],
            "burst_tasks": [],
            "nest_closures": [{"cycle": 10, "nest_id": 0}],
        },
    },
}


def apply_factor(cfg: dict[str, Any], factor: str) -> dict[str, Any]:
    """把因子定义合入 config dict（events.schedule + events 开关）。"""
    if factor not in FACTOR_DEFS:
        raise ValueError(f"未知因子 {factor!r}，可选：{sorted(FACTOR_DEFS)}")
    fd = FACTOR_DEFS[factor]
    cfg = copy.deepcopy(cfg)
    ev = cfg.setdefault("events", {})
    ev["schedule"] = copy.deepcopy(fd["schedule"])
    for k, v in fd.get("events", {}).items():
        ev[k] = v
    # rolling.events 检测开关与 events 段同步
    rolling = cfg.setdefault("rolling", {})
    r_ev = rolling.setdefault("events", {})
    for k in ("enabled", "on_uav_fault", "on_nest_unavailable", "on_urgent_task"):
        if k in ev:
            r_ev[k] = ev[k]
    # E0 时 fault 注入面也清空
    if factor == "E0":
        rolling["faults"] = {}
    else:
        faults = fd["schedule"].get("faults", [])
        rolling["faults"] = {f["cycle"]: [f["uav"]] for f in faults}
    # nest_closures → HorizonConfig（build.py 会读 events.schedule.nest_closures）
    return cfg


# ---------------------------------------------------------------------------
# 装配结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigBundle:
    raw: dict[str, Any]
    problem_model: MUASProblemConfig
    stage_model: MUASStageConfig
    solver: DMDEConfig
    event: EventTriggerConfig
    horizon: HorizonConfig
    selection: SelectionConfig
    nest_offset: int
    factor: str = ""

    @property
    def experiment(self) -> dict[str, Any]:
        return _section(self.raw, "experiment")

    @property
    def data(self) -> dict[str, Any]:
        return _section(self.raw, "data")

    @property
    def problem(self) -> dict[str, Any]:
        return _section(self.raw, "problem")

    @property
    def tasks(self) -> dict[str, Any]:
        return _section(self.raw, "tasks")

    @property
    def cost(self) -> dict[str, Any]:
        return _section(self.raw, "cost")

    @property
    def rolling(self) -> dict[str, Any]:
        return _section(self.raw, "rolling")

    @property
    def events(self) -> dict[str, Any]:
        return _section(self.raw, "events")

    @property
    def run(self) -> dict[str, Any]:
        return _section(self.raw, "run")

    @property
    def plot(self) -> dict[str, Any]:
        return _section(self.raw, "plot")

    @property
    def seed(self) -> int:
        return int(self.experiment.get("seed", 42))

    @property
    def n_cycles(self) -> int:
        return int(self.horizon.n_cycles)

    @property
    def make_plots(self) -> bool:
        return bool(self.run.get("make_plots", True))

    @property
    def save_results(self) -> bool:
        return bool(self.run.get("save_results", True))

    @property
    def verbose(self) -> bool:
        return bool(self.run.get("verbose", True))


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------


def build_config_bundle(cfg: Mapping[str, Any], factor: str = "") -> ConfigBundle:
    exp = _section(cfg, "experiment")
    seed = exp.get("seed", 42)

    m = _section(cfg, "muas")
    cons_raw = _section(cfg, "constraints")
    constraints = ConstraintConfig.from_dict(cons_raw)
    if "w_nest_capacity" not in cons_raw:
        constraints.w_nest_capacity = float(m.get("w_nest_capacity", 1.0))
    problem_model = MUASProblemConfig(
        allow_unassigned=bool(m.get("allow_unassigned", True)),
        allow_idle_uav=bool(m.get("allow_idle_uav", True)),
        big_m=(None if m.get("big_m") is None else float(m["big_m"])),
        lambda_cost=float(m.get("lambda_cost", 1.0e-2)),
        w_penalty=float(m.get("w_penalty", 1.0)),
        w_violation=(None if m.get("w_violation") is None else float(m["w_violation"])),
        w_nest_capacity=float(m.get("w_nest_capacity", 1.0)),
        w_hard_violation=(None if m.get("w_hard_violation") is None else float(m["w_hard_violation"])),
        w_range_excess=(None if m.get("w_range_excess") is None else float(m["w_range_excess"])),
        hard_violation_kappa=float(m.get("hard_violation_kappa", 10.0)),
        default_service_time=float(m.get("default_service_time", 0.0)),
        late_reward_decay_seconds=(None if m.get("late_reward_decay_seconds") is None else float(m["late_reward_decay_seconds"])),
        coverage_audit=bool(m.get("coverage_audit", m.get("lexicographic_coverage", False))),
        w_skip_violation=(None if m.get("w_skip_violation") is None else float(m["w_skip_violation"])),
        constraints=constraints,
    )
    stage_model = MUASStageConfig(
        max_participating_uavs=m.get("max_participating_uavs", None),
        sortie_budget=(None if m.get("sortie_budget") is None else int(m["sortie_budget"])),
        participation_cost=float(m.get("participation_cost", 0.0)),
        participating_policy=str(m.get("participating_policy", "cheapest")),
        demand_coverage_radius=(None if m.get("demand_coverage_radius") is None else float(m["demand_coverage_radius"])),
        demand_range_factor=float(m.get("demand_range_factor", 0.5)),
        demand_provision_serving=bool(m.get("demand_provision_serving", True)),
        demand_serve_margin=float(m.get("demand_serve_margin", 0.95)),
        reflow_rescue_scope=str(m.get("reflow_rescue_scope", "fleet")),
        coverage_gate=bool(m.get("coverage_gate", False)),
        coverage_gate_max_rounds=int(m.get("coverage_gate_max_rounds", 8)),
        coverage_first=bool(m.get("coverage_first", False)),
        coverage_first_max_rounds=int(m.get("coverage_first_max_rounds", 4)),
        monotone_polish_max_rounds=int(m.get("monotone_polish_max_rounds", 30)),
        monotone_polish_eps=float(m.get("monotone_polish_eps", 1.0e-6)),
        reorder_range_optimize=bool(m.get("reorder_range_optimize", False)),
        reorder_range_eps=float(m.get("reorder_range_eps", 1.0)),
        cost_polish_enabled=bool(m.get("cost_polish_enabled", True)),
        cost_polish_max_rounds=int(m.get("cost_polish_max_rounds", 30)),
        cost_polish_eps=float(m.get("cost_polish_eps", 1.0)),
        l3b_enabled=(None if m.get("l3b_enabled") is None else bool(m["l3b_enabled"])),
        cost_polish_relocate_enabled=bool(m.get("cost_polish_relocate_enabled", True)),
        marginal_tour_cap=(None if m.get("marginal_tour_cap") is None else int(m["marginal_tour_cap"])),
        prune_unprofitable=bool(m.get("prune_unprofitable", True)),
        prune_margin=float(m.get("prune_margin", 0.0)),
        prune_max_rounds=int(m.get("prune_max_rounds", 8)),
        prune_zero_reward=bool(m.get("prune_zero_reward", True)),
        prune_wait_bonus=float(m.get("prune_wait_bonus", 1.0)),
        wait_scale=float(m.get("wait_scale", 3600.0)),
        lamarckian=bool(m.get("lamarckian", True)),
        polish_enabled=bool(m.get("polish_enabled", True)),
        polish_moves=tuple(m.get("polish_moves", ["relocate", "swap", "position"])),
        polish_max_rounds=int(m.get("polish_max_rounds", 30)),
        polish_max_evals=int(m.get("polish_max_evals", 2000)),
        polish_eps=float(m.get("polish_eps", 1e-6)),
        polish_in_loop=bool(m.get("polish_in_loop", False)),
        memetic_K=int(m.get("memetic_K", 20)),
        memetic_max_rounds=int(m.get("memetic_max_rounds", 6)),
        memetic_max_evals=int(m.get("memetic_max_evals", 500)),
        decode_mode=str(m.get("decode_mode", "repair_chain")),
    )

    s = _section(cfg, "solver")
    solver = DMDEConfig(
        pop_size=int(s.get("population", 20)),
        max_generations=int(s.get("max_iter", 40)),
        zeta=int(s.get("zeta", 3)),
        delta=(None if s.get("delta") is None else float(s["delta"])),
        seed=s.get("seed", seed),
        verbose=bool(s.get("verbose", False)),
        log_interval=int(s.get("log_interval", 10)),
    )

    rolling = _section(cfg, "rolling")
    e = rolling.get("events") or {}
    # 优先取顶层 events 段（因子覆盖写在那里），回退 rolling.events
    ev_top = _section(cfg, "events")
    event = EventTriggerConfig(
        enabled=bool(ev_top.get("enabled", e.get("enabled", True))),
        on_uav_fault=bool(ev_top.get("on_uav_fault", e.get("on_uav_fault", True))),
        on_nest_unavailable=bool(ev_top.get("on_nest_unavailable", e.get("on_nest_unavailable", True))),
        on_urgent_task=bool(ev_top.get("on_urgent_task", e.get("on_urgent_task", True))),
        reward_threshold=float(ev_top.get("reward_threshold", e.get("reward_threshold", 8.0))),
        slack_threshold=float(ev_top.get("slack_threshold", e.get("slack_threshold", 900.0))),
    )
    sw = rolling.get("stop_when_stable") or {}
    stop_when_stable = bool(sw.get("enabled", False))
    stable_patience = int(sw.get("patience", 4))

    problem = _section(cfg, "problem")

    # faults 注入面（含 "busiest" 支持）
    raw_faults = rolling.get("faults") or {}
    faults: dict[int, list[int | str]] = {}
    for k, v in raw_faults.items():
        faults[int(k)] = [x if isinstance(x, str) else int(x) for x in v]

    # nest_closures 注入面（从 events.schedule 读取）
    sched = ev_top.get("schedule") or {}
    nest_closures: dict[int, list[int]] = {}
    for item in sched.get("nest_closures", []):
        c = int(item["cycle"])
        nest_closures.setdefault(c, []).append(int(item["nest_id"]))

    horizon = HorizonConfig(
        n_cycles=int(rolling.get("n_cycles", 1)),
        cycle_length=float(rolling.get("cycle_length", 600.0)),
        complete_after_cycle=bool(rolling.get("complete_after_cycle", True)),
        recharge_at_nest=bool(rolling.get("recharge_at_nest", True)),
        uav_max_range=float(problem.get("uav_max_range", 120000.0)),
        event_replan=bool(rolling.get("event_replan", True)),
        faults=faults,
        nest_closures=nest_closures,
        strict_capacity=bool(rolling.get("strict_capacity", False)),
        completion_feasibility_gate=bool(rolling.get("completion_feasibility_gate", True)),
        stop_when_stable=stop_when_stable,
        stable_patience=stable_patience,
    )

    selection = RollingHorizon.selection_config_from_dict(cfg.get("selection"))
    if not constraints.time_window:
        selection.feasibility.check_time_window = False
    cost = _section(cfg, "cost")

    return ConfigBundle(
        raw=dict(cfg),
        problem_model=problem_model,
        stage_model=stage_model,
        solver=solver,
        event=event,
        horizon=horizon,
        selection=selection,
        nest_offset=int(cost.get("nest_offset", 1_000_000)),
        factor=factor,
    )
