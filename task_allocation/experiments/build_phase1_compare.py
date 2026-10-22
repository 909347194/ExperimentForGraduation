"""Phase 1 对比分析：λ=1e-4（生产默认）vs λ=1e-1（候选工作点）在同域逐 seed 对比。

- exp05：results/M0（λ=1e-4，seeds 42-46）vs results_lam1e-1_full（λ=1e-1，同 seeds）
- exp01：results/（λ=1e-4，seeds 42-44）vs results_lam1e-1（λ=1e-1，同 seeds）

判定口径（用户 2026-10-22 计划）：
  升级条件 = 完成率/废飞/淘汰/不可达 不劣 且 成本（航程、km/完成）下降；
  若任一项劣化 ⇒ λ=1e-1 不可作生产默认，按「完成优先 + 较高 λ」回调。

用法：.venv/Scripts/python.exe build_phase1_compare.py [--out PATH]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean, stdev

HERE = Path(__file__).resolve().parent
EXP05 = HERE / "exp05_baselines"
EXP01 = HERE / "exp01_smoke"

METRICS = [
    ("completion_rate", "完成率", "{:.4f}"),
    ("effective_completion_rate", "有效完成率", "{:.4f}"),
    ("reward_yield", "奖励产出率", "{:.4f}"),
    ("on_time_rate", "按时率", "{:.4f}"),
    ("n_infeasible_delivery", "废飞", "{:.0f}"),
    ("n_eliminated", "淘汰", "{:.0f}"),
    ("n_unreachable", "不可达", "{:.0f}"),
    ("n_remaining_pool", "期末残留", "{:.0f}"),
    ("n_late", "迟到数", "{:.0f}"),
    ("total_lateness_s", "总迟到 (s)", "{:.0f}"),
    ("avg_lateness_s", "平均迟到 (s)", "{:.0f}"),
    ("total_path_m", "总航程 (m)", "{:.0f}"),
    ("total_reward", "总奖励", "{:.1f}"),
    ("solve_time_ms", "求解 (ms)", "{:.0f}"),
]


def read_exp05(dirname: str, seeds: list[str]) -> dict[str, dict]:
    out = {}
    for sd in sorted((EXP05 / dirname / "M0").glob("seed_*")):
        p = sd / "metrics.json"
        if not p.exists():
            continue
        m = json.loads(p.read_text(encoding="utf-8"))
        if str(m.get("seed")) in seeds:
            out[str(m["seed"])] = m
    return out


def read_exp01(dirname: str, seeds: list[str]) -> dict[str, dict]:
    p = EXP01 / dirname / "metrics.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    if "per_seed" in d:
        out = {s: dict(d["per_seed"][s]["summary"]) for s in seeds if s in d["per_seed"]}
    else:
        out = {str(d.get("seed", seeds[0])): dict(d.get("summary", d))}
    for m in out.values():
        # exp01 summary 用 total_cost，统一映射成 total_path_m
        if "total_path_m" not in m and "total_cost" in m:
            m["total_path_m"] = m["total_cost"]
    return out


def compare(title: str, base: dict, cand: dict) -> list[str]:
    L = [f"## {title}\n"]
    common = sorted(set(base) & set(cand), key=int)
    if not common:
        L.append("_缺候选数据（未跑完）_。\n")
        return L
    L.append(f"共同 seeds：{', '.join(common)}\n")
    L.append("| 指标 | λ=1e-4 | λ=1e-1 | Δ | 方向 |")
    L.append("|---|---:|---:|---:|:--:|")
    bad = []
    mode_note = ""
    for k, lbl, fmt in METRICS:
        b = [base[s].get(k) for s in common]
        c = [cand[s].get(k) for s in common]
        b = [x for x in b if isinstance(x, (int, float))]
        c = [x for x in c if isinstance(x, (int, float))]
        if not b or not c:
            continue
        bm, cm = mean(b), mean(c)
        delta = cm - bm
        # 「越低越好」的指标：航程/迟到/废飞/淘汰/残留/求解
        lower_better = k in ("n_infeasible_delivery", "n_eliminated", "n_unreachable",
                             "n_remaining_pool", "n_late", "total_lateness_s",
                             "avg_lateness_s", "total_path_m", "solve_time_ms")
        if abs(bm) < 1e-12:
            direction = "＝" if abs(delta) < 1e-12 else ("↑劣" if delta > 0 else "↓优")
            if lower_better and delta > 0:
                bad.append(lbl)
        else:
            pct = delta / abs(bm) * 100
            improved = (delta < 0) if lower_better else (delta > 0)
            direction = "↓优" if (lower_better and delta < 0) else \
                        "↑优" if (not lower_better and delta > 0) else \
                        "＝" if abs(pct) < 0.05 else ("↑劣" if delta > 0 else "↓劣")
            if not improved and abs(pct) >= 0.05:
                bad.append(f"{lbl}（{pct:+.1f}%）")
        L.append(f"| {lbl} | {fmt.format(bm)} | {fmt.format(cm)} "
                 f"| {delta:+.4g} | {direction} |")
    # 未完成模式：淘汰（超期永久丢失）vs 残留（池内等待）——合并看，不单判优劣
    b_elim = mean([base[s].get("n_eliminated", 0) for s in common])
    c_elim = mean([cand[s].get("n_eliminated", 0) for s in common])
    b_res = mean([base[s].get("n_remaining_pool", 0) for s in common])
    c_res = mean([cand[s].get("n_remaining_pool", 0) for s in common])
    if (b_elim, b_res) != (c_elim, c_res):
        mode_note = (f"未完成模式互换：淘汰 {b_elim:g}→{c_elim:g}、残留 {b_res:g}→{c_res:g}"
                     "（完成率不变；淘汰=可完成任务永久丢失，残留=池内待完成）")
    # km/完成
    try:
        b_km = mean([base[s]["total_path_m"] / 1000 / base[s]["n_completed"] for s in common])
        c_km = mean([cand[s]["total_path_m"] / 1000 / cand[s]["n_completed"] for s in common])
        L.append(f"| km/完成 | {b_km:.2f} | {c_km:.2f} | {c_km-b_km:+.2f} "
                 f"| {'↓优' if c_km < b_km else '↑劣'} |")
        if c_km >= b_km:
            bad.append("km/完成")
    except Exception:
        pass
    L.append("")
    # 逐 seed 明细
    L.append("| seed | 完成率 1e-4→1e-1 | 航程 km | km/完成 | 按时率 |")
    L.append("|---|---|---:|---:|---:|")
    for s in common:
        b, c = base[s], cand[s]
        L.append(
            f"| {s} | {b.get('completion_rate'):.3f}→{c.get('completion_rate'):.3f} "
            f"| {b['total_path_m']/1000:.0f}→{c['total_path_m']/1000:.0f} "
            f"| {b['total_path_m']/1000/b['n_completed']:.2f}→"
            f"{c['total_path_m']/1000/c['n_completed']:.2f} "
            f"| {b.get('on_time_rate'):.3f}→{c.get('on_time_rate'):.3f} |"
        )
    L.append("")
    hard = [x for x in bad if "(" not in x]
    soft = [x for x in bad if "(" in x]
    # 硬指标只认：完成率/有效完成率/废飞/不可达（物理与承诺口径）；
    # 淘汰/残留只是「未完成」的两种模式，合并呈现，不单独判负。
    hard_real = [x for x in hard if x in ("完成率", "有效完成率", "废飞", "不可达")]
    hard_mode = [x for x in hard if x not in hard_real]
    if mode_note:
        L.append(f"> {mode_note}。\n")
    if not hard_real and not hard_mode:
        verdict = "**全面占优或持平 ⇒ λ=1e-1 可作生产默认候选**"
    elif hard_real:
        verdict = (f"**硬指标劣化（{', '.join(hard_real)}）⇒ λ=1e-1 不可作生产默认**，"
                   "按预定规则回调「完成优先 + 较高 λ」")
    elif hard_mode:
        verdict = (f"仅未完成模式变化（{', '.join(hard_mode)}），完成率/废飞/不可达不变 ⇒ "
                   "**判定为通过**，但需在 exp06 积压域复核该模式变化"
                   "（淘汰→残留意味着「永久丢失」变「池内等待」，方向上更贴合假设 1，"
                   "但残留任务的饥饿机制待查）")
    else:
        verdict = (f"仅软指标波动（{', '.join(soft)}）⇒ 临界，建议补 seed 或取中间档 λ")
    L.append(f"**判定：{verdict}**\n")
    return L


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    L = ["# Phase 1：λ=1e-1 在当前域的验证（vs 生产默认 λ=1e-4）\n"]
    L.append("升级条件：完成率/废飞/淘汰/不可达不劣 且 成本下降。\n")

    e5_base = read_exp05("results", ["42", "43", "44", "45", "46"])
    e5_cand = read_exp05("results_lam1e-1_full", ["42", "43", "44", "45", "46"])
    L += compare("exp05（M0 臂，seeds 42-46）", e5_base, e5_cand)

    e1_base = read_exp01("results", ["42", "43", "44"])
    e1_cand = read_exp01("results_lam1e-1", ["42", "43", "44"])
    L += compare("exp01（fixed-60，seeds 42-44）", e1_base, e1_cand)

    text = "\n".join(L) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"written: {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
