# -*- coding: utf-8 -*-
"""结果审计：不信任规划器，从落盘航迹重新验一遍（四个实验共用同一套判据）。

五项（每项都能独立否决交付）：
1. 覆盖：每条请求都有可交付航迹，起终点对得上请求、段时刻连续；
2. 复核：独立验证器（不是优化器）按同一净空阈值重验；
3. 真几何：棱柱净空 ≥ ``clearance_m``；
4. 预算：按清单里的剩余续航/航程判；
5. 冲突：重新检测，机队级必须 0 对冲突（可按实验关闭该要求，但必须显式）。

为什么读 JSON 而不读内存：审计要能对**几个月后的结果文件**复跑。若审计依赖
规划过程中的对象，文件格式一改就再也审不动了。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .flight_metrics import budget_check, trajectory_metrics
from .result_store import trajectory_from_dict
from .runtime import Runtime
from path_planning.methods.conflict.conflict_detector import DetectionConfig, detect_conflicts

__all__ = ["Check", "audit_run"]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def audit_run(
    runtime: Runtime,
    fleet,
    run_dir: str | Path,
    *,
    require_zero_conflicts: bool = True,
    extra: list[Check] | None = None,
) -> list[Check]:
    """跑完五项检查（额外检查由实验自己追加），返回检查表。"""
    payload = json.loads((Path(run_dir) / "solution.json").read_text(encoding="utf-8"))
    trajs = {
        int(uid): trajectory_from_dict(res["trajectory"])
        for uid, res in payload["results"].items()
        if res.get("trajectory") is not None
    }
    requests = {int(r.uav_id): r for r in fleet.requests}

    checks = [_coverage(requests, trajs)]
    for uid, traj in sorted(trajs.items()):
        m = trajectory_metrics(
            traj, vehicle=fleet.vehicles[uid], field=runtime.field,
            prisms=runtime.prisms, params=runtime.config.params,
            alt_range_m=runtime.ctx.alt_range_m,
        )
        checks.append(Check(
            f"复核/机{uid}", bool(m["verified"]), f"violations={m['violations']} 段={m['segments']}"
        ))
        checks.append(Check(
            f"真几何/机{uid}", m["prism_min_m"] >= runtime.config.params.clearance_m - 1e-6,
            f"min={m['prism_min_m']:.2f}m 阈值={runtime.config.params.clearance_m:.2f}m",
        ))
        checks.append(Check(
            f"预算/机{uid}", bool(budget_check(traj, requests[uid].vehicle).ok),
            f"时长={traj.duration:.1f}s 弧长={traj.path_len_m:.0f}m "
            f"续航={requests[uid].budget.endurance_remaining_s:.0f}s "
            f"航程={requests[uid].budget.range_remaining_m:.0f}m",
        ))

    if trajs:
        checks.append(_conflict_check(runtime, fleet, trajs, require_zero_conflicts))
    if extra:
        checks.extend(extra)
    return checks


def report(checks: list[Check]) -> int:
    """打印检查表；1 = 有失败项。"""
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name:<{width}}  {c.detail}")
    failed = [c for c in checks if not c.ok]
    print(f"\n审计：{len(checks) - len(failed)}/{len(checks)} 通过")
    return 0 if not failed else 1


# ---------------------------------------------------------------------------
# 单项检查
# ---------------------------------------------------------------------------


def _coverage(requests, trajs) -> Check:
    missing = sorted(set(requests) - set(trajs))
    if missing:
        return Check("覆盖", False, f"缺航迹的机号 {missing}")
    for uid, traj in trajs.items():
        req = requests[uid]
        start_err = float(np.linalg.norm(np.subtract(traj.start_position, req.start_position)))
        end_err = float(np.linalg.norm(np.subtract(traj.end_position, req.stops[-1].position)))
        gaps = sum(
            1 for a, b in zip(traj.segments, traj.segments[1:]) if abs(b.t0 - a.t1) > 1e-6
        )
        if start_err > 1e-3 or end_err > 1e-3 or gaps:
            return Check(
                "覆盖", False,
                f"机 {uid} 起点误差 {start_err:.2e}、终点误差 {end_err:.2e}、时间断点 {gaps}",
            )
    return Check("覆盖", True, f"{len(trajs)} 条航迹，起终点与时刻均连续")


def _conflict_check(runtime: Runtime, fleet, trajs, require_zero: bool) -> Check:
    """用同批机体中**最保守**的机型建检测配置（异构混合时取小半径者）。"""
    vehicles = list(fleet.vehicles.values())
    vehicle = min(vehicles, key=lambda v: v.a_max_mps2)
    det = DetectionConfig.from_params(
        runtime.config.params, vehicle, e_geo_m=runtime.config.e_geo_m
    )
    report_ = detect_conflicts(tuple(trajs.values()), det)
    n_conf = len(report_.conflicts)
    ok = (n_conf == 0) if require_zero else True
    return Check(
        "冲突", ok,
        f"对比 {len(report_.pairs)} 对，冲突 {n_conf} 对，"
        f"涉及机号 {sorted(report_.conflict_ids)}，弦对 {int(report_.chord_pairs)}",
    )
