# -*- coding: utf-8 -*-
"""结果审计：不信任规划器，从落盘 JSON 重新验一遍（多机多周期口径，本实验私有）。

逐周期通用项（每项都能独立否决交付）：
1. 覆盖：每条请求都有可交付航迹，起终点对得上请求、段时刻连续；
2. 航次自洽：``cycle_trace`` 里的时刻/时长与落盘航迹逐位一致；
3. 复核：独立验证器（不是优化器）按同一净空阈值重验；
4. 真几何：棱柱净空 ≥ ``clearance_m``；
5. 预算：按清单里的剩余续航/航程判；
6. 端点净空：起/终点 25 m 球内 SDF ≥ ``clearance_m``（无豁免口径，端点与全域同判）。

跨周期项（本实验的灵魂）：
7. 必撞设计生效：周期1 协调前冲突 ≥ 1（新-新）、周期2 协调前新-旧冲突 ≥ 1；
8. 旧侧冻结：周期2 协调后既有航迹逐段不变（旧机机号 5/6 更大也不许动）；
9. 在飞中规划：周期2 新航迹与既有航迹时间窗重叠 > 0；
10. 协调后归零：逐周期交付集重新检测 0 冲突；全局终检（全部航迹）0 冲突；
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

from path_planning.common.flight_budget import Budget
from path_planning.methods.conflict.conflict_detector import DetectionConfig, detect_conflicts
from path_planning.methods.planning.leg_segments import sampled_positions
from .result_store import trajectory_from_dict
from .runtime import Runtime

__all__ = ["Check", "audit_cycles", "report"]


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


def audit_cycles(runtime: Runtime, fleet, run_dir: str | Path) -> list[Check]:
    """跑通用项 + 跨周期项，返回检查表（质量项由 ``audit.py`` 追加）。"""
    run_dir = Path(run_dir)
    solution = json.loads((run_dir / "solution.json").read_text(encoding="utf-8"))
    trace = json.loads((run_dir / "cycle_trace.json").read_text(encoding="utf-8"))
    traj_raw = json.loads((run_dir / "cycle_trajectories.json").read_text(encoding="utf-8"))

    trajs = {k: trajectory_from_dict(v) for k, v in traj_raw.items()}   # "周期:机号"
    requests = {int(r.uav_id): r for r in fleet.requests}
    checks: list[Check] = []

    # ---- 逐周期：覆盖 / 航次自洽 / 复核 / 真几何 / 预算 / 端点 ----
    for r_idx in sorted(fleet.cycles):
        prefix = f"周期{r_idx}"
        keys = {k for k in trajs if k.startswith(f"{r_idx}:")}
        want = {req.uav_id for req in fleet.cycles[r_idx]}
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
            degraded = traj_raw[f"{r_idx}:{uid}"].get("time_source") == "estimate"
            # 预算按**清单里这一轮的剩余量**判（不是机体名义能力）：降级航次就是被这份
            # 剩余量逼死的，只有按它判才看得出"降级后确实超了、且超得被记了账"。
            bc = Budget(
                endurance_remaining_s=float(req.budget.endurance_remaining_s),
                range_remaining_m=float(req.budget.range_remaining_m),
            ).check(traj.duration, traj.path_len_m, hover_weight=float(traj.hover_weight))
            checks.append(Check(
                f"预算/{prefix}/机{uid}",
                (not bc.ok) if degraded else bool(bc.ok),
                f"时长={traj.duration:.1f}s 弧长={traj.path_len_m:.0f}m "
                f"剩余续航={req.budget.endurance_remaining_s:.0f}s "
                f"剩余航程={req.budget.range_remaining_m:.0f}m"
                + ("（降级交付：超预算必须被记账，不许当成功）" if degraded else ""),
            ))
            checks.append(_endpoint_check(runtime, f"{prefix}/机{uid}", traj))
            checks.append(_stitch_check(r_idx, uid, traj, req, trace))
            if degraded:
                checks.append(_degraded_check(r_idx, uid, traj, trace, solution))

    # ---- 跨周期 ----
    checks.extend(_cross_checks(runtime, fleet, solution, trace, trajs))

    # ---- 冲突：逐周期交付集 + 全局终检（只算 time_source=trajectory）----
    # 估算航迹的时间不是优化结果，按它判冲突等于拿估算背书；门面也不把它送进协调。
    det_trajs = {
        k: t for k, t in trajs.items()
        if traj_raw[k].get("time_source") != "estimate"
    }
    det = _detection(runtime, fleet, det_trajs)
    for r_idx in sorted(fleet.cycles):
        keys = sorted(k for k in det_trajs if k.startswith(f"{r_idx}:"))
        if keys:
            rep = detect_conflicts(tuple(det_trajs[k] for k in keys), det)
            checks.append(Check(
                f"冲突/{'周期'+str(r_idx)}", len(rep.conflicts) == 0,
                f"对比 {len(rep.pairs)} 对，冲突 {len(rep.conflicts)} 对，"
                f"涉及机号 {sorted(rep.conflict_ids)}（已剔除 estimate 航迹）",
            ))
    if det_trajs:
        rep = detect_conflicts(tuple(det_trajs[k] for k in sorted(det_trajs)), det)
        checks.append(Check(
            "冲突/全局终检", len(rep.conflicts) == 0,
            f"对比 {len(rep.pairs)} 对，冲突 {len(rep.conflicts)} 对，"
            f"涉及机号 {sorted(rep.conflict_ids)}（已剔除 estimate 航迹）",
        ))
    return checks


# ---------------------------------------------------------------------------
# 单项检查
# ---------------------------------------------------------------------------


def _coverage_checks(prefix, r_idx, want, requests, trajs, trace) -> list[Check]:
    """覆盖细节：起终点误差、时间断点、航次自洽。"""
    out: list[Check] = []
    tickets = {
        int(t["uav_id"]): t
        for r in trace["cycles"] if int(r["cycle"]) == r_idx
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
        f"航次自洽/{prefix}", not mismatch,
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


def _degraded_check(r_idx: int, uid: int, traj, trace, solution) -> Check:
    """降级交付的三条判据（实现与理由见 ``degrade_checks``）。"""
    from .degrade_checks import degraded_delivery_check
    return degraded_delivery_check(r_idx, uid, traj, trace, solution)


def _stitch_check(r_idx: int, uid: int, traj, req, trace) -> Check:
    """悬停守恒 + 让路只重解一段：多航段航次被"压段"时这两条必红。

    - 悬停守恒：交付航迹的悬停总时长必须等于清单里所有停靠点的 ``service_time_s``
      之和。旧实现把整条 tour 压成一条折线重解，途经点的短悬停会被吃掉。
    - 让路留痕：被协调改过几何的航次，``meta`` 必须带 ``legs`` 与 ``replanned_leg``，
      且 ``replanned_leg`` 落在 ``[0, legs)`` 内（说明只换了那一段、其余段是拼接的）。
    """
    want_hover = sum(float(s.service_time_s) for s in req.stops)
    changed = any(
        t["changed_by_coordination"]
        for c in trace["cycles"] if int(c["cycle"]) == r_idx
        for t in c["tickets"] if int(t["uav_id"]) == uid
    )
    legs = traj.meta.get("legs")
    leg_ok = (
        not changed
        or (legs is not None and int(legs) >= 1
            and 0 <= int(traj.meta.get("replanned_leg", -1)) < int(legs))
    )
    hover_ok = abs(float(traj.hover_duration) - want_hover) <= 1e-3
    return Check(
        f"悬停守恒/让路留痕/周期{r_idx}/机{uid}",
        hover_ok and leg_ok,
        f"悬停 {traj.hover_duration:.1f} s vs 清单 {want_hover:.1f} s；"
        + ("让路航次 legs=%s replanned_leg=%s" % (legs, traj.meta.get("replanned_leg"))
           if changed else "未让路，不要求拼接留痕"),
    )


def _cross_checks(runtime, fleet, solution, trace, trajs) -> list[Check]:
    """跨周期证据：必撞生效、旧侧冻结、在飞中规划、状态账。"""
    out: list[Check] = []
    cross_cycles = {int(r["cycle"]): r for r in trace["cycles"]}

    # 7. 必撞设计生效
    r1 = cross_cycles.get(1)
    if r1 is not None and r1.get("coordination"):
        iters = r1["coordination"]["iterations"]
        before = iters[0]["conflicts_before"] if iters else 0
        out.append(Check(
            "必撞/周期1新-新触发", before >= 1,
            f"协调前冲突 {before} 对（设计 ≥1：机6 航段穿过机5 的同层悬停点）",
        ))
    for r_idx in sorted(cross_cycles):
        if r_idx == 1:
            continue
        pcs = cross_cycles[r_idx].get("pre_conflicts", [])
        new_old = [p for p in pcs if p["kind"] == "new_old"]
        out.append(Check(
            f"必撞/周期{r_idx}新-旧触发", len(new_old) >= 1,
            f"协调前冲突 {len(pcs)} 对，其中新-旧 {len(new_old)} 对"
            f"（设计 ≥1：机3 航段穿过仍在悬停的机5）；明细 "
            f"{[(p['pair'], p['min_distance']) for p in pcs]}",
        ))

        # 8. 旧侧冻结
        frozen = cross_cycles[r_idx].get("old_changed", [])
        out.append(Check(
            f"旧侧冻结/周期{r_idx}", not frozen,
            "既有航迹逐段未变" if not frozen else f"旧侧被改动的机号 {frozen}（不应发生）",
        ))

        # 8b. 旧侧冻结必须是显式白名单：冻结集恰好等于 Registry 既有集、且与本批
        #     请求机号不相交。**不**看机号大小——"旧机号更大仍不动"只作叙事，不作判据；
        #     判据 4 由此不依赖具体机号布局（换一批机号也照样成立）。
        frozen_ids = {int(u) for u in cross_cycles[r_idx].get("frozen_ids", [])}
        existing = {int(u) for u in cross_cycles[r_idx].get("existing_before", [])}
        new_ids = {int(r.uav_id) for r in fleet.cycles.get(r_idx, ())}
        explicit_ok = (
            bool(frozen_ids) and bool(new_ids)
            and frozen_ids == existing and not (frozen_ids & new_ids)
        )
        out.append(Check(
            f"旧侧显式冻结/周期{r_idx}", explicit_ok,
            f"冻结 {sorted(frozen_ids)} == 既有 {sorted(existing)}，"
            f"且与请求机号 {sorted(new_ids)} 不相交"
            f"（白名单判据，与机号大小无关；本例旧机号恰更大，仅作叙事）",
        ))

        # 9. 在飞中规划：本周期新航迹 × 既有航迹的时间窗重叠必须 > 0
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
            f"在飞中规划/周期{r_idx}", bool(overlaps),
            f"新航迹与既有航迹时间窗重叠：{overlaps}",
        ))

    # 11. 状态账
    bad_busy = []
    for key, traj in sorted(trajs.items()):
        if traj.time_source.value == "estimate":
            continue  # 降级航迹不写 Registry，本来就没有 busy 账（见「降级交付」项）
        r_idx, uid = key.split(":")
        busy = _busy_after(solution, int(r_idx), int(uid))
        if busy is not None and abs(busy - traj.t_end) > 1e-3:
            bad_busy.append(f"{key}: busy={busy}≠t_end={traj.t_end:.3f}")
    out.append(Check(
        "状态账/busy_until", not bad_busy,
        "busy_until == t_end（服务时长不双计）" if not bad_busy else f"不一致：{bad_busy}",
    ))

    bad_prune = []
    for r_idx in sorted(cross_cycles):
        now = (r_idx - 1) * float(fleet.cycle_length_s)
        for uid in cross_cycles[r_idx].get("pruned", []):
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
    payload = solution["cycles"].get(str(r_idx))
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

