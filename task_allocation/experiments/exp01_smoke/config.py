# -*- coding: utf-8 -*-
"""exp01_smoke 配置装配：``config.yaml`` → 各模块的配置对象。

只做「读 yaml + 建配置对象」：不建场景、不跑算法、不写产物。
``run.py`` 从这里拿到 :class:`ConfigBundle`，再交给 ``build.py`` / ``methods``。

职责边界：
    * 本文件负责**类型转换与缺省值**（缺省仅作 yaml 漏填时的兜底）；
    * 实验参数一律以 ``config.yaml`` 为准，本文件不写死任何设定值；
    * 算法超参对象（``*Config``）在这里一次性装配，下游模块不再各自解析 yaml。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import paths  # noqa: F401  # sys.path 引导：挂载仓库根，供 task_allocation.* 导入

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from task_allocation.methods.muas.problem import MUASProblemConfig
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig
from task_allocation.methods.rolling.event_trigger import EventTriggerConfig
from task_allocation.methods.rolling.horizon import HorizonConfig, RollingHorizon
from task_allocation.methods.selection.pipeline import SelectionConfig

__all__ = ["ConfigBundle", "load_config", "build_config_bundle"]


# ---------------------------------------------------------------------------
# yaml 读取
# ---------------------------------------------------------------------------


def load_config(path: Path) -> dict[str, Any]:
    """读取实验 yaml；PyYAML 缺失时给出可操作的报错而不是 ImportError。"""
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


# ---------------------------------------------------------------------------
# 装配结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigBundle:
    """一次实验运行所需的全部配置。

    Attributes:
        raw:           yaml 原文，供需要透传的段（``problem`` / ``tasks`` 等）取用。
        problem_model: 第二阶段 MUAS 的模型设定（能否不分配、λ、惩罚权重…）。
        stage_model:   选择性 MUAS 的编排设定（参与 UAV 上限、剔除、Lamarckian…）。
        solver:        DMDE 求解器超参。
        event:         事件触发阈值。
        horizon:       滚动时域参数（周期数、周期长度、故障注入…）。
        selection:     第一阶段动态任务选择配置。
        nest_offset:   机巢在代价键空间中的偏移量。
    """

    raw: dict[str, Any]
    problem_model: MUASProblemConfig
    stage_model: MUASStageConfig
    solver: DMDEConfig
    event: EventTriggerConfig
    horizon: HorizonConfig
    selection: SelectionConfig
    nest_offset: int

    # ── yaml 各节（原样透传给 build.py / metrics.py）───────────────
    @property
    def experiment(self) -> dict[str, Any]:
        return _section(self.raw, "experiment")

    @property
    def data(self) -> dict[str, Any]:
        return _section(self.raw, "data")

    @property
    def problem(self) -> dict[str, Any]:
        """问题规模（机巢数、容量、机队规模、航程、速度）。"""
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
    def plot(self) -> dict[str, Any]:
        return _section(self.raw, "plot")

    # ── 常用小项 ─────────────────────────────────────────────────
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


def build_config_bundle(cfg: Mapping[str, Any]) -> ConfigBundle:
    """把 yaml 映射装配成一次运行所需的配置对象。"""
    exp = _section(cfg, "experiment")
    seed = exp.get("seed", 42)

    m = _section(cfg, "muas")
    problem_model = MUASProblemConfig(
        allow_unassigned=bool(m.get("allow_unassigned", True)),
        allow_idle_uav=bool(m.get("allow_idle_uav", True)),
        lambda_cost=float(m.get("lambda_cost", 1.0e-4)),
        w_penalty=float(m.get("w_penalty", 1.0)),
        w_nest_capacity=float(m.get("w_nest_capacity", 1.0)),
        default_service_time=float(m.get("default_service_time", 0.0)),
    )
    stage_model = MUASStageConfig(
        max_participating_uavs=m.get("max_participating_uavs", None),
        participating_policy=str(m.get("participating_policy", "cheapest")),
        prune_unprofitable=bool(m.get("prune_unprofitable", True)),
        prune_margin=float(m.get("prune_margin", 0.0)),
        prune_max_rounds=int(m.get("prune_max_rounds", 8)),
        prune_wait_bonus=float(m.get("prune_wait_bonus", 1.0)),
        wait_scale=float(m.get("wait_scale", 3600.0)),
        end_nest_in_encoding=bool(m.get("end_nest_in_encoding", True)),
        lamarckian=bool(m.get("lamarckian", True)),
    )

    s = _section(cfg, "solver")
    solver = DMDEConfig(
        pop_size=int(s.get("population", 20)),
        max_generations=int(s.get("max_iter", 40)),
        zeta=int(s.get("zeta", 3)),
        delta=float(s.get("delta", 0.3)),
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

    problem = _section(cfg, "problem")
    horizon = HorizonConfig(
        n_cycles=int(rolling.get("n_cycles", 1)),
        cycle_length=float(rolling.get("cycle_length", 600.0)),
        complete_after_cycle=bool(rolling.get("complete_after_cycle", True)),
        recharge_at_nest=bool(rolling.get("recharge_at_nest", True)),
        uav_max_range=float(problem.get("uav_max_range", 120000.0)),
        event_replan=bool(rolling.get("event_replan", True)),
        faults={
            int(k): [int(x) for x in v]
            for k, v in (rolling.get("faults") or {}).items()
        },
        strict_capacity=bool(rolling.get("strict_capacity", False)),
    )

    selection = RollingHorizon.selection_config_from_dict(cfg.get("selection"))
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
    )
