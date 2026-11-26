# -*- coding: utf-8 -*-
"""exp01_smoke 配置读取与校验：所有实验数值的唯一入口。

职责：读 ``config.yaml``、校验必需段与关键数值、解析相对路径、定结果目录。
边界：不装配对象、不读场景数据、不 import 两侧算法。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

EXP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = EXP_DIR.parents[2]

_REQUIRED = (
    "experiment", "data", "field", "mission_waypoints", "loop", "feedback",
    "resolve", "path", "upper", "selection", "solver", "tasks", "audit",
)


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """读 YAML 并校验；缺段、缺键、非法数值一律报错，不静默补默认。"""
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("需要 pyyaml") from exc

    p = Path(path) if path is not None else EXP_DIR / "config.yaml"
    if not p.is_file():
        raise FileNotFoundError(f"配置文件不存在：{p}")
    with p.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    if not isinstance(cfg, Mapping):
        raise ValueError(f"config 顶层必须是映射：{p}")
    return validate(cfg)


def validate(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """校验必需段与关键数值；缺键、非法值一律报错，不静默补默认。

    ``load_config`` 已调用一次；命令行改过 ``loop.max_cycles`` 之后必须再调用一次。
    """
    missing = [k for k in _REQUIRED if k not in cfg]
    if missing:
        raise ValueError(f"config 缺段 {missing}")

    if int(cfg["experiment"]["cycles"]) < 1:
        raise ValueError("experiment.cycles 必须 >= 1")
    if int(cfg["tasks"]["per_cycle"]) < 1:
        raise ValueError("tasks.per_cycle 必须 >= 1")
    if len(cfg["data"]["active_nest_ids"]) < 1:
        raise ValueError("data.active_nest_ids 不能为空")

    agl_lo, agl_hi = (float(v) for v in cfg["mission_waypoints"]["agl_range_m"])
    if not (0.0 < agl_lo < agl_hi):
        raise ValueError(f"agl_range_m 非法：{(agl_lo, agl_hi)}")
    hover = float(cfg["mission_waypoints"]["nest_hover_agl_m"])
    task_clear = float(cfg["mission_waypoints"]["task_clearance_agl_m"])
    if not (agl_lo <= hover <= agl_hi) or not (agl_lo <= task_clear <= agl_hi):
        raise ValueError(
            f"巢/任务净空 {hover}/{task_clear} m 必须落在 AGL 带 [{agl_lo}, {agl_hi}] 内"
        )
    if float(cfg["loop"]["cycle_length_s"]) <= 0.0:
        raise ValueError("loop.cycle_length_s 必须为正")

    # 周期内事实回灌只有一档：写实测代价 + 不可飞边，至多重解一次。没有 off 对照档，
    # 也不许靠"重解次数"调出别的玩法。
    rs = dict(cfg.get("resolve") or {})
    if int(rs.get("max_in_cycle", 0)) != 1:
        raise ValueError(
            f"resolve.max_in_cycle 只能为 1（同周期至多重解一次）：{rs.get('max_in_cycle')}"
        )

    # 上层求解预算必需显式给出：漏写会落进 DMDE 默认 1000 代（单周期半小时以上）
    sv = dict(cfg["solver"])
    if int(sv.get("pop_size", 0)) < 1 or int(sv.get("max_generations", 0)) < 1:
        raise ValueError(f"solver.pop_size / max_generations 必须为正：{sv}")

    # 上层口径段：selection 走 task_allocation 的三 mode，防止拼写错误静默退回默认
    # （"默认值不是本实验口径"这条，早先在 selection 回退到 intersect 名额时吃过一次亏）。
    sel = dict(cfg.get("selection") or {})
    cap = dict(sel.get("capability") or {})
    mode = str(cap.get("mode", "intersect"))
    if mode not in ("intersect", "primary", "single"):
        raise ValueError(f"selection.capability.mode 非法：{mode!r}")
    if cap.get("n_compute") is not None and int(cap["n_compute"]) < 1:
        raise ValueError("selection.capability.n_compute 必须为正整数")
    return dict(cfg)


def resolve(path: str | Path) -> Path:
    """相对路径按仓库根解析（场景数据路径都写在仓库根坐标系里）。"""
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def default_run_dir(cfg: Mapping[str, Any]) -> Path:
    """正式结果目录：``results/seed_<seed>``（只有一档，不按档位分目录）。

    运行、出图、重审计、失败诊断四处都从这里取默认值：一处改，四处同时生效。
    """
    return EXP_DIR / "results" / f"seed_{int(cfg['experiment']['seed'])}"
