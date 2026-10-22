# -*- coding: utf-8 -*-
"""exp05_baselines 配置装配：``config.yaml`` → 各模块的配置对象。

与 exp01 同一套 yaml 键（场景 / 选择 / MUAS / 约束 / 求解器 / 滚动），
保证「同一场景、同一指标口径」；exp05 额外读 ``methods:`` 段定义对照臂
（method_id → 求解器 + 代价模型）。

只做「读 yaml + 建配置对象」：不建场景、不跑算法、不写产物。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import paths  # noqa: F401  # sys.path 引导：挂载仓库根，供 task_allocation.* 导入

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from task_allocation.methods.muas.problem import (
    ConstraintConfig,
    MUASProblemConfig,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig
from task_allocation.methods.rolling.event_trigger import EventTriggerConfig
from task_allocation.methods.rolling.horizon import HorizonConfig, RollingHorizon
from task_allocation.methods.selection.pipeline import SelectionConfig

__all__ = ["ConfigBundle", "MethodArm", "load_config", "build_config_bundle"]


def load_config(path: Path) -> dict[str, Any]:
    """读取实验 yaml。"""
    if yaml is None:
        raise RuntimeError("需要 PyYAML：请在 pyproject.toml 中加入 pyyaml 依赖后安装")
    with Path(path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"config.yaml 顶层必须是映射：{path}")
    return data


def _section(raw: Mapping[str, Any], name: str) -> dict[str, Any]:
    v = raw.get(name)
    return dict(v) if isinstance(v, Mapping) else {}


@dataclass(frozen=True)
class MethodArm:
    """一个对照臂（methods: 段的一行）。

    Attributes:
        id:         臂名（结果目录名，如 M0 / D0 / B1 / B2 / B3）。
        solver:     求解器名：dmde（本文方法）或 baselines.REGISTRY 的 key。
        cost_model: dem（三维地形代价，生产口径）| euclidean（同架构欧氏对照）。
        params:     求解器构造参数（透传给 baseline 构造器，如 seed）。
        label:      人读标签（notes / 表格用）。
    """

    id: str
    solver: str
    cost_model: str = "dem"
    params: dict[str, Any] = field(default_factory=dict)
    label: str = ""

    def __post_init__(self) -> None:
        if self.cost_model not in ("dem", "euclidean"):
            raise ValueError(f"{self.id}: cost_model 必须 dem|euclidean，实际 {self.cost_model}")
        if self.solver != "dmde" and not self.label:
            object.__setattr__(self, "label", f"{self.solver} 启发式")


@dataclass(frozen=True)
class ConfigBundle:
    """一次实验运行所需的全部配置（与 exp01 同构）。"""

    raw: dict[str, Any]
    problem_model: MUASProblemConfig
    stage_model: MUASStageConfig
    solver: DMDEConfig
    event: EventTriggerConfig
    horizon: HorizonConfig
    selection: SelectionConfig
    nest_offset: int
    arms: tuple[MethodArm, ...]

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
    def run(self) -> dict[str, Any]:
        return _section(self.raw, "run")

    @property
    def seed(self) -> int:
        return int(self.experiment.get("seed", 42))

    @property
    def verbose(self) -> bool:
        return bool(self.run.get("verbose", True))


def _build_arms(raw: Mapping[str, Any]) -> tuple[MethodArm, ...]:
    rows = raw.get("methods") or []
    if not rows:
        raise ValueError("config.yaml 缺少 methods: 段（至少一个对照臂）")
    arms = []
    seen: set[str] = set()
    for row in rows:
        arm = MethodArm(
            id=str(row["id"]),
            solver=str(row.get("solver", "dmde")).strip().lower(),
            cost_model=str(row.get("cost_model", "dem")).strip().lower(),
            params=dict(row.get("params") or {}),
            label=str(row.get("label", "")),
        )
        if arm.id in seen:
            raise ValueError(f"methods 段臂 id 重复：{arm.id}")
        seen.add(arm.id)
        arms.append(arm)
    return tuple(arms)


def build_config_bundle(cfg: Mapping[str, Any]) -> ConfigBundle:
    """把 yaml 映射装配成一次运行所需的配置对象（键与 exp01 同构）。"""
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
        lambda_cost=float(m.get("lambda_cost", 1.0e-2)),
        w_penalty=float(m.get("w_penalty", 1.0)),
        w_violation=(None if m.get("w_violation") is None else float(m["w_violation"])),
        w_nest_capacity=float(m.get("w_nest_capacity", 1.0)),
        w_hard_violation=(
            None if m.get("w_hard_violation") is None else float(m["w_hard_violation"])
        ),
        w_range_excess=(
            None if m.get("w_range_excess") is None else float(m["w_range_excess"])
        ),
        hard_violation_kappa=float(m.get("hard_violation_kappa", 10.0)),
        w_time=float(m.get("w_time", 0.0)),
        default_service_time=float(m.get("default_service_time", 0.0)),
        late_reward_decay_seconds=(
            None
            if m.get("late_reward_decay_seconds") is None
            else float(m["late_reward_decay_seconds"])
        ),
        constraints=constraints,
    )
    stage_model = MUASStageConfig(
        max_participating_uavs=m.get("max_participating_uavs", None),
        sortie_budget=(None if m.get("sortie_budget") is None else int(m["sortie_budget"])),
        participation_cost=float(m.get("participation_cost", 0.0)),
        participating_policy=str(m.get("participating_policy", "cheapest")),
        demand_coverage_radius=(
            None if m.get("demand_coverage_radius") is None
            else float(m["demand_coverage_radius"])
        ),
        demand_range_factor=float(m.get("demand_range_factor", 0.5)),
        demand_provision_serving=bool(m.get("demand_provision_serving", True)),
        demand_serve_margin=float(m.get("demand_serve_margin", 0.95)),
        reflow_rescue_scope=str(m.get("reflow_rescue_scope", "fleet")),
        marginal_tour_cap=(
            None if m.get("marginal_tour_cap") is None else int(m["marginal_tour_cap"])
        ),
        prune_unprofitable=bool(m.get("prune_unprofitable", True)),
        prune_margin=float(m.get("prune_margin", 0.0)),
        prune_max_rounds=int(m.get("prune_max_rounds", 8)),
        prune_zero_reward=bool(m.get("prune_zero_reward", False)),
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
    event = EventTriggerConfig(
        enabled=bool(e.get("enabled", True)),
        on_uav_fault=bool(e.get("on_uav_fault", True)),
        on_nest_unavailable=bool(e.get("on_nest_unavailable", True)),
        on_urgent_task=bool(e.get("on_urgent_task", True)),
        reward_threshold=float(e.get("reward_threshold", 8.0)),
        slack_threshold=float(e.get("slack_threshold", 900.0)),
    )
    sw = rolling.get("stop_when_stable") or {}
    problem = _section(cfg, "problem")
    horizon = HorizonConfig(
        n_cycles=int(rolling.get("n_cycles", 1)),
        cycle_length=float(rolling.get("cycle_length", 600.0)),
        complete_after_cycle=bool(rolling.get("complete_after_cycle", True)),
        recharge_at_nest=bool(rolling.get("recharge_at_nest", True)),
        uav_max_range=float(problem.get("uav_max_range", 120000.0)),
        event_replan=bool(rolling.get("event_replan", True)),
        faults={
            int(k): [int(x) for x in v] for k, v in (rolling.get("faults") or {}).items()
        },
        strict_capacity=bool(rolling.get("strict_capacity", False)),
        completion_feasibility_gate=bool(rolling.get("completion_feasibility_gate", True)),
        stop_when_stable=bool(sw.get("enabled", False)),
        stable_patience=int(sw.get("patience", 4)),
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
        arms=_build_arms(cfg),
    )
