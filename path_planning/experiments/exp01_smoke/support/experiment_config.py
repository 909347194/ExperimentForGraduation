# -*- coding: utf-8 -*-
"""实验配置：一份配置（YAML/JSON/JSONC）→ 一个不可变配置对象。

职责：读取并校验实验配置（场景 / 建场 / 走廊 / 规划参数 / 飞行带 / 误差口径），
      把配置里的列表还原成 ``PlanParams`` 需要的元组，拒绝任何未知键。
边界：不读机队清单（``fleet_spec.py``）、不建场、不跑规划。
相关：``common/plan_params.py``（参数结构的唯一来源）、
      ``experiments/exp01_smoke/config.yaml``（数值的唯一来源）。

**为什么不设默认值**：这里每个字段都必须从配置文件出现。少写一个键就报错，
而不是静默取一个藏在代码里的默认值——否则论文里那个数字的出处会说不清。
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from path_planning.common.config_io import load_config_mapping
from path_planning.common.plan_params import PlanParams
from path_planning.methods.spatial.corridor_window import CorridorRule

__all__ = [
    "CorridorSettings",
    "ExperimentConfig",
    "FieldSettings",
    "LoopSettings",
    "LoopTaskSettings",
    "MissionWaypointsSettings",
    "ScenarioSettings",
    "load_experiment_config",
]

#: 元组型参数（配置文件里是列表，必须还原成元组）
_TUPLE_FIELDS = frozenset(
    {
        "sdf_spacing_cells",
        "ellipsoid_semiaxes_m",
        "retreat_node_ladder",
        "detour_vertical_steps_m",
        "detour_lateral_steps_m",
    }
)


@dataclass(frozen=True)
class ScenarioSettings:
    """场景与地形装配口径（``common/scenario_loader.py`` 的入参）。"""

    data_dir: Path
    name: str | None
    nest_count: int | None
    terrain_step_m: float
    bbox_margin_m: float
    #: 地形空洞的水面高程（米，绝对 ENU z）；None = 最近邻扩散。
    #: 广州 DEM 的 nodata=255 是北缘珠江，按水面填，否则水被糊成岸边高程。
    water_level_m: float | None = None


@dataclass(frozen=True)
class FieldSettings:
    """全域建场口径（``methods/spatial/world_field_builder.py`` 的入参）。"""

    spacing_m: tuple[float, float, float]
    alt_max_m: float
    pad_m: float
    include_static_nofly: bool
    sample_bias_m: float
    cache_dir: Path | None


@dataclass(frozen=True)
class CorridorSettings:
    """走廊规则（半宽优先于宽比；``half_width_min_m`` 是比例口径的绝对下限）。"""

    half_width_m: float | None
    width_ratio: float | None
    half_width_min_m: float
    margin_m: float

    def to_rule(self) -> CorridorRule:
        return CorridorRule(
            half_width_m=self.half_width_m,
            width_ratio=self.width_ratio,
            half_width_min_m=float(self.half_width_min_m),
            margin_m=float(self.margin_m),
        )


@dataclass(frozen=True)
class MissionWaypointsSettings:
    """航点高度解析规则的数值（规则本身在本实验 ``support/waypoint_policy.py``）。"""

    nest_hover_agl_m: float
    task_clearance_agl_m: float

    def __post_init__(self) -> None:
        for name in ("nest_hover_agl_m", "task_clearance_agl_m"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} 必须为正有限值：{value}")


@dataclass(frozen=True)
class LoopTaskSettings:
    """一个按周期投放的任务点（多周期实验用）。"""

    id: int
    position: tuple[float, float, float]
    service_time_s: float
    release_cycle: int  # 1 起；第几周期投放


@dataclass(frozen=True)
class LoopSettings:
    """多周期实验的环参数（只有多周期实验写这一段）。"""

    cycle_length_s: float
    n_cycles: int
    overrun_tol_s: float
    tasks: tuple[LoopTaskSettings, ...]


@dataclass(frozen=True)
class ExperimentConfig:
    """一次实验运行的全部输入（不含机队清单）。"""

    name: str
    seeds: tuple[int, ...]
    scenario: ScenarioSettings
    field: FieldSettings
    corridor: CorridorSettings
    params: PlanParams
    #: 端点净空口径（本实验端点由自选航点构成，无豁免：``endpoint_clearance_m``
    #: 与全域同判、``endpoint_exempt_radius_m`` 恒为 0）
    endpoint_clearance_m: float | None
    endpoint_exempt_radius_m: float
    #: 检测半径的几何误差标量（DEM 像元 / 棱柱化 / 高程舍入合成）
    e_geo_m: float
    #: 决策周期（秒）：周期 r 的 ``t0 = (r-1) × cycle_length_s``
    cycle_length_s: float
    #: 同周期**同起点**航次的离巢错峰（秒）：第 k 台 ``t0 = 周期基准 + k × 本值``。
    #: 0 = 不错峰（同刻同起点必被判 0 距离，见 ``docs/architecture.md`` §4）
    depart_stagger_s: float
    results_dir: Path
    #: 航点高度解析配置（巢址悬停 / 任务点净空）
    mission_waypoints: MissionWaypointsSettings
    #: 多周期实验的环参数；单周期实验为 None
    loop: LoopSettings | None = None

    def field_key(self, scenario_meta: Mapping[str, Any]) -> str:
        """场的身份串：参数变了一位就换缓存文件，不会命中别的口径。"""
        sp = self.field
        parts = [
            self.scenario.data_dir.name,
            str(scenario_meta.get("data_bounds_xy")),
            str(scenario_meta.get("building_count")),
            "x".join(f"{v:g}" for v in sp.spacing_m),
            f"alt{sp.alt_max_m:g}",
            f"pad{sp.pad_m:g}",
            f"nofly{int(sp.include_static_nofly)}",
            f"bias{sp.sample_bias_m:g}",
        ]
        return "_".join(parts).replace(" ", "").replace("(", "").replace(")", "")


def load_experiment_config(path: str | Path) -> ExperimentConfig:
    """读配置（YAML/JSON/JSONC）并校验；未知键、缺键、非法值一律报错。"""
    p = Path(path)
    raw = load_config_mapping(p)

    _reject_unknown(raw, {"experiment", "scenario", "field", "corridor", "planning", "runtime",
                          "mission_waypoints", "loop"}, p)

    exp = _section(raw, "experiment", p)
    scen = _section(raw, "scenario", p)
    fld = _section(raw, "field", p)
    cor = _section(raw, "corridor", p)
    plan = _section(raw, "planning", p)
    run = _section(raw, "runtime", p)
    way = _section(raw, "mission_waypoints", p)

    _reject_unknown(exp, {"name", "seeds"}, p)
    _reject_unknown(
        scen,
        {"data_dir", "name", "nest_count", "terrain_step_m", "bbox_margin_m",
         "water_level_m"},
        p,
    )
    _reject_unknown(
        fld,
        {"spacing_m", "alt_max_m", "pad_m", "include_static_nofly",
         "sample_bias_m", "cache_dir"},
        p,
    )
    _reject_unknown(cor, {"half_width_m", "width_ratio", "half_width_min_m", "margin_m"}, p)
    _reject_unknown(run, {"endpoint_clearance_m", "endpoint_exempt_radius_m",
                          "e_geo_m", "cycle_length_s", "depart_stagger_s", "results_dir"}, p)
    _reject_unknown(way, {"nest_hover_agl_m", "task_clearance_agl_m"}, p)

    params = _build_params(plan, p)
    params.validate()
    # 两条间隙是"离表面多高"，不是真高：巢址在地面上、作业面就是地面时二者相等。
    # 间隙小于真高下限会让每个端点都必被 plan_tour 拒，所以在读配置时就拦下。
    for name in ("nest_hover_agl_m", "task_clearance_agl_m"):
        gap = float(way[name])
        if gap < float(params.alt_agl_min_m) - 1e-9:
            raise ValueError(
                f"mission_waypoints.{name}={gap:g} m 小于真高带下限 "
                f"{params.alt_agl_min_m:g} m：地表点会被端点准入直接拒"
            )
    if float(run["cycle_length_s"]) <= 0.0:
        raise ValueError(f"cycle_length_s 必须为正：{run['cycle_length_s']}")
    if float(run["depart_stagger_s"]) < 0.0:
        raise ValueError(f"depart_stagger_s 不能为负：{run['depart_stagger_s']}")

    return ExperimentConfig(
        name=str(exp["name"]),
        seeds=tuple(int(s) for s in exp["seeds"]),
        scenario=ScenarioSettings(
            data_dir=_resolve(scen["data_dir"]),
            name=None if scen.get("name") is None else str(scen["name"]),
            nest_count=None if scen.get("nest_count") is None else int(scen["nest_count"]),
            terrain_step_m=float(scen["terrain_step_m"]),
            bbox_margin_m=float(scen["bbox_margin_m"]),
            water_level_m=(
                None if scen.get("water_level_m") is None
                else float(scen["water_level_m"])
            ),
        ),
        field=FieldSettings(
            spacing_m=tuple(float(v) for v in fld["spacing_m"]),
            alt_max_m=float(fld["alt_max_m"]),
            pad_m=float(fld["pad_m"]),
            include_static_nofly=bool(fld["include_static_nofly"]),
            sample_bias_m=float(fld["sample_bias_m"]),
            cache_dir=None if fld.get("cache_dir") is None else _resolve(fld["cache_dir"]),
        ),
        corridor=CorridorSettings(
            half_width_m=None if cor.get("half_width_m") is None else float(cor["half_width_m"]),
            width_ratio=None if cor.get("width_ratio") is None else float(cor["width_ratio"]),
            half_width_min_m=float(cor["half_width_min_m"]),
            margin_m=float(cor["margin_m"]),
        ),
        params=params,
        endpoint_clearance_m=(
            None if run.get("endpoint_clearance_m") is None
            else float(run["endpoint_clearance_m"])
        ),
        endpoint_exempt_radius_m=float(run["endpoint_exempt_radius_m"]),
        e_geo_m=float(run["e_geo_m"]),
        cycle_length_s=float(run["cycle_length_s"]),
        depart_stagger_s=float(run["depart_stagger_s"]),
        results_dir=_resolve(run["results_dir"]),
        mission_waypoints=MissionWaypointsSettings(
            nest_hover_agl_m=float(way["nest_hover_agl_m"]),
            task_clearance_agl_m=float(way["task_clearance_agl_m"]),
        ),
        loop=_build_loop(raw.get("loop"), p),
    )


def _build_loop(section: Any, path: Path) -> LoopSettings | None:
    """``loop`` 段（可选）：没有就 None，有就逐字段校验。"""
    if section is None:
        return None
    if not isinstance(section, Mapping):
        raise ValueError(f"loop 段必须是映射：{path}")
    _reject_unknown(section, {"cycle_length_s", "n_cycles", "overrun_tol_s", "tasks"}, path)
    missing = sorted({"cycle_length_s", "n_cycles", "overrun_tol_s", "tasks"} - set(section))
    if missing:
        raise ValueError(f"loop 段缺少键 {missing}（{path}）")
    tasks_raw = section["tasks"]
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ValueError(f"loop.tasks 必须是非空列表：{path}")
    tasks: list[LoopTaskSettings] = []
    for item in tasks_raw:
        if not isinstance(item, Mapping):
            raise ValueError(f"loop.tasks 元素必须是对象：{item!r}")
        _reject_unknown(item, {"id", "enu", "service_time_s", "release_cycle"}, path)
        pos = tuple(float(v) for v in item["enu"])
        if len(pos) != 3:
            raise ValueError(f"loop.tasks 的 enu 必须是三维：{item!r}")
        release = int(item["release_cycle"])
        if release < 1:
            raise ValueError(f"loop.tasks 的 release_cycle 从 1 起：{item!r}")
        tasks.append(LoopTaskSettings(
            id=int(item["id"]),
            position=pos,
            service_time_s=float(item["service_time_s"]),
            release_cycle=release,
        ))
    cycle_length_s = float(section["cycle_length_s"])
    n_cycles = int(section["n_cycles"])
    if cycle_length_s <= 0.0 or n_cycles <= 0:
        raise ValueError(f"loop 的周期长度/周期数必须为正：{section}")
    return LoopSettings(
        cycle_length_s=cycle_length_s,
        n_cycles=n_cycles,
        overrun_tol_s=float(section["overrun_tol_s"]),
        tasks=tuple(tasks),
    )


#: 项目根（``path_planning/experiments/exp01_smoke/support/experiment_config.py`` 往上五层）
PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _resolve(value: str | Path) -> Path:
    """相对路径按**项目根**解析，与从哪个目录启动无关。"""
    p = Path(value)
    return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


# ---------------------------------------------------------------------------
# 解析工具
# ---------------------------------------------------------------------------


def _section(raw: Mapping[str, Any], key: str, path: Path) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"配置缺少 ``{key}`` 段（或不是映射）：{path}")
    return value


def _reject_unknown(section: Mapping[str, Any], allowed: set[str], path: Path) -> None:
    extra = sorted(set(section) - allowed)
    if extra:
        raise ValueError(f"配置出现未知键 {extra}（{path}）")


def _build_params(section: Mapping[str, Any], path: Path) -> PlanParams:
    """``planning`` 段 → ``PlanParams``；字段名必须与 dataclass 完全一致。"""
    fields = {f.name for f in dataclasses.fields(PlanParams)}
    unknown = sorted(set(section) - fields)
    if unknown:
        raise ValueError(f"planning 段出现未知参数 {unknown}（{path}）")
    missing = sorted(fields - set(section))
    if missing:
        raise ValueError(f"planning 段缺少参数 {missing}（一实验一配置，不设默认值）")
    kwargs: dict[str, Any] = {}
    for name, value in section.items():
        if name in _TUPLE_FIELDS:
            kwargs[name] = tuple(value)
        else:
            kwargs[name] = value
    return PlanParams(**kwargs)
