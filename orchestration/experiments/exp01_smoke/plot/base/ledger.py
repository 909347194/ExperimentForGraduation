# -*- coding: utf-8 -*-
"""台账层：把落盘结果整理成**四张唯一口径**的表，所有图只读这里。

为什么要这一层
--------------
旧版图各自从 ``loop_trace.json`` / ``trajectories.json`` 重新推导「哪些任务到了、
哪条航次失败」，口径散在各图里，于是同一件事在不同图上可以有不同答案
（"排程数 vs 到达数 vs 有航迹数"混用）。本模块一次性算清，图只负责画。

四张表
------
``tasks``  任务台账：位置、作业高度、地面、AGL、净空、服务时长 —— 回答
           "任务池里有哪些任务"（**输入**，与算法无关）。
``tours``  航次台账：哪个周期、哪架机、几站、按什么顺序、飞了多久多远、
           上层估计多少、失败码是什么 —— 回答 "产生了哪些轨迹"。
``cells``  任务×周期事件：某任务在某周期是"未派/派出未到/到达"，
           由哪架机在第几站送达 —— 回答 "某个任务的这趟活到底飞没飞成"。
``cycles`` 周期台账：当周期派了多少、到多少、回池多少、池里剩多少、
           几何不可行边剔了几条、是否重解、谁闲置、估计 vs 实测总里程
           —— 回答 "闭环这一拍的账"。

口径（与 ``support/report.py``、``support/audit.py`` 保持一致，勿另立）
------------------------------------------------------------------
* **到达** = 该周期该机的航次 ``ok`` 且任务在该航次的 ``task_ids`` 里；
  只有 ``ok`` 的航次才写进 ``trajectories.json``，两者天然对齐；
* **未到达** = 派进了航次，但航次没排出可交付航迹（``ok=false``）；
* **未派** = 该周期压根没进任何航次（含"还没到它出生的周期"之外的等待）；
* **回池** = 失败航次承载的任务退回上层任务池，下一周期可重投；
* 任务编号：``id`` 从 1 连续编到 N，图上直接用 ``tag``（= ``T``+id），
  CSV 里 id 与 tag 同值，不存在第二套"原始号"。

``write_ledgers`` 把四张表落成 CSV，供论文附录/答辩备查；出图不依赖 CSV。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "ARRIVED", "FAILED", "POOL", "ABSENT", "DONE",
    "TaskRow", "TourRow", "Cell", "CycleRow", "Inventory",
    "build", "write_ledgers", "stop_indices",
]

ARRIVED = "arrived"
FAILED = "failed"
POOL = "pool"
ABSENT = "absent"
DONE = "done"     # 已在此前周期到达，之后不再进池（不是"在池"）

STATE_ZH = {ARRIVED: "到达", FAILED: "未到达", POOL: "未派", ABSENT: "未出生",
            DONE: "已完成"}


@dataclass(frozen=True)
class TaskRow:
    """一个任务点的静态属性（来自 ``tasks.json``，与算法无关）。"""

    id: int
    cycle: int
    x: float
    y: float
    z: float
    ground_z: float
    roof_z: float
    agl_m: float
    sdf_m: float
    service_time_s: float
    reward: float

    @property
    def tag(self) -> str:
        """图上唯一的任务编号：``T``+id（id 即清单序号 1…N）。"""
        return f"T{self.id}"


@dataclass(frozen=True)
class TourRow:
    """一个航次（一机 · 一周期 · 一次派遣）：上层分配的产物 + 下层交付的证据。"""

    cycle: int
    uav: int
    nest_id: int          # 起飞巢（ticket.start_nest_id，缺票时回退到机驻巢）
    end_nest_id: int      # 回收巢（ticket.end_nest_id）
    model: str
    ok: bool
    t0: float
    t_end: float
    duration_s: float
    path_len_m: float
    est_m: float
    stops: tuple[int, ...]
    failure: str | None
    detail: str
    worst_clearance_m: float | None
    track: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    #: 失败航次的逐航段诊断（``ticket.failure_diag``）；成功航次为空 dict。
    diag: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def n_stops(self) -> int:
        return len(self.stops)

    @property
    def ratio(self) -> float:
        """实测 / 上层估计；估计为 0 时返回 nan。"""
        return (self.path_len_m / self.est_m) if self.est_m > 0 else float("nan")


@dataclass(frozen=True)
class Cell:
    """某任务在某周期的经历。``stop`` 是 1 起的访问次序。"""

    task_id: int
    cycle: int
    state: str
    uav: int | None = None
    stop: int | None = None
    reopened: bool = False


@dataclass(frozen=True)
class CycleRow:
    """周期台账：一拍决策的收支（TA 出账、PP 交付、回灌剔除都记在这里）。"""

    cycle: int
    t_start: float
    t_end: float
    n_resolves: int                # 周期内重解次数（0/1）
    n_infeasible: int              # 本周期写进代价的不可飞边条数
    n_unresolved: int              # 回灌后仍留在最终解里的不可飞边条数
    n_measured: int                # 本周期写进代价的实测长度条数
    n_intermediate: int            # 中间版失败、最终版成功的航次数
    cost_version: str              # 最终交付版的代价版本
    n_born: int                    # 本周期新到任务数
    n_tickets: int                # 航次数
    n_ok: int                     # 成功航次数
    n_assigned: int               # 派入任务数（去重）
    n_arrived: int                # 到达任务数
    n_failed: int                 # 派出未到达（随航次退回池）
    n_pool: int                   # 本周期仍在池（已出生、未派、未完成）
    n_reopened: int               # 本周期失败后退回池的任务数
    reopened_ids: tuple[int, ...]
    n_moved: int                  # 换机（同一任务先后被两台机承载）
    idle_uavs: tuple[int, ...]    # 本周期整周期无航次的机
    est_m: float                  # 上层估计总里程
    real_m: float                 # 下层实测总里程（只算成功航次）

    @property
    def ratio(self) -> float:
        return (self.real_m / self.est_m) if self.est_m > 0 else float("nan")


@dataclass(frozen=True)
class Inventory:
    tasks: tuple[TaskRow, ...]
    tours: tuple[TourRow, ...]
    cells: tuple[Cell, ...]
    cycles: tuple[int, ...]
    cycle_rows: tuple[CycleRow, ...]
    fleet: tuple[int, ...]
    per_cycle: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {t.id: t for t in self.tasks})

    # ---- 派生查询 ---------------------------------------------------
    def tag(self, tid: int) -> str:
        """id → 图上标签（``T``+id）。"""
        return self.task(tid).tag

    def task(self, tid: int) -> TaskRow:
        return self._by_id[tid]

    def cycle_row(self, cycle: int) -> CycleRow:
        return next(r for r in self.cycle_rows if r.cycle == int(cycle))

    def cell(self, tid: int, cycle: int) -> Cell:
        return self._cell_map[(tid, cycle)]

    def cells_of(self, tid: int) -> tuple[Cell, ...]:
        return tuple(self.cell(tid, c) for c in self.cycles)

    def tours_of(self, cycle: int, *, ok_only: bool = False) -> tuple[TourRow, ...]:
        out = [t for t in self.tours if t.cycle == cycle]
        return tuple(t for t in out if t.ok) if ok_only else tuple(out)

    def arrived_cycle(self, tid: int) -> int | None:
        for c in self.cells_of(tid):
            if c.state == ARRIVED:
                return c.cycle
        return None

    def final_state(self, tid: int) -> str:
        """终局：到达 / 未到达（派过没到）/ 未派（从未进过航次）。"""
        states = {c.state for c in self.cells_of(tid)}
        if ARRIVED in states:
            return ARRIVED
        return FAILED if FAILED in states else POOL

    def uavs(self) -> tuple[int, ...]:
        return tuple(sorted({t.uav for t in self.tours}))

    def track_of(self, cycle: int, uav: int) -> dict[str, Any] | None:
        for t in self.tours:
            if t.cycle == cycle and t.uav == uav and t.track:
                return t.track
        return None

    @property
    def n_tasks(self) -> int:
        return len(self.tasks)

    @property
    def n_arrived(self) -> int:
        return sum(1 for t in self.tasks if self.arrived_cycle(t.id) is not None)


def stop_indices(track: dict[str, Any], pts: Iterable[tuple[float, float]]) -> list[int]:
    """航迹上"离每个站点最近"的采样下标；强制单调递增，避免回头误配。"""
    import numpy as np

    xs, ys = np.asarray(track["x"]), np.asarray(track["y"])
    out: list[int] = []
    lo = 0
    for px, py in pts:
        if lo >= len(xs):
            out.append(len(xs) - 1)
            continue
        d = np.hypot(xs[lo:] - float(px), ys[lo:] - float(py))
        i = lo + int(d.argmin())
        out.append(i)
        lo = i
    return out


def build(ctx) -> Inventory:
    """从 ``Ctx`` 构建四张台账（纯读，不重跑规划器）。"""
    from ...support.fleet import load_uav_config

    spec = {int(r["id"]): r for r in load_uav_config()}
    fleet = tuple(sorted(spec))

    tasks: list[TaskRow] = []
    born_by_cycle: dict[int, int] = {}
    for t in ctx.tasks:
        born_by_cycle[int(t["cycle"])] = born_by_cycle.get(int(t["cycle"]), 0) + 1
    per_cycle = max(born_by_cycle.values(), default=len(ctx.tasks))
    for t in ctx.tasks:
        tasks.append(TaskRow(
            id=int(t["id"]), cycle=int(t["cycle"]),
            x=float(t["x"]), y=float(t["y"]), z=float(t["z"]),
            ground_z=float(t["ground_z"]), roof_z=float(t["roof_z"]),
            agl_m=float(t["agl_m"]), sdf_m=float(t["sdf_m"]),
            service_time_s=float(t.get("service_time_s") or 0.0),
            reward=float(t.get("reward") or 1.0),
        ))
    tasks.sort(key=lambda r: (r.cycle, r.id))
    by_id = {r.id: r for r in tasks}

    plans = sorted(ctx.trace["cycles"], key=lambda c: int(c["cycle"]))
    cycles = tuple(int(p["cycle"]) for p in plans)

    tours: list[TourRow] = []
    cells: list[Cell] = []
    for plan in plans:
        c = int(plan["cycle"])
        reopened = {int(x) for x in (plan.get("reopened") or [])}
        est = plan.get("est_cost") or {}
        for uav_s, ticket in sorted(plan["tickets"].items(), key=lambda kv: int(kv[0])):
            u = int(uav_s)
            tids = tuple(int(x) for x in ticket.get("task_ids") or ())
            gate = ticket.get("gate") or {}
            row = spec.get(u, {})
            # 两端巢以航次结构为准（落盘字段）；只有旧结果或假件缺票时才回退到机驻巢
            home = int(row.get("nest_id", -1))
            start_nest = ticket.get("start_nest_id")
            start_nest = home if start_nest is None else int(start_nest)
            end_nest = ticket.get("end_nest_id")
            end_nest = start_nest if end_nest is None else int(end_nest)
            tours.append(TourRow(
                cycle=c, uav=u,
                nest_id=start_nest,
                end_nest_id=end_nest,
                model=str(row.get("model", "?")),
                ok=bool(ticket.get("ok")),
                t0=float(ticket.get("t0") or 0.0),
                t_end=float(ticket.get("t_end") or 0.0),
                duration_s=float(ticket.get("duration_s") or 0.0),
                path_len_m=float(ticket.get("path_len_m") or 0.0),
                est_m=float(est.get(uav_s, 0.0) or 0.0),
                stops=tids,
                failure=ticket.get("failure"),
                detail=str(ticket.get("detail") or ""),
                worst_clearance_m=(float(gate["worst_clearance_m"])
                                   if gate.get("worst_clearance_m") is not None else None),
                track=(ctx.tracks.get(str(c), {}) or {}).get(str(u), {}) or {},
                diag=dict(ticket.get("failure_diag") or {}),
            ))
            state = ARRIVED if ticket.get("ok") else FAILED
            for k, tid in enumerate(tids, start=1):
                cells.append(Cell(task_id=tid, cycle=c, state=state, uav=u, stop=k,
                                  reopened=tid in reopened))

    have = {(x.task_id, x.cycle) for x in cells}
    for row in tasks:
        for c in cycles:
            if (row.id, c) in have:
                continue
            # 还没出生的周期与"出生了但没派"要分开：只画它存在之后的周期
            state = ABSENT if c < row.cycle else POOL
            cells.append(Cell(task_id=row.id, cycle=c, state=state, reopened=False))
    # 到达之后的周期标 DONE 而不是 POOL：已完成的任务不在池里，
    # 混成"在池"会让人误以为它还能被重新派出去。
    first_arr: dict[int, int] = {}
    for x in cells:
        if x.state == ARRIVED:
            first_arr[x.task_id] = min(first_arr.get(x.task_id, 99), x.cycle)
    cells = [
        Cell(task_id=x.task_id, cycle=x.cycle,
             state=(DONE if x.cycle > first_arr.get(x.task_id, 99) else x.state),
             uav=None if x.cycle > first_arr.get(x.task_id, 99) else x.uav,
             stop=None if x.cycle > first_arr.get(x.task_id, 99) else x.stop,
             reopened=False if x.cycle > first_arr.get(x.task_id, 99) else x.reopened)
        for x in cells
    ]
    cells.sort(key=lambda x: (by_id[x.task_id].id, x.cycle))

    # ---- 周期台账（一拍一笔账） -------------------------------------
    cycle_rows: list[CycleRow] = []
    for plan in plans:
        c = int(plan["cycle"])
        ts = [t for t in tours if t.cycle == c]
        idle = tuple(u for u in fleet if u not in {t.uav for t in ts})
        n_born = sum(1 for r in tasks if r.cycle == c)
        n_assigned = len({tid for t in ts for tid in t.stops})
        n_arrived = sum(1 for x in cells if x.cycle == c and x.state == ARRIVED)
        n_failed = sum(1 for x in cells if x.cycle == c and x.state == FAILED)
        n_pool = sum(1 for x in cells if x.cycle == c and x.state == POOL)
        reopened = tuple(int(x) for x in (plan.get("reopened") or []))
        est = plan.get("est_cost") or {}
        cycle_rows.append(CycleRow(
            cycle=c,
            t_start=float(plan.get("t_start") or 0.0),
            t_end=float(plan.get("t_end") or 0.0),
            n_resolves=int(plan.get("n_resolves") or 0),
            n_infeasible=len(plan.get("infeasible_edges") or []),
            n_unresolved=len(plan.get("unresolved_infeasible_edges") or []),
            n_measured=int(plan.get("n_measured_edges") or 0),
            n_intermediate=int(plan.get("n_intermediate_failures") or 0),
            cost_version=str(plan.get("cost_version") or "base"),
            n_born=n_born,
            n_tickets=len(ts),
            n_ok=sum(1 for t in ts if t.ok),
            n_assigned=n_assigned,
            n_arrived=n_arrived,
            n_failed=n_failed,
            n_pool=n_pool,
            n_reopened=len(reopened),
            reopened_ids=reopened,
            n_moved=len(plan.get("moved") or {}),
            idle_uavs=idle,
            est_m=sum(float(v) for v in est.values()),
            real_m=sum(t.path_len_m for t in ts if t.ok),
        ))

    inv = Inventory(tasks=tuple(tasks), tours=tuple(tours), cells=tuple(cells),
                    cycles=cycles, cycle_rows=tuple(cycle_rows), fleet=fleet,
                    per_cycle=per_cycle)
    object.__setattr__(inv, "_cell_map", {(x.task_id, x.cycle): x for x in inv.cells})
    return inv


# ----------------------------------------------------------------------
# 落盘：论文附录 / 答辩备查
# ----------------------------------------------------------------------
def write_ledgers(inv: Inventory, out_dir: Path) -> list[Path]:
    """把四张台账写成 CSV（UTF-8 BOM，Excel 直接打开不乱码）。"""
    import csv

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    p1 = out_dir / "task_ledger.csv"
    with p1.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["任务号", "周期", "x_m", "y_m", "作业高z_m",
                    "地面_m", "屋顶_m", "AGL_m", "净空SDF_m", "服务_s",
                    "终局", "到达周期", "执行机", "第几站"])
        for t in inv.tasks:
            arr = inv.arrived_cycle(t.id)
            cell = next((inv.cell(t.id, c) for c in inv.cycles
                         if inv.cell(t.id, c).state == ARRIVED), None)
            w.writerow([t.tag, t.cycle, f"{t.x:.3f}", f"{t.y:.3f}",
                        f"{t.z:.2f}", f"{t.ground_z:.2f}", f"{t.roof_z:.2f}",
                        f"{t.agl_m:.2f}", f"{t.sdf_m:.2f}", f"{t.service_time_s:.1f}",
                        STATE_ZH[inv.final_state(t.id)], arr or "",
                        cell.uav if cell else "", cell.stop if cell else ""])

    p2 = out_dir / "tour_ledger.csv"
    with p2.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["周期", "机号", "起点巢", "终点巢", "机型", "成功", "起飞s", "落地s",
                    "时长s", "实测航程m", "上层估计m", "实测/估计", "站数",
                    "任务顺序", "最差净空m", "失败码", "失败说明"])
        for r in inv.tours:
            w.writerow([r.cycle, r.uav, r.nest_id, r.end_nest_id, r.model, int(r.ok),
                        f"{r.t0:.1f}", f"{r.t_end:.1f}", f"{r.duration_s:.1f}",
                        f"{r.path_len_m:.1f}", f"{r.est_m:.1f}",
                        f"{r.ratio:.3f}", r.n_stops,
                        "→".join(inv.task(int(s)).tag for s in r.stops),
                        "" if r.worst_clearance_m is None else f"{r.worst_clearance_m:.2f}",
                        r.failure or "", r.detail])

    p3 = out_dir / "task_cycle_events.csv"
    with p3.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["任务", "周期", "状态", "执行机", "第几站", "是否回池"])
        for x in inv.cells:
            w.writerow([inv.task(x.task_id).tag, x.cycle,
                        STATE_ZH[x.state], x.uav if x.uav is not None else "",
                        x.stop if x.stop is not None else "", int(x.reopened)])

    p4 = out_dir / "cycle_ledger.csv"
    with p4.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["周期", "起s", "止s", "重解次数", "写进代价的不可飞边", "实测边",
                    "残留不可飞边",
                    "中间版失败", "解版本", "新到任务", "航次", "成功航次",
                    "派入任务", "到达", "派出未到达", "池中未派", "回池", "换机",
                    "闲置机", "估计总里程m", "实测总里程m", "实测/估计"])
        for r in inv.cycle_rows:
            w.writerow([r.cycle, f"{r.t_start:.0f}", f"{r.t_end:.0f}",
                        r.n_resolves, r.n_infeasible, r.n_measured, r.n_unresolved,
                        r.n_intermediate, r.cost_version,
                        r.n_born, r.n_tickets, r.n_ok, r.n_assigned, r.n_arrived,
                        r.n_failed, r.n_pool, r.n_reopened, r.n_moved,
                        "、".join(map(str, r.idle_uavs)),
                        f"{r.est_m:.1f}", f"{r.real_m:.1f}", f"{r.ratio:.3f}"])
    return [p1, p2, p3, p4]
