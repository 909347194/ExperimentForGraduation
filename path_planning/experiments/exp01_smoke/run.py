# -*- coding: utf-8 -*-
"""exp01_smoke：多机多周期 tour 冒烟（两周期五机，无巢口径）。

跑什么：两周期都走生产门面 ``plan_fleet``（Registry 接线在门面里，不在实验里）：
  第 1 周期（t0=0）   机5/机6 同批规划 + 新-新协调；必撞设计 = 机5 在 H 悬停 500 s，
                     机6 的航段同层穿过 H（协调前必有一对冲突，机6 让路）；
  第 2 周期（t0=240） 机3/机4 起飞，约束 = DEM + 建筑 + 第 1 周期已发布航迹（TrajectoryRegistry）；
                     必撞设计 = 机3 的航段同样穿过仍在悬停的 H → 新-旧冲突，旧侧机5 冻结、机3 让路。
看什么：tour 逐航段交付、时间来自优化器、跨周期"既有航迹约束"真的被门面检测并消解、
      旧机号（5/6）大于新机号（3/4）仍逐段不变（冻结靠显式白名单）、busy_until/prune 账目自洽。
记什么：``results/seed_0/`` 六份 JSON（见 PIPELINE.md），外加 figures/ tables/。

离巢错峰：清单里的 ``t0`` 是**周期基准**；同周期同起点的航次按机号加
``runtime.depart_stagger_s``（默认 10 s，本清单无同起点组故偏移为 0）。

点位预检：开跑前先验所有起点/停靠点的 SDF、棱柱净空与**真高带**（选址口径
≥ 8 m、阈值 6 m、真高 ≥ 10 m 且 ≤ 120 m）；不达标直接停，不烧几十分钟规划才发现点位贴楼。
真高带与 ``plan_tour`` 的端点准入同源（都读 ``ctx.agl_range_m``）。
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from path_planning.common.plan_types import TimeSource
from .support.audit_checks import Check
from .support.experiment_run import print_summary, run_cycles_experiment
from .support.runtime import load_runtime

_HERE = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _HERE / "config.yaml"
_DEFAULT_FLEET = _HERE / "uav_config.json"


def precheck_points(runtime, fleet) -> list[Check]:
    """点位预检：逐点 SDF / 棱柱净空 / 真高带（去重后逐点判）。"""
    limit = float(runtime.config.params.clearance_m)
    agl_lo, agl_hi = (float(v) for v in runtime.ctx.agl_range_m)
    seen: set[tuple[float, float, float]] = set()
    checks: list[Check] = []
    for req in fleet.requests:
        named = [("起", req.start_position)] + [
            (f"停{s.id}", s.position) for s in req.stops
        ]
        for tag, p in named:
            key = tuple(round(float(v), 3) for v in p)
            if key in seen:
                continue
            seen.add(key)
            x, y, z = (float(v) for v in p)
            g = float(runtime.scenario.ground_height([x], [y])[0])
            sdf = float(runtime.field.at_one(x, y, z))
            prism = float(runtime.prisms.min_clearance([p], 1.0))
            agl = z - g
            ok = (min(sdf, prism) >= limit - 1e-6
                  and agl_lo - 1e-6 <= agl <= agl_hi + 1e-6)
            checks.append(Check(
                f"点位/机{req.uav_id}/{tag}({x:.1f},{y:.1f},{z:.1f})", ok,
                f"SDF={sdf:.2f} 棱柱={prism:.2f}（阈值 {limit:.2f}）"
                f" ground={g:.1f} agl={agl:.1f}（真高带 [{agl_lo:g}, {agl_hi:g}]）",
            ))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="exp01_smoke：多机多周期 tour 冒烟")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG))
    parser.add_argument("--fleet", default=str(_DEFAULT_FLEET))
    parser.add_argument("--seed", type=int, default=None, help="覆盖 RRT 基础种子")
    parser.add_argument("--out", default=None, help="结果目录（缺省 = results/seed_<种子>）")
    parser.add_argument("--check-points", action="store_true",
                        help="只跑点位预检（不规划），快速核对选址口径")
    parser.add_argument("--skip-point-check", action="store_true",
                        help="跳过点位预检（仅排查编排问题时用，正式跑不要加）")
    args = parser.parse_args(argv)

    runtime = load_runtime(args.config)
    if args.seed is not None:
        runtime.planner.context = dataclasses.replace(runtime.ctx, seed=int(args.seed))
    fleet = runtime.requests_from(args.fleet)

    checks = precheck_points(runtime, fleet)
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name:<{width}}  {c.detail}")
    bad = [c for c in checks if not c.ok]
    print(f"点位预检：{len(checks) - len(bad)}/{len(checks)} 通过")
    if args.check_points:
        return 0 if not bad else 2
    if bad and not args.skip_point_check:
        print("点位未达阈值——先按 PIPELINE.md 的选址口径调整坐标再跑。")
        return 2

    out_dir = (
        Path(args.out)
        if args.out
        else runtime.config.results_dir / f"seed_{runtime.ctx.seed}"
    )
    runs, metrics, _ = run_cycles_experiment(
        runtime, fleet, out_dir,
        meta_extra={"fleet_file": str(Path(args.fleet).name)},
    )
    print_summary(runtime, fleet, runs, metrics, out_dir)
    _warn_missing_trigger(metrics)
    ok_all = all(_acceptable(run, fleet) for run in runs.values())
    return 0 if ok_all else 2


def _acceptable(run, fleet) -> bool:
    """一周期是否"按设计交付"：允许清单里声明的降级交付（预算必须如实超限）。"""
    allow = {req.uav_id for req in fleet.requests if req.allow_estimate_fallback}
    for uid, res in run.results.items():
        if res.ok:
            continue
        if (uid in allow and res.failure is not None
                and res.trajectory is not None
                and res.trajectory.time_source is TimeSource.ESTIMATE):
            continue  # 声明的降级交付：失败码如实记账，审计再判"降级但可解释"
        return False
    return not run.old_changed


def _warn_missing_trigger(metrics) -> None:
    """必撞设计未触发时响亮告警（不是失败，审计会红牌；这里给现场诊断指引）。"""
    cross = metrics["cross_cycle"]
    msgs = []
    for r_idx in sorted(int(k) for k in cross["pre_conflicts"]):
        pcs = cross["pre_conflicts"][str(r_idx)]
        if r_idx == 1:
            continue
        if not any(p["kind"] == "new_old" for p in pcs):
            msgs.append(
                f"周期{r_idx} 协调前没有新-旧冲突（必撞未触发）。"
                f"先看本周期新航迹在目标点上空的 z：若爬高掠过（目标 z=60、航迹 z≳80），"
                f"垂向差超出椭球 z 半轴 15 m 就不会判冲突。"
                f"处置：按 PIPELINE.md 把本周期 t0/途经悬停平移 ±30 s 或把起点抬回 z=60 再跑；"
                f"审计的「必撞/周期{r_idx}新-旧触发」会一直红到触发为止。"
            )
    for m in msgs:
        print("\n⚠ " + m + "\n")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

