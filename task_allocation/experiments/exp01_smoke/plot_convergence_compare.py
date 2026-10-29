# -*- coding: utf-8 -*-
"""exp01_smoke gen100 vs gen200 收敛曲线对比（单种子 42）。

数据来源：results_gen{100,200}/solution.json 的 cycles[].cost_history
（DMDE 逐代 best-so-far 适应度，搜索期口径，审计项 I 已验证非增）。

只画真正跑了 DMDE 的周期（cost_history 非空）；周期 3-6 无到达任务、
历史为空，跳过。每个周期子图叠加 gen100（蓝）与 gen200（橙），虚线标
gen=100 边界，并在标题/标注里给出「gen200 在 gen100 之后又挤出的改进量」
（同一 run 内、同一 RNG 序列，才是 apples-to-apples 的「额外代数收益」）。
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt

# 注册系统中文字体（黑体），避免标题/标签出现缺字方框
_CJK = "C:/Windows/Fonts/simhei.ttf"
if Path(_CJK).exists():
    fm.fontManager.addfont(_CJK)
    plt.rcParams["font.family"] = fm.FontProperties(fname=_CJK).get_name()
plt.rcParams["axes.unicode_minus"] = False

HERE = Path(__file__).resolve().parent


def load(g: str) -> dict:
    return json.load(open(HERE / f"results_gen{g}" / "solution.json", encoding="utf-8"))


def hist(cycles, i):
    h = (cycles[i].get("cost_history") or []) if i < len(cycles) else []
    return [float(x) for x in h]


sol100, sol200 = load("100"), load("200")
cyc100, cyc200 = sol100["cycles"], sol200["cycles"]
n = min(len(cyc100), len(cyc200))
idxs = [i for i in range(n) if len(hist(cyc100, i)) > 1 and len(hist(cyc200, i)) > 1]

print("cycles with non-empty DMDE history (both runs):", idxs)

fig, axes = plt.subplots(1, len(idxs), figsize=(5.6 * len(idxs), 4.4), squeeze=False)
axes = axes[0]
for ax, i in zip(axes, idxs):
    h100 = hist(cyc100, i)
    h200 = hist(cyc200, i)
    x100 = list(range(len(h100)))
    x200 = list(range(len(h200)))
    ax.plot(x100, h100, color="#1f77b4", lw=2.0,
            label=f"gen100 (final={h100[-1]:.0f})")
    ax.plot(x200, h200, color="#ff7f0e", lw=2.0,
            label=f"gen200 (final={h200[-1]:.0f})")
    ax.axvline(100, color="#555555", ls="--", lw=1.0)
    # 同一 run 内、gen100 处 vs 末代的「额外代数收益」
    v200_at100 = h200[100] if len(h200) > 100 else h200[-1]
    gain_after = v200_at100 - h200[-1]  # >0 表示 gen100 之后继续下降
    ax.annotate(f"gen100 之后\n再降 {gain_after:+.0f}",
                xy=(len(h200) - 1, h200[-1]), xytext=(len(h200) * 0.45, h200[-1]),
                fontsize=8, color="#b35900",
                arrowprops=dict(arrowstyle="->", color="#b35900", lw=0.8))
    ax.set_title(f"周期 {i}（DMDE 代数 100 vs 200）", fontsize=10)
    ax.set_xlabel("DMDE 代数 (generation)")
    ax.set_ylabel("best-so-far 适应度")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(alpha=0.3)

fig.suptitle("exp01_smoke seed=42 — DMDE 收敛曲线对比 (gen100 vs gen200)",
             fontsize=13)
fig.tight_layout(rect=[0, 0, 1, 0.95])
out = HERE / "convergence_compare_gen100_200.png"
fig.savefig(out, dpi=150)
print("saved:", out)
