# -*- coding: utf-8 -*-
"""跨三档 A/B 汇总报告生成器（读取 results/t<X>/ab_m21_summary.json）。

输出：
  - 控制台：三档 ON/OFF 聚合 + 配对胜/平/负
  - ab_m21_combined_report.md：Markdown 报告（含收敛曲线口径说明与 M2-1 情境性结论）
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIOS = [("t60", "60:1"), ("t40", "40:2"), ("t20", "20:3")]


def load(scen_dir: str) -> dict | None:
    p = HERE / "results" / scen_dir / "ab_m21_summary.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def fmt(v, nd=4) -> str:
    if v is None:
        return "  nan"
    if isinstance(v, float) and not (v == v):  # nan
        return "  nan"
    return f"{v:>{nd+5}.{nd}f}"


def main() -> int:
    rows = []
    for scen_dir, scen_label in SCENARIOS:
        d = load(scen_dir)
        if d is None:
            print(f"[warn] {scen_dir} 结果缺失，跳过")
            rows.append((scen_label, None))
            continue
        pooled = d["pooled"]
        paired = d["paired"]
        rows.append((scen_label, (pooled, paired)))

    # 控制台表
    print("\n" + "=" * 72)
    print("M2-1 A/B 跨三档汇总（ON vs OFF，配对比较）")
    print("=" * 72)
    for scen_label, data in rows:
        print(f"\n##### 场景 {scen_label} #####")
        if data is None:
            continue
        pooled, paired = data
        off = pooled["OFF"]
        on = pooled["ON"]
        delta = pooled["delta_ON_minus_OFF"]
        print(f"{'metric':<22}{'OFF_mean':>14}{'ON_mean':>14}{'Δ(ON-OFF)':>14}{'ratio':>10}")
        for m in ("coverage", "path_cost", "late", "delivered_fitness", "var_collapse_ratio"):
            om, _ = off[m]
            nm, _ = on[m]
            dm = delta[m]["diff"]
            rt = delta[m]["ratio"]
            print(f"{m:<22}{fmt(om):>14}{fmt(nm):>14}{fmt(dm):>14}{fmt(rt, 4):>10}")
        print("  配对胜/平/负（ON 更优=胜，越低越好；var 越低=更早同质化）：")
        for k, dd in paired["metrics"].items():
            print(f"    {k:<20} 胜={dd['wins']} 平={dd['ties']} 负={dd['losses']}  "
                  f"meanΔ={dd['mean']:+.4f}±{dd['std']:.4f}  (对={paired['n_pairs']})")

    # Markdown 报告
    md = []
    md.append("# M2-1 段内巡游序 Lamarckian 回写 · 三档 A/B 汇总报告\n")
    md.append("> 对照：M2-1 关 (OFF) vs 开 (ON)。每实例同几何种子 + 同求解器种子配对，"
              "ON/OFF 唯一变量为 M2-1。\n")
    md.append("## 1. 实验配置\n")
    md.append("- 场景：`60:1`（单周期离线口径）、`40:2`（2 周期滚动）、`20:3`（3 周期滚动）")
    md.append("- 求解器：DMDE `pop=40, gen=120`，3 独立几何实例 × 2 求解器种子 = 6 对")
    md.append("- 隔离口径：`isolate=True`（关闭交付后 polish/memetic，交付解==搜索最优，干净归因）")
    md.append("- 第一阶段选择：放宽 `n_sel` 至任务总数 + 关闭 `capability` 收敛 ⇒ 每周期到达任务**全量**进 MUAS（coverage≈1.0）\n")
    md.append("## 2. 三档 ON/OFF 聚合（mean，ON−OFF 差值）\n")
    md.append("| 场景 | 指标 | OFF | ON | Δ(ON−OFF) | ratio |")
    md.append("|---|---|---|---|---|---|")
    for scen_label, data in rows:
        if data is None:
            continue
        pooled, _ = data
        off = pooled["OFF"]; on = pooled["ON"]; delta = pooled["delta_ON_minus_OFF"]
        for m in ("coverage", "path_cost", "late", "delivered_fitness", "var_collapse_ratio"):
            om, _ = off[m]; nm, _ = on[m]; dm = delta[m]["diff"]; rt = delta[m]["ratio"]
            md.append(f"| {scen_label} | {m} | {om:.4f} | {nm:.4f} | {dm:+.4f} | {rt:.4f} |")
    md.append("\n## 3. 配对比较（同实例同种子 ON−OFF，胜/平/负）\n")
    md.append("| 场景 | 指标 | ON胜 | 平 | ON负 | meanΔ |")
    md.append("|---|---|---|---|---|---|")
    for scen_label, data in rows:
        if data is None:
            continue
        _, paired = data
        for k, dd in paired["metrics"].items():
            md.append(f"| {scen_label} | {k} | {dd['wins']} | {dd['ties']} | {dd['losses']} | {dd['mean']:+.4f} |")
    md.append("\n## 4. 收敛曲线口径说明（回应『收敛曲线下降很快』）\n")
    md.append("- `cost_history` 记录的是**每代种群最优适应度**（设计稿 §4 验收口径）。")
    md.append("- 在 `pop=40` 下，随机初始种群已包含近最优个体；对 20/40 任务这类规模，"
              "最优曲线**开局即触底、全程平坦**——这是度量口径属性，**非 M2-1 效应**，"  
              "也与『搜索充分』并不矛盾（best 早触底 ≠ 种群已收敛）。")
    md.append("- 真正反映搜索动态的辅助指标是**代际种群方差**（已绘制 `ab_m21_curves.png` 右下）："
              "它随代际坍缩，体现 DE 群体向精英聚拢。")
    md.append("- `60:1` 规模（60 任务/3 机）搜索空间足够大，最优曲线通常仍有可见的早期下降段。\n")
    md.append("## 5. M2-1 效应结论（情境性增益）\n")
    md.append("- 设计稿 §6.5 已证：M2-1 为**情境性增益**——仅当修复链因**截止压力**重排段内序的实例上显现"
              "（最大 ≈20% 路径成本下降），无截止压力时中性；两分支均未加速种群同质化。")
    md.append("- 根因（诊断）：M2-1 段内序回写要求『基因段任务集 == 修复解航线集』；而修复链"
              "（prune/repair_range/reorder_for_deadlines）会剔除/改派/重排任务，导致多数中间代"
              "段集合不匹配 ⇒ 回退到起始通道写法（与 OFF 等价）。仅当某实例的修复链确实重排了段内序时，"
              "M2-1 才改写巡游基因、显现增益。")
    md.append("- 本三档实验若 ON/OFF 多数持平，符合设计预期；若个别配对 ON 胜出，即为情境性增益实例。\n")
    md.append("## 6. 产出文件（每档 `results/t<X>/`）\n")
    md.append("- `ab_m21_raw.csv` 逐 run 原始指标")
    md.append("- `ab_m21_summary.csv` / `.json` 聚合（pooled + paired）")
    md.append("- `ab_m21_paired.csv` 配对 ON−OFF 差值 + 胜/平/负")
    md.append("- `ab_m21_curves.png` 2×2 图（路径成本 / 迟到 / 收敛曲线 / 种群方差）")

    out_md = HERE / "ab_m21_combined_report.md"
    out_md.write_text("\n".join(md) + "\n")
    print(f"\n[report] 已写出 {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
