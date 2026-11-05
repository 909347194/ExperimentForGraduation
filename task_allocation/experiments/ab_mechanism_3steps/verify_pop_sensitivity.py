# -*- coding: utf-8 -*-
"""复现性核查：进化效应是否稳定，还是取决于初始种群（pop / seed）。

背景：上一轮 ab_optimization_attribution 在 60:1/d0（pop8/gen25/3seed）
报告进化占比 68.7%（C−A = 460.29）。本脚本用同一套 common.run_rolling
做三组交叉核查，结果落 step0_sensitivity.csv 供 synthesize.py 读取：

  A. 精确复现上一轮参数（pop8/gen25/3seed 均值）
  B. pop10/gen25/3seed（本轮 2×2 的配置）
  C. 单 seed=11、pop ∈ {8,10,12}（初始种群敏感性的直接展示）

输出列：case / pop / seeds / no_evo / evo / evo_effect / per_seed_no_evo / per_seed_evo
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[3]))

import common as C  # noqa: E402


def run(pop, seeds, gens=25, deadline_s=0.0, n_targets=60, n_cycles=1):
    params = dict(pop_size=pop, max_generations=gens, n_cycles=n_cycles,
                  inst_base=5000, n_uavs=3, area=1000.0,
                  deadline_s=deadline_s)
    tasks, uavs, nests = C.make_instance(5000, 3, n_targets, 1000.0,
                                          deadline_s=deadline_s)
    per = {0: [], 1: []}
    for evo in (0, 1):
        for sd in seeds:
            r = C.run_rolling(
                tasks, uavs, nests, params=params,
                arm_cfg=dict(_label=("evo" if evo else "repair"),
                             refine=False,
                             gens_override=(None if evo else 0),
                             seed=sd, instance=0),
            )
            per[evo].append(round(r["delivered_fitness"], 2))
    m0 = C.mean_std(per[0])[0]
    m1 = C.mean_std(per[1])[0]
    return m0, m1, per[0], per[1]


def main():
    rows = []
    cases = [
        ("A_replay_prev", 8, [11, 22, 33]),
        ("B_this_2x2", 10, [11, 22, 33]),
        ("C_single_s11_p8", 8, [11]),
        ("C_single_s11_p10", 10, [11]),
        ("C_single_s11_p12", 12, [11]),
    ]
    for case, pop, seeds in cases:
        m0, m1, p0, p1 = run(pop, seeds)
        rows.append(dict(
            case=case, pop=pop, seeds=",".join(map(str, seeds)),
            no_evo=round(m0, 2), evo=round(m1, 2),
            evo_effect=round(m0 - m1, 2),
            per_seed_no_evo="|".join(map(str, p0)),
            per_seed_evo="|".join(map(str, p1)),
        ))
        print(f"[{case}] pop={pop} seeds={seeds}: "
              f"no_evo={m0:.2f} evo={m1:.2f} effect={m0-m1:.2f}",
              flush=True)
    C.write_csv(HERE / "results" / "step0_sensitivity.csv", rows)
    print("写出 results/step0_sensitivity.csv", flush=True)


if __name__ == "__main__":
    main()
