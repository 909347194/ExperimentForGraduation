"""画 gen100 vs gen200 在 seed 42/43/44 上的稳健性对比图。

三面板：
  左：逐种子 按时率 分组柱（gen100 / gen200）
  中：逐种子 按时率增量 Δ（百分点）
  右：逐种子 航程/完成 变化率（%）
产出：seed_robustness_gen100_200.png
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# 注册中文字体（黑体），否则中文变方框
for cand in (Path("C:/Windows/Fonts/simhei.ttf"), Path("C:/Windows/Fonts/msyh.ttc")):
    if cand.exists():
        font_manager.fontManager.addfont(str(cand))
        plt.rcParams["font.sans-serif"] = [cand.stem] + plt.rcParams["font.sans-serif"]
        break
plt.rcParams["axes.unicode_minus"] = False

import json  # noqa: E402

HERE = Path(__file__).resolve().parent
SEEDS = [42, 43, 44]


def load(seed, gen):
    d = HERE / (f"results_gen{gen}" if seed == 42 else f"results_gen{gen}_s{seed}")
    return json.load(open(d / "metrics.json", encoding="utf-8")).get("summary", {})


def main():
    ot100 = [load(s, 100)["on_time_rate_final"] for s in SEEDS]
    ot200 = [load(s, 200)["on_time_rate_final"] for s in SEEDS]
    pc100 = [load(s, 100)["path_per_completion_m"] for s in SEEDS]
    pc200 = [load(s, 200)["path_per_completion_m"] for s in SEEDS]

    d_ot = [(b - a) * 100.0 for a, b in zip(ot100, ot200)]          # 百分点
    d_pc = [(b - a) / a * 100.0 for a, b in zip(pc100, pc200)]      # 百分比

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
    x = range(len(SEEDS))
    labels = [f"seed {s}" for s in SEEDS]
    w = 0.36

    # --- 左：按时率分组柱 ---
    ax = axes[0]
    ax.bar([i - w / 2 for i in x], ot100, w, label="gen100", color="#4C72B0")
    ax.bar([i + w / 2 for i in x], ot200, w, label="gen200", color="#DD8452")
    for i, (a, b) in enumerate(zip(ot100, ot200)):
        ax.text(i - w / 2, a + 0.012, f"{a:.3f}", ha="center", fontsize=8)
        ax.text(i + w / 2, b + 0.012, f"{b:.3f}", ha="center", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylim(0.75, 1.08)
    ax.set_ylabel("按时交付率")
    ax.set_title("逐种子按时率（同种子唯一变量 max_iter）")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    # --- 中：按时率增量（百分点）---
    ax = axes[1]
    colors = ["#55A868" if v > 0.5 else ("#C44E52" if v < -0.5 else "#9E9E9E") for v in d_ot]
    bars = ax.bar(list(x), d_ot, 0.5, color=colors)
    for i, v in enumerate(d_ot):
        ax.text(i, v + (0.35 if v >= 0 else -0.9), f"{v:+.1f}pp", ha="center", fontsize=9)
    ax.axhline(0, color="k", lw=1)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylim(-3, 19)
    ax.set_ylabel("Δ 按时率 (百分点)")
    ax.set_title(f"gen200 减 gen100: 按时率  均值 {sum(d_ot)/len(d_ot):+.2f}pp")
    ax.grid(axis="y", alpha=0.3)

    # --- 右：航程变化率（%）---
    ax = axes[2]
    colors = ["#55A868" if v < -0.05 else ("#C44E52" if v > 0.05 else "#9E9E9E") for v in d_pc]
    ax.bar(list(x), d_pc, 0.5, color=colors)
    for i, v in enumerate(d_pc):
        ax.text(i, v + (0.35 if v >= 0 else -1.0), f"{v:+.2f}%", ha="center", fontsize=9)
    ax.axhline(0, color="k", lw=1)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylim(-16, 4)
    ax.set_ylabel("Δ 航程/完成 (%)")
    ax.set_title(f"gen200 减 gen100: 航程  均值 {sum(d_pc)/len(d_pc):+.2f}%")
    ax.grid(axis="y", alpha=0.3)

    fig.suptitle("exp01 搜索预算 100 vs 200 代：跨种子稳健性（smoke 规模，n=3）", fontsize=12)
    fig.tight_layout()
    out = HERE / "seed_robustness_gen100_200.png"
    fig.savefig(out, dpi=150)
    print("已生成:", out)


if __name__ == "__main__":
    main()
