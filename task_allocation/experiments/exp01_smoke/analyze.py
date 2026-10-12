# -*- coding: utf-8 -*-
"""exp01 结果分析：可行性、违约类型、终点机巢分布、完成率与闸门拦截量。

用法（在本目录或仓库根执行均可）：

    python analyze.py results/metrics.json
    python analyze.py results/metrics_tw_off.json   # 时间窗关闭对照
    python analyze.py results/metrics_tw_on.json    # 时间窗开启对照
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def main(path: str) -> None:
    d = json.load(open(path, encoding="utf-8"))
    seeds = d["seeds"]
    n_nests = d["problem"]["n_nests"]

    feas_t = feas_f = 0
    tw = collections.Counter()
    other = collections.Counter()
    nest_dist = collections.Counter()
    n_infeasible = 0
    infeas_tags = collections.Counter()
    per_cycle_div = []

    for s in seeds:
        for c in d["per_seed"][str(s)]["cycles"]:
            m = c["muas"]
            if m.get("feasible"):
                feas_t += 1
            else:
                feas_f += 1
            for v in m.get("violations", []):
                (tw if v.startswith("tw_") else other)[v] += 1
            # 方案 A：内层最小费用流实际选定的终点机巢（diagnostics.end_nests）
            flown = [n for n in m.get("end_nests", {}).values() if n is not None]
            for n in flown:
                nest_dist[n] += 1
            per_cycle_div.append(f"{len(set(flown))}/{len(flown)}")
            n_infeasible += len(c.get("infeasible_ids", []))
            for tags in c.get("infeasible_tags", {}).values():
                for t in tags:
                    infeas_tags[t.rsplit("_", 1)[0]] += 1

    total = feas_t + feas_f
    print("=" * 68)
    print(f"metrics: {path}")
    print(f"cycle 解总数 = {total}   (seeds={seeds}, n_cycles={d['n_cycles']})")
    print("-" * 68)
    print(f"可行解: {feas_t}/{total}   不可行: {feas_f}/{total}" +
          f"   (cycle_feasible_rate ≈ {feas_t/total:.3f})" if total else "")
    print("-" * 68)
    print("违约类型统计：")
    print(f"  时间窗 tw_task_*  : {sum(tw.values()):3d} 次 / {len(tw)} 个任务")
    print(f"  其它             : {sum(other.values()):3d} 次  {dict(other)}")
    print(f"  闸门拦截（未交付）: {n_infeasible} 个任务次  {dict(infeas_tags)}")
    print("-" * 68)
    tot = sum(nest_dist.values())
    if tot:
        print(f"终点机巢分布（{tot} 个飞行航次，共 {n_nests} 个机巢）：")
        for n in sorted(nest_dist):
            print(f"  nest {n}: {nest_dist[n]:3d}  ({100*nest_dist[n]/tot:5.1f}%)")
        print(f"  使用到的不同机巢数 = {len(nest_dist)} / {n_nests}")
    print(f"  单周期终点多样性   = {per_cycle_div}")
    print("-" * 68)
    ss = d.get("seed_summary", {})
    if ss:
        print("跨 seed 汇总（mean ± std）：")
        for k in (
            "n_arrived",
            "n_completed",
            "completion_rate",
            "cycle_feasible_rate",
            "n_infeasible_delivery",
            "total_reward",
            "total_cost",
            "avg_wait",
            "n_remaining_pool",
            "n_deferred_last",
        ):
            v = ss.get(k)
            if v:
                print(f"  {k:22s} {v['mean']:.4f} ± {v['std']:.4f}")
    print("=" * 68)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         "task_allocation/experiments/exp01_smoke/results/metrics.json")
