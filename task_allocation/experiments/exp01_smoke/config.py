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

from task_allocation.methods.muas.problem import (
    ConstraintConfig,
    MUASProblemConfig,
)
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
    # ── 约束开关（constraints: 段）───────────────────────────────
    # 缺省 = 全部启用、权重 1；逐项在 yaml 里开关 / 调权即可做消融与口径取舍。
    cons_raw = _section(cfg, "constraints")
    constraints = ConstraintConfig.from_dict(cons_raw)
    # 历史字段兼容：未显式给出 constraints.w_nest_capacity 时取 muas.w_nest_capacity
    if "w_nest_capacity" not in cons_raw:
        constraints.w_nest_capacity = float(m.get("w_nest_capacity", 1.0))
    problem_model = MUASProblemConfig(
        allow_unassigned=bool(m.get("allow_unassigned", True)),
        allow_idle_uav=bool(m.get("allow_idle_uav", True)),
        # 双目标口径（2026-10-23 修复透传）：此前 yaml 的 muas.lexicographic /
        # muas.big_m 在本装配里被漏掉——算法恒按 dataclass 默认（lexicographic
        # =True、big_m=None）跑，而 plot/setup/setup_params.py 直接从 yaml 取值
        # 标注「字典序 / 加权和」。即 yaml 改了算法不动、图却跟着变 ⇒ 图会说谎。
        # 现显式透传：yaml 与算法同源，消融改 false 才真正切到 legacy 加权和。
        lexicographic=bool(m.get("lexicographic", True)),
        big_m=(None if m.get("big_m") is None else float(m["big_m"])),
        lambda_cost=float(m.get("lambda_cost", 1.0e-2)),
        w_penalty=float(m.get("w_penalty", 1.0)),
        # 惩罚量纲解耦开关：留空/None = 历史口径（惩罚随 F2 一起乘 λ）
        w_violation=(None if m.get("w_violation") is None else float(m["w_violation"])),
        w_nest_capacity=float(m.get("w_nest_capacity", 1.0)),
        # 2026-10-18 四层准字典序 T0 层：硬违约（物理）支配权重与超程 tie-break。
        #   w_hard_violation: None = 自动 κ·m·R_pool（κ=hard_violation_kappa）；
        #     显式值 = 固定权重（0 = 关闭 T0 层，退回旧混合 penalty 口径）。
        #   w_range_excess:   None = 自动 1/m（1km 超程≈1000 单位）。
        w_hard_violation=(
            None if m.get("w_hard_violation") is None
            else float(m["w_hard_violation"])
        ),
        w_range_excess=(
            None if m.get("w_range_excess") is None
            else float(m["w_range_excess"])
        ),
        hard_violation_kappa=float(m.get("hard_violation_kappa", 10.0)),
        w_time=float(m.get("w_time", 0.0)),
        default_service_time=float(m.get("default_service_time", 0.0)),
        # 迟到奖励折减容忍 W（秒）：晚到 eff=R×max(0,1−L/W)；
        # None = 关闭（历史口径）。与 selection.feasibility.late_tolerance_seconds 同值。
        late_reward_decay_seconds=(
            None
            if m.get("late_reward_decay_seconds") is None
            else float(m["late_reward_decay_seconds"])
        ),
        # 覆盖门禁两层适应度（2026-10-22）：True = W_skip 层 + F1 纯完成
        # 计数 + 拆 1e6 虚构帽；默认 False = 现行四层口径逐位不变。
        lexicographic_coverage=bool(m.get("lexicographic_coverage", False)),
        w_skip_violation=(
            None if m.get("w_skip_violation") is None
            else float(m["w_skip_violation"])
        ),
        constraints=constraints,
    )
    stage_model = MUASStageConfig(
        max_participating_uavs=m.get("max_participating_uavs", None),
        sortie_budget=(None if m.get("sortie_budget") is None else int(m["sortie_budget"])),
        participation_cost=float(m.get("participation_cost", 0.0)),
        participating_policy=str(m.get("participating_policy", "cheapest")),
        # Fix④ 需求感知参与：demand 策略的覆盖半径（米）与航程因子
        demand_coverage_radius=(
            None if m.get("demand_coverage_radius") is None
            else float(m["demand_coverage_radius"])
        ),
        demand_range_factor=float(m.get("demand_range_factor", 0.5)),
        # Fix⑤ 服务能力感知 provisioning：覆盖后按航程预算补机，救远任务
        demand_provision_serving=bool(m.get("demand_provision_serving", True)),
        demand_serve_margin=float(m.get("demand_serve_margin", 0.95)),
        reflow_rescue_scope=str(m.get("reflow_rescue_scope", "fleet")),
        # 覆盖门禁（2026-10-22）：True = 终态修到不动点（能救必救）；
        # False = 不修复（lexicographic_coverage 时只做纯审计分账）。
        coverage_gate=bool(m.get("coverage_gate", False)),
        coverage_gate_max_rounds=int(m.get("coverage_gate_max_rounds", 8)),
        # 覆盖优先两段式解码（2026-10-23 用户裁定）：True = 先装到约定
        # 口径下的最大可行集（EDD 不动点），再在不减少已装集合的约束下
        # 做成本 polish（跨机 relocate / 调序）。False = 现行修复链口径。
        coverage_first=bool(m.get("coverage_first", False)),
        coverage_first_max_rounds=int(m.get("coverage_first_max_rounds", 4)),
        monotone_polish_max_rounds=int(m.get("monotone_polish_max_rounds", 30)),
        monotone_polish_eps=float(m.get("monotone_polish_eps", 1.0e-6)),
        # 重排期航程优化（2026-10-24 修）：零迟到时仍评估「最近邻」候选，
        # 仅在「仍零迟到 且 航程严格更短」时采纳 ⇒ 执行集不变（F1 不动）、
        # 只压航程。语义 = 覆盖优先、成本随后，不触碰主适应度阶梯。
        # False = 修复前行为（早退把航程一起退掉）。
        # ⚠️ 默认 False：2026-10-24 A/B 实测正式配置下净持平且总航程 +4%
        # （多出尾巴周期），仅搜索不充分的冒烟档有收益。详见 muas_stage 注释。
        reorder_range_optimize=bool(m.get("reorder_range_optimize", False)),
        reorder_range_eps=float(m.get("reorder_range_eps", 1.0)),
        # 交付后成本 polish（2026-10-24 用户裁定）：DMDE 收敛后对**交付解**
        # 做组内 2-opt/Or-opt 纯成本重排。四道门：航程 / 迟到不增（数+量
        # 逐分量）/ 执行集由构造不变 / 成本严格降。不触碰主适应度阶梯，
        # 可与覆盖门禁 / coverage_first 叠加。默认开；消融显式 False。
        cost_polish_enabled=bool(m.get("cost_polish_enabled", True)),
        cost_polish_max_rounds=int(m.get("cost_polish_max_rounds", 30)),
        cost_polish_eps=float(m.get("cost_polish_eps", 1.0)),
        # 跨机指派成本 polish（2026-10-24 用户裁定转向）：组内重排之后补
        # 归属维度——单任务跨机改派（含拉空闲机），四道门同组内口径。
        # 3 seed A/B 钉死组内维度已穷尽（63 周期解 3 次接受、0.014%）
        # 之后转向此维度；默认开，消融显式 False。
        cost_polish_relocate_enabled=bool(
            m.get("cost_polish_relocate_enabled", True)
        ),
        marginal_tour_cap=(
            None if m.get("marginal_tour_cap") is None
            else int(m["marginal_tour_cap"])
        ),
        prune_unprofitable=bool(m.get("prune_unprofitable", True)),
        prune_margin=float(m.get("prune_margin", 0.0)),
        prune_max_rounds=int(m.get("prune_max_rounds", 8)),
        # 2026-10-18 零奖励剔除：ETA ≥ latest+W 的任务暂缓（eff≈0 不降 F1）
        prune_zero_reward=bool(m.get("prune_zero_reward", True)),
        prune_wait_bonus=float(m.get("prune_wait_bonus", 1.0)),
        wait_scale=float(m.get("wait_scale", 3600.0)),
        lamarckian=bool(m.get("lamarckian", True)),
        # 2026-10-19 解码后局部搜索 polish（relocate/swap/插入位，
        # 过同一修复链、仅严格改进才接受）；默认开，消融显式关
        polish_enabled=bool(m.get("polish_enabled", True)),
        polish_moves=tuple(
            m.get("polish_moves", ["relocate", "swap", "position"])
        ),
        polish_max_rounds=int(m.get("polish_max_rounds", 30)),
        polish_max_evals=int(m.get("polish_max_evals", 2000)),
        polish_eps=float(m.get("polish_eps", 1e-6)),
        # 2026-10-19 memetic M1 精英档案：搜索期内每 memetic_K 代对精英
        # 跑一次 polish，路由改进前移进搜索期。默认关（基线不动）；
        # exp01 memetic 变体显式开。
        polish_in_loop=bool(m.get("polish_in_loop", False)),
        memetic_K=int(m.get("memetic_K", 20)),
        memetic_max_rounds=int(m.get("memetic_max_rounds", 6)),
        memetic_max_evals=int(m.get("memetic_max_evals", 500)),
        # exp04 B1 解码模式对照臂：默认 repair_chain（生产口径，bit-exact）；
        # "constructive" = 构造式解码（救援前置为主解码），仅消融显式开启
        decode_mode=str(m.get("decode_mode", "repair_chain")),
    )

    s = _section(cfg, "solver")
    solver = DMDEConfig(
        pop_size=int(s.get("population", 20)),
        max_generations=int(s.get("max_iter", 40)),
        zeta=int(s.get("zeta", 3)),
        # None/留空 = 论文公式 3-12 原式 GMR = 1 - CR；给数值则附加 δ 门限
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
    # 收敛早停配置（论文长程吞吐口径）
    sw = rolling.get("stop_when_stable") or {}
    stop_when_stable = bool(sw.get("enabled", False))
    stable_patience = int(sw.get("patience", 4))

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
        completion_feasibility_gate=bool(
            rolling.get("completion_feasibility_gate", True)
        ),
        stop_when_stable=stop_when_stable,
        stable_patience=stable_patience,
    )

    selection = RollingHorizon.selection_config_from_dict(cfg.get("selection"))
    # 约束开关贯通两阶段：关闭交付时间窗时，第一阶段的可行性预筛同步关闭，
    # 避免「第二阶段不计违约、第一阶段却把它筛掉」的口径不一致。
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
    )
