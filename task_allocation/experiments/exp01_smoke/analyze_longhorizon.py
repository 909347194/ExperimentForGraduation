# -*- coding: utf-8 -*-
"""analyze_longhorizon.py — 长程协议结果的深度分析（只读 json，不重跑）。

分析维度：
  A. 记账恒等式验证（executions = completed + waste；arrived = completed + unreachable + eliminated + remain）
  B. 废飞（违约交付）分析：任务/周期/违约标签/浪费航程/返送情况
  C. 漏斗分析：T_t → T_sel → T_exec → 交付/废飞
  D. 等待时间分析：分布/到达批次/与废飞淘汰的关系
  E. 淘汰分析：任务/时机/等待时长
  F. 池深动态：backlog 演化、到达 vs 服务速率
  G. 求解规模：solve_seconds vs 池内任务数
  H. 机队利用：每周期出动架次、闲置
  I. 与旧 3 周期口径周期1 的一致性交叉验证（硬编码历史值）

用法：python analyze_longhorizon.py [results_dir]
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "results"


def load(rdir: Path) -> tuple[dict, dict]:
    m = json.loads((rdir / "metrics.json").read_text(encoding="utf-8"))
    s = json.loads((rdir / "solution.json").read_text(encoding="utf-8"))
    return m, s


def sec(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> None:
    rdir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    m, sol = load(rdir)
    cyc = m["cycles"]
    s = m["summary"]
    n_cyc = m["n_cycles"]

    # ── A. 记账恒等式 ─────────────────────────────────────────────
    sec("A. 记账恒等式验证")
    tot_exec = sum(c["n_executed"] for c in cyc)
    tot_done = sum(c["n_completed"] for c in cyc)
    tot_waste = sum(c["n_infeasible"] for c in cyc)
    tot_elim = sum(len(c["expired_ids"]) for c in cyc)
    tot_unreach = sum(len(c.get("unreachable_ids", [])) for c in cyc)
    arrived = s["n_arrived"]
    remain = s["n_remaining_pool"]
    print(f"出动总数        : {tot_exec}")
    print(f"交付总数        : {tot_done}")
    print(f"废飞总数        : {tot_waste}")
    print(f"不可达(过于偏远): {tot_unreach}")
    print(f"恒等式1 exec=done+waste : {tot_exec} = {tot_done}+{tot_waste} -> "
          f"{'OK' if tot_exec == tot_done + tot_waste else 'FAIL'}")
    print(f"恒等式2 arrived=done+unreach+elim+remain : {arrived} = {tot_done}+{tot_unreach}+{tot_elim}+{remain} -> "
          f"{'OK' if arrived == tot_done + tot_unreach + tot_elim + remain else 'FAIL'}")

    # ── B. 废飞分析 ──────────────────────────────────────────────
    sec("B. 废飞（违约交付）分析")
    waste_tasks: list[int] = []
    waste_tags: Counter = Counter()
    waste_by_cycle: Counter = Counter()
    for c in cyc:
        for tid in c["infeasible_ids"]:
            waste_tasks.append(int(tid))
            waste_by_cycle[c["cycle"]] += 1
            for tag in c["infeasible_tags"].get(str(tid), []):
                waste_tags[tag.split("_uav_")[0].split("_task_")[0]
                           .rstrip("0123456789")] += 1
    print(f"废飞次数        : {len(waste_tasks)}（涉及 {len(set(waste_tasks))} 个不同任务）")
    print(f"每任务废飞次数  : {dict(Counter(waste_tasks))}")
    print(f"按周期分布      : {dict(sorted(waste_by_cycle.items()))}")
    print(f"违约标签分布    : {dict(waste_tags.most_common())}")

    # 每任务出动/废飞/最终状态
    exec_count: Counter = Counter()
    for c in cyc:
        exec_count.update(int(i) for i in c["executed_ids"])
    task_status = {int(t["id"]): t["status"] for t in sol["tasks"]}
    multi = {tid: n for tid, n in exec_count.items() if n > 1}
    print(f"重复出动任务    : {multi}")
    rescued = [tid for tid in set(waste_tasks)
               if task_status.get(tid) in ("done", "completed")]
    lost = [tid for tid in set(waste_tasks)
            if task_status.get(tid) not in ("done", "completed")]
    print(f"废飞后最终完成  : {sorted(rescued)}")
    print(f"废飞后仍未完成  : {sorted(lost)}（状态 "
          f"{ {tid: task_status.get(tid) for tid in lost} }）")

    # 浪费航程：含废飞任务的航次全额计入（保守口径）
    sol_cycles = sol.get("cycles", [])
    waste_cost = 0.0
    clean_cost = 0.0
    for c, sc in zip(cyc, sol_cycles):
        bad = {int(i) for i in c["infeasible_ids"]}
        tours = sc.get("tours", [])
        for t in tours:
            tids = {int(i) for i in t.get("task_ids", [])}
            if not tids:
                continue
            if tids & bad:
                waste_cost += float(t.get("est_cost", 0.0))
            else:
                clean_cost += float(t.get("est_cost", 0.0))
    print(f"含废飞航次航程  : {waste_cost/1000:.1f} km（保守：整航次计入）")
    print(f"干净航次航程    : {clean_cost/1000:.1f} km")
    print(f"废飞航程占比    : {waste_cost/(waste_cost+clean_cost)*100:.1f}%")

    # ── C. 漏斗 ──────────────────────────────────────────────────
    sec("C. 逐周期漏斗（T_t → T_sel → T_exec → 交付/废飞/滞留）")
    print(f"{'cyc':>4}{'T_t':>5}{'sel':>5}{'exec':>6}{'done':>6}{'waste':>6}"
          f"{'remain':>8}{'F1':>8}{'F2(km)':>9}{'solve_s':>9}")
    for c in cyc:
        sel = c.get("selection", {})
        n_sel = sel.get("n_selected", sel.get("n_tasks_sel", None))
        if n_sel is None:
            n_sel = "-"
        print(f"{c['cycle']:>4}{c['pool_counts'].get('n_total', 0):>5}"
              f"{str(n_sel):>5}{c['n_executed']:>6}{c['n_completed']:>6}"
              f"{c['n_infeasible']:>6}{c['pool_counts'].get('n_remain', 0):>8}"
              f"{c['muas'].get('f1_reward', 0):>8.1f}"
              f"{c['muas'].get('f2_cost', 0)/1000:>9.1f}"
              f"{c['muas'].get('solve_seconds', 0):>9.1f}")

    # ── D. 等待时间 ──────────────────────────────────────────────
    sec("D. 等待时间分析")
    waits = []
    wait_by_arrival: dict[int, list[float]] = defaultdict(list)
    for t in sol["tasks"]:
        if t.get("status") in ("done", "completed") and t.get("completed_at") is not None:
            w = float(t["completed_at"]) - float(t["arrival_time"])
            waits.append(w)
            arr_cyc = int(float(t["arrival_time"]) // 600.0) + 1
            wait_by_arrival[arr_cyc].append(w)
    if waits:
        print(f"完成任务等待    : mean={statistics.mean(waits):.0f}s "
              f"median={statistics.median(waits):.0f}s "
              f"max={max(waits):.0f}s min={min(waits):.0f}s")
        print(f"按到达周期均值  : "
              f"{ {k: round(statistics.mean(v)) for k, v in sorted(wait_by_arrival.items())} }")

    # ── E. 淘汰 ──────────────────────────────────────────────────
    sec("E. 超期淘汰分析")
    elim_rows = []
    for c in cyc:
        for tid in c["expired_ids"]:
            elim_rows.append((c["cycle"], int(tid)))
    print(f"淘汰 {len(elim_rows)} 个：{elim_rows}")
    for tid in [t for _, t in elim_rows]:
        t = next((x for x in sol["tasks"] if int(x["id"]) == tid), None)
        if t:
            print(f"  T{tid}: 到达={float(t['arrival_time']):.0f}s "
                  f"latest={t['latest']:.0f}s 等待预算="
                  f"{float(t['latest'])-float(t['arrival_time']):.0f}s "
                  f"reward={t['reward']:.1f} 出动={exec_count.get(tid,0)}次 "
                  f"废飞={waste_tasks.count(tid)}次")

    # ── F. 池深动态 ──────────────────────────────────────────────
    sec("F. 池深动态（backlog）")
    print(f"{'cyc':>4}{'new':>5}{'remain_end':>11}{'executing':>11}{'expired':>9}")
    for c in cyc:
        pc = c["pool_counts"]
        print(f"{c['cycle']:>4}{pc.get('n_new', 0):>5}{pc.get('n_remain', 0):>11}"
              f"{pc.get('n_executing', 0):>11}{pc.get('n_expired', 0):>9}")
    arr_rates = [c["pool_counts"].get("n_new", 0) for c in cyc[1:]]
    print(f"到达速率        : mean={statistics.mean(arr_rates):.2f}/周期 "
          f"(泊松 λ=5 期望)")
    print(f"服务速率(交付)  : mean={tot_done/n_cyc:.2f}/周期")
    print(f"出动速率        : mean={tot_exec/n_cyc:.2f}/周期")

    # ── G. 求解规模 ──────────────────────────────────────────────
    sec("G. 求解开销 vs 问题规模")
    rows = [(c["pool_counts"].get("n_total", 0), c["muas"].get("solve_seconds", 0),
             c["muas"].get("n_selected", 0)) for c in cyc]
    big = [r for r in rows if r[1] > 60]
    small = [r for r in rows if r[1] <= 60]
    if big:
        print(f"慢周期(>60s)    : n={len(big)} 平均池深="
              f"{statistics.mean(r[0] for r in big):.1f} "
              f"平均选中={statistics.mean(r[2] for r in big):.1f}")
    if small:
        print(f"快周期(<=60s)   : n={len(small)} 平均池深="
              f"{statistics.mean(r[0] for r in small):.1f} "
              f"平均选中={statistics.mean(r[2] for r in small):.1f}")
    print(f"总求解          : {s['solve_time_ms']/1000:.0f}s "
          f"({s['solve_time_ms']/1000/n_cyc:.1f}s/周期)")

    # ── H. 机队利用 ──────────────────────────────────────────────
    sec("H. 机队利用")
    util = []
    for sc in sol_cycles:
        tours = [t for t in sc.get("tours", []) if t.get("task_ids")]
        util.append(len(tours))
    print(f"每周期出动架次  : {util}")
    print(f"平均出动        : {statistics.mean(util):.2f}/10 架"
          f"（闲置 {10-statistics.mean(util):.2f} 架/周期）")

    # ── I. 一致性交叉验证 ────────────────────────────────────────
    sec("I. 与旧 3 周期口径（500代, seed42）周期1 交叉验证")
    c1 = cyc[0]
    print(f"本run 周期1     : F1={c1['muas'].get('f1_reward'):.4f} "
          f"F2={c1['muas'].get('f2_cost'):.2f} "
          f"废飞={c1['n_infeasible']} 执行={c1['n_executed']} 交付={c1['n_completed']}")
    print("历史记录(记忆)  : F1=60.4962 F2=381007.85 废飞=4([6,11,0,7]) "
          "执行=12 交付=8")
    ok = (abs(c1['muas'].get('f1_reward', 0) - 60.4962) < 0.01
          and abs(c1['muas'].get('f2_cost', 0) - 381007.85) < 1.0
          and c1['n_infeasible'] == 4 and c1['n_executed'] == 12
          and c1['n_completed'] == 8)
    print(f"判定            : {'一致（续跑确定性成立）' if ok else '不一致（需排查）'}")

    # ── 汇总 ─────────────────────────────────────────────────────
    sec("汇总（论文长程口径）")
    print(f"周期数={n_cyc} 到达={arrived} 完成={tot_done} "
          f"窗口完成率={s['completion_rate']*100:.1f}%")
    print(f"吞吐={s['throughput_per_cycle']:.3f}/周期 "
          f"正确执行率={s['correct_exec_rate']*100:.1f}% "
          f"废飞={tot_waste} 淘汰={tot_elim} 滞留={remain}")
    print(f"平均等待={s['avg_wait']:.0f}s 总航程={s['total_cost']/1000:.0f}km "
          f"周期可行率={s['cycle_feasible_rate']*100:.1f}%")


if __name__ == "__main__":
    main()
