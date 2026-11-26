# -*- coding: utf-8 -*-
"""上层装配：机巢作业高度、RollingHorizon、以及实验侧离巢错峰。

职责：机巢 z 由 CSV 的 hover_agl_m 与 DEM 地面高程合成（不建模起降）；
      错峰只改 TourOrder 的冻结出发时刻，不改上层派活与下层几何。
边界：不碰上层算法，不碰下层规划器。
"""

from __future__ import annotations

import csv
from dataclasses import replace
from typing import Any, Mapping, Sequence

from orchestration.protocol import CycleSnapshot, TourOrder
from task_allocation.common.types import Nest
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
from task_allocation.methods.rolling.horizon import HorizonConfig, RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet

from .config import resolve


def nest_hover_heights(cfg: dict[str, Any]) -> dict[int, float]:
    """读机巢 CSV 的 hover_agl_m，按 ``base_id`` 归到机巢号（``base1`` → 0）。

    不再依赖 CSV 行序与场景加载顺序一致：行换序不会把高度错配到别的巢。
    """
    path = resolve(cfg["data"]["scenario_dir"]) / str(cfg["data"]["nest_csv"])
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out: dict[int, float] = {}
    for r in rows:
        bid = str(r["base_id"]).strip()
        if not bid.startswith("base") or not bid[4:].isdigit():
            raise ValueError(f"机巢 CSV 的 base_id 无法解析：{bid!r}（{path}）")
        out[int(bid[4:]) - 1] = float(r["hover_agl_m"])
    if len(out) < int(cfg["data"]["nest_count"]):
        raise ValueError(f"CSV 机巢高度行数不足：{path}")
    return out


def build_nests(cfg: dict[str, Any], scenario) -> tuple[list[Nest], dict[int, Nest]]:
    """所有巢都有作业高度；只有 active_nest_ids 进入上层求解。"""
    hovers = nest_hover_heights(cfg)
    active = set(int(v) for v in cfg["data"]["active_nest_ids"])
    all_nests: dict[int, Nest] = {}
    for site in scenario.nests:
        if int(site.id) not in hovers:
            raise ValueError(f"CSV 缺机巢 {int(site.id)} 的 hover_agl_m")
        hover = hovers[int(site.id)]
        all_nests[int(site.id)] = Nest(
            id=int(site.id),
            x=float(site.x), y=float(site.y),
            z=float(site.z) + hover,   # 作业点 = 地面 + 15 m；不生成起降段
            capacity=10,
            meta={
                "base_id": f"base{int(site.id) + 1}",
                "ground_z": float(site.z),
                "hover_agl_m": float(hover),
                "active": int(site.id) in active,
            },
        )
    missing = active - set(all_nests)
    if missing:
        raise ValueError(f"active_nest_ids 含未知机巢：{sorted(missing)}")
    selected = [all_nests[i] for i in sorted(active)]
    return selected, all_nests


def solver_config(cfg: dict[str, Any]) -> DMDEConfig:
    """把 ``solver`` 段映射成 ``DMDEConfig``；**随机种子由实验 seed 注入**。

    不接线会落进库默认（``pop_size=50 / max_generations=1000 / seed=None``）：
    前者把单周期拖到半小时以上，后者让 ``results/seed_<N>`` 名不副实——
    同一 seed 两次跑不到同一批航次。故本段是必需段，且只接受下表列出的键。
    """
    allowed = ("pop_size", "max_generations", "zeta", "delta", "alpha", "beta")
    raw = dict(cfg["solver"])
    missing = [k for k in allowed if k not in raw]
    extra = sorted(set(raw) - set(allowed))
    if missing or extra:
        raise ValueError(f"solver 段键不匹配：缺 {missing}，多 {extra}")
    values: dict[str, Any] = {
        "pop_size": int(raw["pop_size"]),
        "max_generations": int(raw["max_generations"]),
        "zeta": int(raw["zeta"]),
        "delta": None if raw["delta"] is None else float(raw["delta"]),
        "alpha": float(raw["alpha"]),
        "beta": float(raw["beta"]),
        "seed": int(cfg["experiment"]["seed"]),
    }
    return DMDEConfig(**values)


def build_horizon(
    cfg: dict[str, Any], nests: Sequence[Nest], uavs: Sequence[Any], estimator: Any
) -> RollingHorizon:
    """装配滚动时域；``selection`` 与 ``solver`` 两段**显式**传给上层。

    阶段一的口径已是 ``mode="single"``（``n_sel = min{cap(t), n_compute}``，
    机数名额 ⌈max_ratio·K⌉ 不参与），但 ``CapabilityScaleConfig`` 的 dataclass
    默认仍是旧的 ``intersect``。本实验不传就退回旧名额把窗口截成
    ⌈2.0·4⌉=8（曾把 30 个任务只完成 24 个），故必须在本文件里显式接线。
    求解器同理：不传 ``solver_config`` 就落默认 1000 代、随机种子。
    """
    loop = cfg["loop"]
    hcfg = HorizonConfig(
        cycle_length=float(loop["cycle_length_s"]),
        complete_after_cycle=True,
        recharge_at_nest=True,
        uav_max_range=max(float(u.max_range or 0.0) for u in uavs),
    )
    return RollingHorizon(
        TaskPool(), UAVFleet(list(uavs)), list(nests),
        horizon_config=hcfg, estimator=estimator,
        selection_config=RollingHorizon.selection_config_from_dict(cfg.get("selection")),
        solver_config=solver_config(cfg),
    )


class DepartStaggerAllocator:
    """实验侧错峰：同周期同起点按机号升序加 0/Δ/2Δ/3Δ 秒。

    只改冻结出发时刻：``step`` / ``prepare`` / ``resolve`` / ``commit`` 四个出口
    都过同一层错峰，否则重解版的 ``t0`` 与首版不一致，下层会把"同一时刻"排成
    "错开 10 s"，两版航迹不可比。
    """

    def __init__(self, inner: Any, stagger_s: float) -> None:
        if stagger_s < 0.0:
            raise ValueError(f"depart_stagger_s 不能为负：{stagger_s}")
        self.inner = inner
        self.stagger_s = float(stagger_s)

    def apply_cost_update(self, update: Any) -> None:
        self.inner.apply_cost_update(update)

    def cost_state(self) -> dict[str, Any]:
        state = getattr(self.inner, "cost_state", None)
        return {} if not callable(state) else dict(state())

    def reopen(self, task_ids: Sequence[int]) -> tuple[int, ...]:
        """失败航次的任务回灌上层任务池（下一周期重投）；内层若没有该能力就直接报错。"""
        if not hasattr(self.inner, "reopen"):
            raise TypeError(
                f"{type(self.inner).__name__} 没有 reopen：回池需要上层提供正式入口"
            )
        return tuple(self.inner.reopen(task_ids))

    def step(self, new_tasks: Sequence[Any] | None = None) -> CycleSnapshot:
        return self._stagger(self.inner.step(new_tasks))

    def prepare(self, new_tasks: Sequence[Any] | None = None) -> CycleSnapshot:
        return self._stagger(self.inner.prepare(new_tasks))

    def resolve(self) -> CycleSnapshot:
        return self._stagger(self.inner.resolve())

    def commit(self) -> CycleSnapshot:
        return self._stagger(self.inner.commit())

    def allowed_stagger_s(self) -> set[float]:
        return {float(i) * self.stagger_s for i in range(8)}

    def _stagger(self, snap: CycleSnapshot) -> CycleSnapshot:
        if self.stagger_s <= 0.0:
            return snap
        groups: dict[tuple[int, tuple[float, float, float]], list[int]] = {}
        for order in snap.orders:
            key = (int(order.meta.get("cycle", snap.cycle)), tuple(order.start_position))
            groups.setdefault(key, []).append(int(order.uav_id))
        offsets: dict[int, float] = {}
        for ids in groups.values():
            for k, uid in enumerate(sorted(ids)):
                offsets[uid] = k * self.stagger_s
        orders = tuple(
            replace(order, t0=float(order.t0) + offsets.get(int(order.uav_id), 0.0))
            for order in snap.orders
        )
        return replace(snap, orders=orders)
