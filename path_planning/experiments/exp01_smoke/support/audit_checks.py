# -*- coding: utf-8 -*-
"""结果审计：不信任规划器，从落盘 JSON 重新验一遍（多机多轮口径，本实验私有）。

逐轮通用项（每项都能独立否决交付）：
1. 覆盖：每条请求都有可交付航迹，起终点对得上请求、段时刻连续；
2. 票自洽：``round_trace`` 里的时刻/时长与落盘航迹逐位一致；
3. 复核：独立验证器（不是优化器）按同一净空阈值重验；
4. 真几何：棱柱净空 ≥ ``clearance_m``；
5. 预算：按清单里的剩余续航/航程判；
6. 端点净空：起/终点 25 m 球内 SDF ≥ ``clearance_m``（无豁免口径，端点与全域同判）。

跨轮项（本实验的灵魂）：
7. 必撞设计生效：轮1 协调前冲突 ≥ 1（新-新）、轮2 协调前新-旧冲突 ≥ 1；
8. 旧侧冻结：轮2 协调后既有航迹逐段不变（机号小者不许动）；
9. 在飞中规划：轮2 新航迹与既有航迹时间窗重叠 > 0；
10. 协调后归零：逐轮交付集重新检测 0 冲突；全局终检（全部航迹）0 冲突；
11. 状态账：``busy_until`` == 航迹 ``t_end``；``prune`` 摘除的航迹确实已落地。

为什么读 JSON 而不读内存：审计要能对**几个月后的结果文件**复跑。若审计依赖
规划过程中的对象，文件格式一改就再也审不动了。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from path_planning.methods.conflict.conflict_detector import DetectionConfig, detect_conflicts
from path_planning.methods.planning.leg_segments import sampled_positions
from .flight_metrics import budget_check
from .result_store import trajectory_from_dict
from .runtime import Runtime

__all__ = ["Check", "audit_rounds", "report"]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def report(checks: list[Check]) -> int:
    """打印检查表；1 = 有失败项。"""
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name:<{width}}  {c.detail}")
    failed = [c for c in checks if not c.ok]
    print(f"\n审计：{len(checks) - len(failed)}/{len(checks)} 通过")
    return 0 if not failed else 1


def audit_rounds(runtime: Runtime, fleet, run_dir: str | Path) -> list[Check]:
    """跑通用项 + 跨轮项，返回检查表（质量项由 ``audit.py`` 追加）。"""
    run_dir = Path(run_dir)
    solution = json.loads((run_dir / "solution.json").read_text(encoding="utf-8"))
    trace = json.loads((run_dir / "round_trace.json").read_text(encoding="utf-8"))
    traj_raw = json.loads((run_dir / "round_trajectories.json").read_text(encoding="utf-8"))

    trajs = {k: trajectory_from_dict(v) for k, v in traj_raw.items()}   # "轮:机号"
    requests = {int(r.uav_id): r for r in fleet.requests}
    checks: list[Check] = []

    # ---- 逐轮：覆盖 / 票自洽 / 复核 / 真几何 / 预算 / 端点 ----
    for r_idx in sorted(fleet.rounds):
        prefix = f"轮{r_idx}"
        keys = {k for k in trajs if k.startswith(f"{r_idx}:")}
        want = {req.uav_id for req in fleet.rounds[r_idx]}
        got = {int(k.split(":")[1]) for k in keys}
        missing = sorted(want - got)
        checks.append(Check(
            f"覆盖/{prefix}", not missing,
            f"{len(got)}/{len(want)} 条交付" + (f"，缺 {missing}" if missing else ""),
        ))
        checks.extend(_coverage_checks(prefix, r_idx, want, requests, trajs, trace))
        for uid in sorted(want & got):
            traj = trajs[f"{r_idx}:{uid}"]
            req = requests[uid]
            checks.append(Check(
                f"预算/{prefix}/机{uid}",
                bool(budget_check(traj, req.vehicle).ok),
                f"时长={traj.duration:.1f}s 弧长={traj.path_len_m:.0f}m "
                f"剩余续航={req.budget.endurance_remaining_s:.0f}s "
                f"剩余航程={req.budget.range_remaining_m:.0f}m",
            ))
            checks.append(_endpoint_check(runtime, f"{prefix}/机{uid}", traj))

    # ---- 跨轮 ----
    checks.extend(_cross_checks(runtime, fleet, solution, trace, trajs))

    # ---- 冲突：逐轮交付集 + 全局终检 ----
    det = _detection(runtime, fleet, trajs)
    for r_idx in sorted(fleet.rounds):
        keys = sorted(k for k in trajs if k.startswith(f"{r_idx}:"))
        if keys:
            rep = detect_conflicts(tuple(trajs[k] for k in keys), det)
            checks.append(Check(
                f"冲突/{'轮'+str(r_idx)}", len(rep.conflicts) == 0,
                f"对比 {len(rep.pairs)} 对，冲突 {len(rep.conflicts)} 对，"
                f"涉及机号 {sorted(rep.conflict_ids)}",
            ))
    if trajs:
        rep = detect_conflicts(tuple(trajs[k] for k in sorted(trajs)), det)
        checks.append(Check(
            "冲突/全局终检", len(rep.conflicts) == 0,
            f"对比 {len(rep.pairs)} 对，冲突 {len(rep.conflicts)} 对，"
            f"涉及机号 {sorted(rep.conflict_ids)}",
        ))
    return checks


# ---------------------------------------------------------------------------
# 单项检查
# ---------------------------------------------------------------------------


def _coverage_checks(prefix, r_idx, want, requests, trajs, trace) -> list[Check]:
    """覆盖细节：起终点误差、时间断点、票自洽。"""
    out: list[Check] = []
    tickets = {
        int(t["uav_id"]): t
        for r in trace["rounds"] if int(r["round"]) == r_idx
        for t in r["tickets"]
    }
    bad = []
    for uid in sorted(want):
        key = f"{r_idx}:{uid}"
        if key not in trajs:
            continue
        traj = trajs[key]
        req = requests[uid]
        start_err = float(np.linalg.norm(np.subtract(traj.start_position, req.start_position)))
        end_err = float(np.linalg.norm(np.subtract(traj.end_position, req.stops[-1].position)))
        gaps = sum(
            1 for a, b in zip(traj.segments, traj.segments[1:]) if abs(b.t0 - a.t1) > 1e-6
        )
        if start_err > 1e-3 or end_err > 1e-3 or gaps:
            bad.append(f"机{uid}(起{start_err:.1e}/终{end_err:.1e}/断{gaps})")
    out.append(Check(
        f"覆盖细节/{prefix}", not bad,
        "起终点误差 ≤1 mm、段时刻连续" if not bad else f"异常：{bad}",
    ))

    mismatch = []
    for uid, t in tickets.items():
        key = f"{r_idx}:{uid}"
        if key not in trajs:
            continue
        traj = trajs[key]
        if t["t_end"] is not None and abs(float(t["t_end"]) - traj.t_end) > 1e-3:
            mismatch.append(f"机{uid} t_end {t['t_end']}≠{traj.t_end:.3f}")
        if t["duration_s"] is not None and abs(float(t["duration_s"]) - traj.duration) > 1e-2:
            mismatch.append(f"机{uid} 时长 {t['duration_s']}≠{traj.duration:.3f}")
    out.append(Check(
        f"票自洽/{prefix}", not mismatch,
        "trace 时刻与航迹逐位一致" if not mismatch else f"不一致：{mismatch}",
    ))
    return out


def _endpoint_check(runtime: Runtime, name: str, traj) -> Check:
    """端点 25 m 球内 SDF ≥ 阈值（无豁免：端点与全域同判）。"""
    limit = float(runtime.config.params.clearance_m)
    pts = sampled_positions(traj, 0.2)
    mins = []
    for end in (traj.start_position, traj.end_position):
        near = np.linalg.norm(pts - np.asarray(end, dtype=float), axis=1) <= 25.0
        mins.append(float(np.min(runtime.field.at(pts[near]))) if near.any() else float("inf"))
    worst = min(mins)
    return Check(
        f"端点净空/{name}", worst >= limit - 1e-6,
        f"起/终点 25 m 球内 min={worst:.2f} m，阈值 {limit:.2f} m（无豁免口径）",
    )


def _cross_checks(runtime, fleet, solution, trace, trajs) -> list[Check]:
    """跨轮证据：必撞生效、旧侧冻结、在飞中规划、状态账。"""
    out: list[Check] = []
    rounds_payload = solution["rounds"]
    cross_rounds = {int(r["round"]): r for r in trace["rounds"]}

    # 7. 必撞设计生效
    r1 = cross_rounds.get(1)
    if r1 is not None and r1.get("coordination"):
        before = r1["coordination"]["rounds"][0]["conflicts_before"] if r1["coordination"]["rounds"] else 0
        out.append(Check(
            "必撞/轮1新-新触发", before >= 1,
            f"协调前冲突 {before} 对（设计 ≥1，UAV2 掠过 UAV1 终点悬停）",
        ))
    for r_idx in sorted(cross_rounds):
        if r_idx == 1:
            continue
        pcs = cross_rounds[r_idx].get("pre_conflicts", [])
        new_old = [p for p in pcs if p["kind"] == "new_old"]
        out.append(Check(
            f"必撞/轮{r_idx}新-旧触发", len(new_old) >= 1,
            f"协调前冲突 {len(pcs)} 对，其中新-旧 {len(new_old)} 对"
            f"（设计 ≥1，新机掠过既有悬停）；明细 {[(p['pair'], p['min_distance']) for p in pcs]}",
        ))

        # 8. 旧侧冻结
        frozen = cross_rounds[r_idx].get("old_changed", [])
        out.append(Check(
            f"旧侧冻结/轮{r_idx}", not frozen,
            "既有航迹逐段未变" if not frozen else f"旧侧被改动的机号 {frozen}（不应发生）",
        ))

        # 9. 在飞中规划：本轮新航迹 × 既有航迹的时间窗重叠必须 > 0
        existing = {int(u) for u in cross_rounds[r_idx].get("existing_before", [])}
        overlaps = []
        for key, traj in sorted(trajs.items()):
            if not key.startswith(f"{r_idx}:") or int(key.split(":")[1]) in existing:
                continue
            for uid in sorted(existing):
                old = _find_traj(trajs, uid)
                if old is None:
                    continue
                lo, hi = max(traj.t0, old.t0), min(traj.t_end, old.t_end)
                if hi - lo > 0:
                    overlaps.append((key, uid, round(hi - lo, 1)))
        out.append(Check(
            f"在飞中规划/轮{r_idx}", bool(overlaps),
            f"新航迹与既有航迹时间窗重叠：{overlaps}",
        ))

    # 11. 状态账
    bad_busy = []
    for key, traj in sorted(trajs.items()):
        r_idx, uid = key.split(":")
        busy = _busy_after(solution, int(r_idx), int(uid))
        if busy is not None and abs(busy - traj.t_end) > 1e-3:
            bad_busy.append(f"{key}: busy={busy}≠t_end={traj.t_end:.3f}")
    out.append(Check(
        "状态账/busy_until", not bad_busy,
        "busy_until == t_end（服务时长不双计）" if not bad_busy else f"不一致：{bad_busy}",
    ))

    bad_prune = []
    for r_idx in sorted(cross_rounds):
        now = (r_idx - 1) * float(fleet.cycle_length_s)
        for uid in cross_rounds[r_idx].get("pruned", []):
            key = _find_key(trajs, int(uid))
            if key is not None and trajs[key].t_end > now + 1e-6:
                bad_prune.append(f"{key}: t_end={trajs[key].t_end:.1f} > {now:g}")
    out.append(Check(
        "状态账/prune", not bad_prune,
        "摘除的航迹均已落地" if not bad_prune else f"误摘：{bad_prune}",
    ))
    return out


def _find_traj(trajs, uid: int):
    keys = [k for k in trajs if int(k.split(":")[1]) == uid]
    return None if not keys else trajs[max(keys, key=lambda k: int(k.split(":")[0]))]


def _find_key(trajs, uid: int) -> str | None:
    keys = [k for k in trajs if int(k.split(":")[1]) == uid]
    return None if not keys else max(keys, key=lambda k: int(k.split(":")[0]))


def _busy_after(solution, r_idx: int, uid: int) -> float | None:
    payload = solution["rounds"].get(str(r_idx))
    if payload is None:
        return None
    busy = payload.get("registry_busy_until") or {}
    v = busy.get(str(uid))
    return None if v is None else float(v)


def _detection(runtime: Runtime, fleet, trajs) -> DetectionConfig:
    vehicle = min(fleet.vehicles.values(), key=lambda v: v.a_max_mps2)
    return DetectionConfig.from_params(
        runtime.config.params, vehicle, e_geo_m=runtime.config.e_geo_m
    )
