# -*- coding: utf-8 -*-
"""deep_dive.py — 长程结果的三个聚焦问题深挖（只读 json）。

Q1 废飞明细：每次违约的周期/任务/标签/所属航次代价
Q2 从未出动任务（T52/T104/T105）的命运轨迹：每周期在池中的状态
Q3 震荡任务（多次出动多次废飞）的完整轨迹
Q4 经济性剔除的证据：被 _prune/选择丢弃的任务奖励 vs 代价
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "results"


def main() -> None:
    rdir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    m = json.loads((rdir / "metrics.json").read_text(encoding="utf-8"))
    sol = json.loads((rdir / "solution.json").read_text(encoding="utf-8"))
    cyc = m["cycles"]
    tasks = {int(t["id"]): t for t in sol["tasks"]}

    print("=" * 72)
    print("Q1. 废飞事件明细（周期/任务/奖励/标签/航次代价）")
    print("=" * 72)
    for c, sc in zip(cyc, sol["cycles"]):
        if not c["infeasible_ids"]:
            continue
        # 该周期航次：uav -> (task_ids, cost)
        tours = sc.get("tours", [])
        bad = {int(i) for i in c["infeasible_ids"]}
        for t in tours:
            tids = {int(i) for i in t.get("task_ids", [])}
            hit = tids & bad
            if not hit:
                continue
            for tid in sorted(hit):
                tags = c["infeasible_tags"].get(str(tid), [])
                tk = tasks.get(tid, {})
                print(f"  周期{c['cycle']:>2} T{tid:>3} R={tk.get('reward', 0):>5.1f} "
                      f"航次={sorted(tids)} 代价={t.get('est_cost', 0)/1000:>6.1f}km "
                      f"UAV{t.get('uav_id')} 标签={tags}")

    print()
    print("=" * 72)
    print("Q2. 从未出动任务的命运（每周期：在池/选中/执行/淘汰/不可达）")
    print("=" * 72)
    never = [52, 104, 105]
    for tid in never:
        tk = tasks.get(tid, {})
        print(f"T{tid}: R={tk.get('reward')} 到达={float(tk.get('arrival_time', 0)):.0f}s "
              f"latest={tk.get('latest'):.0f}s 终态={tk.get('status')}")
        for c in cyc:
            in_pool = (tid in c["deferred_ids"]) or (tid in c["executed_ids"])
            if not in_pool:
                continue
            sel = "选中" if tid in c.get("executed_ids", []) else "滞留"
            exp = " ★淘汰" if tid in c["expired_ids"] else ""
            unr = " ▲不可达(过于偏远)" if tid in c.get("unreachable_ids", []) else ""
            print(f"    周期{c['cycle']:>2}: {sel}{exp}{unr}")

    print()
    print("=" * 72)
    print("Q3. 震荡任务轨迹（出动≥2 或废飞≥1）")
    print("=" * 72)
    exec_count: dict[int, list[int]] = {}
    waste_at: dict[int, list[int]] = {}
    for c in cyc:
        for tid in c["executed_ids"]:
            exec_count.setdefault(int(tid), []).append(c["cycle"])
        for tid in c["infeasible_ids"]:
            waste_at.setdefault(int(tid), []).append(c["cycle"])
    churn = {tid: cy for tid, cy in exec_count.items() if len(cy) > 1}
    for tid, cycles in sorted(churn.items()):
        tk = tasks.get(tid, {})
        print(f"T{tid:>3} R={tk.get('reward', 0):>5.1f} 出动周期={cycles} "
              f"废飞周期={waste_at.get(tid, [])} 终态={tk.get('status')} "
              f"latest={tk.get('latest', 0):.0f}")

    print()
    print("=" * 72)
    print("Q4. 选择阶段丢弃证据（T_t > T_sel 的周期：谁被丢）")
    print("=" * 72)
    for c in cyc:
        n_in = c["pool_counts"].get("n_total", 0)
        sel = c.get("selection", {})
        n_sel = sel.get("n_selected")
        if n_sel is None or n_sel >= n_in:
            continue
        # 被丢弃 = 在池但未执行也未滞留(即未选中)
        dropped = [tid for tid in c["deferred_ids"]]
        print(f"  周期{c['cycle']:>2}: T_t={n_in} T_sel={n_sel} "
              f"未选中={sorted(dropped)}")
        for tid in dropped:
            tk = tasks.get(int(tid), {})
            print(f"      T{tid}: R={tk.get('reward', 0):.1f} "
                  f"到达={float(tk.get('arrival_time', 0)):.0f}s")


if __name__ == "__main__":
    main()
