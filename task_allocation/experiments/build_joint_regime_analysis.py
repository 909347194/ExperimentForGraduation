#!/usr/bin/env python
"""exp06 联合域产能模型分析：完成数 = Σ min(到达率, 选择名额) 的拟合与偏离。

回答：积压域完成率下降是「产能饿死」（结构性名额上限）还是「搜索/权重失效」。
口径：
  - 每周期预测可完成数 = min(n_new_t, n_sel_t)（到达 vs 选择名额，取小）
  - 实际完成数来自 cycles[].n_completed
  - 偏离 = 实际 − 预测（负偏离 = 搜索在该周期的损失）
  - 淘汰/不可达/残留在恒等式 arrived = done + unreach + elim + remain 下分账
输出：stdout 表 + experiments/JOINT_REGIME_CAPACITY_ANALYSIS.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "exp06_joint_regime" / "results"


def load_cell(tag_dir: Path) -> dict | None:
    p = tag_dir / "metrics.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    if "per_seed" in d:
        m = next(iter(d["per_seed"].values()))
    else:
        m = d
    s = m.get("summary", m)
    if "total_path_m" not in s and "total_cost" in s:
        s = dict(s)
        s["total_path_m"] = s["total_cost"]
        m = dict(m)
        m["summary"] = s
    return m


def capacity_model(m: dict) -> dict:
    """逐周期 min(池内, 名额) 产能模型 vs 实际。

    pool_in = cycles[].pool_counts.n_total（选择层看到的池规模，含携带+新到达）。
    预测可完成 = min(pool_in, n_sel)：池小于名额时供给约束，池大于名额时名额约束。
    """
    cycles = m.get("cycles") or []
    pred = act = 0
    quota_tot = 0
    pool_max = 0
    forced_max = 0
    per_cycle = []
    for c in cycles:
        pc = c.get("pool_counts") or {}
        sel = c.get("selection") or {}
        pool_in = int(pc.get("n_total", 0) or 0)
        n_sel = sel.get("capability_n_sel")
        n_sel = int(n_sel) if n_sel is not None else 0
        done = int(c.get("n_completed", 0) or 0)
        forced = int(sel.get("n_forced_in", 0) or 0)
        quota_tot += n_sel
        pool_max = max(pool_max, pool_in)
        forced_max = max(forced_max, forced)
        p = min(pool_in, n_sel)
        pred += p
        act += done
        per_cycle.append({"cycle": c["cycle"], "pool_in": pool_in, "n_sel": n_sel,
                          "pred": p, "done": done, "forced": forced})
    return {"pred": pred, "act": act, "quota_tot": quota_tot, "pool_max": pool_max,
            "forced_max": forced_max, "per_cycle": per_cycle}


def paired_divergence(m_lo: dict, m_hi: dict) -> dict:
    """同 seed 同到达流下两 λ 臂的逐周期完成数 diff：区分系统信号与混沌振荡。"""
    c_lo = m_lo.get("cycles") or []
    c_hi = m_hi.get("cycles") or []
    deltas = [(cb.get("n_completed", 0) or 0) - (ca.get("n_completed", 0) or 0)
              for ca, cb in zip(c_lo, c_hi)]
    n_pos = sum(1 for d in deltas if d > 0)
    n_neg = sum(1 for d in deltas if d < 0)
    net = sum(deltas)
    systematic = (n_pos == 0) != (n_neg == 0)  # 全同号才算系统信号
    return {"deltas": deltas, "n_pos": n_pos, "n_neg": n_neg, "net": net,
            "n_div": n_pos + n_neg, "systematic": systematic}


def main() -> None:
    if not RESULTS.is_dir():
        print("no results dir"); sys.exit(1)
    lines: list[str] = ["# exp06 联合域产能模型分析\n",
                        "预测完成 = Σ_t min(池内_t, 名额_t)；实际完成来自 metrics。",
                        "偏离为负 = 名额内搜索/期限损失；名额跑满（完成/名额→1）而完成率 <1 = 产能饿死。\n",
                        "| 格 | 到达 | 名额Σ | 预测完成 | 实际完成 | 偏离 | 完成/名额 | 完成率 | 淘汰 | 不可达 | 残留 | 池峰 | 强制峰 |",
                        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    print(f"{'格':<26} {'到达':>5} {'名额Σ':>6} {'预测':>5} {'实际':>5} {'偏离':>5} "
          f"{'完成/名额':>9} {'完成率':>7} {'淘汰':>4} {'不可达':>5} {'残留':>4} {'池峰':>4} {'强制峰':>5}")
    for tag_dir in sorted(RESULTS.iterdir()):
        if not tag_dir.is_dir():
            continue
        m = load_cell(tag_dir)
        if m is None:
            continue
        s = m["summary"]
        cm = capacity_model(m)
        dev = cm["act"] - cm["pred"]
        fill = cm["act"] / cm["quota_tot"] if cm["quota_tot"] else float("nan")
        row = (f"| {tag_dir.name} | {s['n_arrived']} | {cm['quota_tot']} | {cm['pred']} "
               f"| {cm['act']} | {dev:+d} | {fill:.3f} | {s['completion_rate']:.3f} "
               f"| {s['n_eliminated']} | {s['n_unreachable']} | {s['n_remaining_pool']} "
               f"| {cm['pool_max']} | {cm['forced_max']} |")
        lines.append(row)
        print(f"{tag_dir.name:<26} {s['n_arrived']:>5} {cm['quota_tot']:>6} {cm['pred']:>5} "
              f"{cm['act']:>5} {dev:>+5d} {fill:>9.3f} {s['completion_rate']:>7.3f} "
              f"{s['n_eliminated']:>4} {s['n_unreachable']:>5} {s['n_remaining_pool']:>4} "
              f"{cm['pool_max']:>4} {cm['forced_max']:>5}")
    out = HERE / "JOINT_REGIME_CAPACITY_ANALYSIS.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n→ {out}")

    # ── 配对逐周期 diff：系统信号 vs 混沌振荡 ──
    lines2: list[str] = ["\n## 配对逐周期 diff（同 seed 同到达流，仅 λ 不同）\n",
                         "判定：逐周期 Δ 全同号 = 系统信号；正负振荡 = 混沌噪声（单 seed 不可分辨）。\n",
                         "| 到达λ | Δ序列 | 正周期 | 负周期 | 净Δ | 判定 |",
                         "|---:|---|---:|---:|---:|---|"]
    print(f"\n{'到达λ':>6} {'Δ序列':<40} {'正':>3} {'负':>3} {'净':>4} 判定")
    cells: dict[float, dict[float, dict]] = {}
    for tag_dir in sorted(RESULTS.iterdir()):
        if not tag_dir.is_dir():
            continue
        m = load_cell(tag_dir)
        if m is None:
            continue
        lam = float(tag_dir.name.split("_arr")[1].split("_")[0])
        lc = float(tag_dir.name.split("_lc")[1].split("_")[0])
        cells.setdefault(lam, {})[lc] = m
    for lam in sorted(cells):
        arms = cells[lam]
        if 1e-4 not in arms or 1e-1 not in arms:
            continue
        dv = paired_divergence(arms[1e-4], arms[1e-1])
        seq = ",".join(f"{d:+d}" for d in dv["deltas"])
        verdict = ("**系统信号**" if dv["systematic"] and dv["net"] != 0
                   else "混沌振荡（噪声级）")
        lines2.append(f"| {lam:g} | {seq} | {dv['n_pos']} | {dv['n_neg']} "
                      f"| {dv['net']:+d} | {verdict} |")
        print(f"{lam:>6} {seq:<40} {dv['n_pos']:>3} {dv['n_neg']:>3} {dv['net']:>+4} {verdict}")
    with out.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines2) + "\n")
    print(f"\n(已追加 → {out})")


if __name__ == "__main__":
    main()
