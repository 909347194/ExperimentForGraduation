# -*- coding: utf-8 -*-
"""T02：跨周期证据表（必撞触发 / 旧侧冻结 / 在飞重叠 / 状态账）。

这张表是本实验的核心判据，单独成表，不与性能混排。
"""

from __future__ import annotations

import sys

from ._table import write_rows

NAME = "T02_cross_cycle"


def build(ctx) -> list:
    cross = ctx.metrics["cross_cycle"]
    ev: list[dict[str, object]] = []
    for r in ctx.trace["cycles"]:
        r_idx = int(r["cycle"])
        for p in r.get("pre_conflicts", []) or []:
            ev.append({
                "项": f"周期{r_idx} 协调前冲突",
                "值": f"{p['pair']} {p['kind']}",
                "最小距离(m)": p["min_distance"],
                "CPA时刻(s)": p["cpa_time"],
            })
        if r_idx > 1:
            old_ids = cross["existing_before"].get(str(r_idx), [])
            ev.append({
                "项": f"周期{r_idx} 旧侧冻结",
                "值": f"改动机号 {r['old_changed'] or '无'}；既有航迹机号 {old_ids}",
                "最小距离(m)": "", "CPA时刻(s)": "",
            })
            for uid, sec in sorted(cross["overlap_s"].get(str(r_idx), {}).items()):
                ev.append({
                    "项": f"周期{r_idx} 在飞重叠/机{uid}", "值": f"{sec} s",
                    "最小距离(m)": "", "CPA时刻(s)": "",
                })
        busy = cross["busy_until_after"].get(str(r_idx), {})
        for uid, t in sorted(busy.items()):
            if float(t) <= 0.0:
                continue  # 未写入 Registry（降级交付的估算航迹）不产生 busy 账
            ev.append({
                "项": f"周期{r_idx} busy_until/机{uid}", "值": f"{t} s",
                "最小距离(m)": "", "CPA时刻(s)": "",
            })

    for key, traj in sorted(ctx.trajs.items()):
        if traj.time_source.value != "estimate":
            continue
        r_idx, uid = (int(v) for v in key.split(":"))
        code = next(
            (t["failure"] for c in ctx.trace["cycles"] if int(c["cycle"]) == r_idx
             for t in c["tickets"] if int(t["uav_id"]) == uid), None,
        )
        ev.append({
            "项": f"周期{r_idx} 降级交付/机{uid}",
            "值": f"time_source=estimate，失败码 {code}（不进协调、不写 Registry）",
            "最小距离(m)": "", "CPA时刻(s)": "",
        })

    ctx.tab_dir.mkdir(parents=True, exist_ok=True)
    return write_rows(
        ctx.tab_dir / NAME, ev,
        caption="跨周期证据：必撞触发、旧侧冻结、在飞中规划与状态账",
        label="tab:exp01-cross",
        note="由 exp01_smoke/plot/t02_cross_cycle.py 从 results 单源重算。"
             "新-旧冲突对必须出现（必撞设计生效），旧侧改动必须为空；"
             "冻结判据是「冻结集 == 既有集且与请求机号不相交」的白名单口径，"
             "与机号大小无关（本例旧机号 5/6 恰好更大，只作叙事）；"
             "被让路的两台只重解含冲突时刻的那一条航段，悬停时长守恒。",
    )


if __name__ == "__main__":  # pragma: no cover
    from ._shared import run_single
    sys.exit(run_single("exp01 T02 跨周期证据表", build, None))
