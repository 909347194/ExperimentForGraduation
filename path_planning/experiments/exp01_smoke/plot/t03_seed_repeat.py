# -*- coding: utf-8 -*-
"""T03：三种子重复表（把"一次跑通"变成"可复现"的记账）。

为什么单独成表：exp01 的必撞设计在几何上是确定的，但规划链里有 RRT 多初值这一层
随机源（种子进 RRT，选出来的初值再喂给 NLP）。只报一个种子等于只报一条偶然路径；
这张表把种子 0/1/2 摆在一起，逐项比对解本身、必撞触发、让路、降级与悬停守恒。

数据来源：``results/seed_*/`` 下的 ``solution.json``（取哈希）、``cycle_trace.json``
与 ``cycle_trajectories.json``（只读落盘结果，不重跑规划）。``failed`` 取各协调迭代
失败机号并集——多轮场景下"哪轮失败"比"失败几次"更重要，见 T02 之后的正文。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from ._table import write_rows

NAME = "T03_seed_repeat"


def build(ctx) -> list:
    dirs = _seed_dirs(ctx.run_dir.parent)
    rows, hashes = [], []
    for seed_dir in dirs:
        trace = json.loads((seed_dir / "cycle_trace.json").read_text(encoding="utf-8"))
        traj = json.loads((seed_dir / "cycle_trajectories.json").read_text(encoding="utf-8"))
        digest = _digest(seed_dir / "solution.json")
        hashes.append(digest)
        rows.append(_row(seed_dir.name, digest, trace, traj))

    ctx.tab_dir.mkdir(parents=True, exist_ok=True)
    return write_rows(
        ctx.tab_dir / NAME, rows,
        caption="三种子重复：解本身与必撞触发、协调迭代、让路、降级交付逐种子并排",
        label="tab:exp01-seed-repeat",
        note="由 exp01_smoke/plot/t03_seed_repeat.py 从 results/seed_*/ 单源重算。"
             "「解哈希」= solution.json 的 sha256 前 12 位；「前冲突」= 协调前检测到的"
             "冲突对数与最小距离（周期1 新-新、周期2 新-旧）；「协调迭代」= 实际协调"
             "迭代数（上限 max_replan_rounds=3）；「failed」= 各迭代失败机号并集；"
             "「悬停/段」取被让路的两台（机6、机3），「机7」为降级交付那台。"
             + _same_note(hashes),
    )


def _same_note(hashes: list[str]) -> str:
    if len(set(hashes)) == 1:
        return " 三种子解逐位相同：种子只改 RRT 初值，NLP 收敛到同一解，故此表是" \
               "可复现性证据，不提供方差。"
    return " 三种子解不同：种子改动了初值选择，逐项差异即该维度的敏感性。"


def _seed_dirs(root: Path) -> list[Path]:
    dirs = sorted(p for p in Path(root).glob("seed_*") if (p / "cycle_trace.json").exists())
    if not dirs:
        raise ValueError(f"{root} 下没有 seed_*/cycle_trace.json：先跑 run --seed N")
    return dirs


def _digest(path: Path) -> str:
    if not path.exists():
        return "缺"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _row(name: str, digest: str, trace, traj) -> dict[str, object]:
    cycles = {int(c["cycle"]): c for c in trace["cycles"]}
    pre = {r: cycles.get(r, {}).get("pre_conflicts", []) or [] for r in (1, 2)}
    iters = {
        r: (cycles.get(r, {}).get("coordination") or {}).get("iterations", []) or []
        for r in (1, 2)
    }
    replanned = {r: sorted({int(u) for it in iters[r] for u in it["replanned"]}) for r in iters}
    failed = sorted({int(u) for r in iters for it in iters[r] for u in it["failed"]})

    return {
        "种子": name.replace("seed_", ""),
        "解哈希": digest,
        "周期1 前冲突": len(pre[1]),
        "周期1 最小距离": _nearest(pre[1]),
        "周期2 新-旧": sum(1 for p in pre[2] if p["kind"] == "new_old"),
        "周期2 最小距离": _nearest(pre[2]),
        "协调迭代(1/2)": f"{len(iters[1])}/{len(iters[2])}",
        "让路(1)": replanned[1] or "无",
        "让路(2)": replanned[2] or "无",
        "failed": failed or "无",
        "机6 悬停/段": _hover_legs(traj, "1:6"),
        "机3 悬停/段": _hover_legs(traj, "2:3"),
        "机7 交付": _degraded(traj, cycles, "1:7"),
    }


def _nearest(conflicts: list) -> str:
    return "—" if not conflicts else f"{min(float(p['min_distance']) for p in conflicts):.3f}"


def _hover_legs(traj, key: str) -> str:
    t = traj.get(key)
    if t is None:
        return "缺"
    return f"{t['hover_duration_s']:.0f} s / {int(t['meta'].get('legs', 1))} 段"


def _degraded(traj, cycles, key: str) -> str:
    t = traj.get(key)
    if t is None:
        return "缺"
    uid = int(key.split(":")[1])
    code = next(
        (tk["failure"] for c in cycles.values() for tk in c["tickets"]
         if int(tk["uav_id"]) == uid),
        None,
    )
    return f"{t['time_source']}/{code or '无码'}"


if __name__ == "__main__":  # pragma: no cover
    from ._shared import run_single
    sys.exit(run_single("exp01 T03 三种子重复表", build, None))
