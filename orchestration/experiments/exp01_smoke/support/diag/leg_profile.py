# -*- coding: utf-8 -*-
"""失败航段的净空/高度剖面诊断图（只读结果，不重跑规划）。

回答一个问题：**这一段为什么被判不可行？** 图上给三样东西：
    1. 节点折线上的净空曲线 + 三条阈值线（交付 6.0 / 求解 8.0 / 本次 10.0）；
    2. NLP 真正加了硬约束的那些点（节点 + 每段 8 个插值点）；
    3. 同一段的高度剖面，标出 RRT 候选达到的绕行高度。

用法：``python -m orchestration.experiments.exp01_smoke.support.diag.leg_profile``
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from orchestration.experiments.exp01_smoke.plot import theme as th
from orchestration.experiments.exp01_smoke.support.config import EXP_DIR

RUN_DIR = EXP_DIR / "results" / "seed_0"
OUT = RUN_DIR / "diag" / "D01_leg5_clearance_profile.png"

SOLVE_CLEARANCE = 10.0   # 6.0 交付 + 2.0 SDF 量化容差 + 2.0 本次抬阈
BASE_CLEARANCE = 8.0     # 6.0 + 2.0 量化容差
DELIVER_CLEARANCE = 6.0
SEG_SAMPLES = 8          # config: clearance_samples_per_segment


def main() -> int:
    th.style()
    plt = th.plt
    diag = json.loads((RUN_DIR / "failures.json").read_text(encoding="utf-8"))
    leg = diag["failures"][0]["diagnostics"]["legs"][5]
    pts = np.asarray(leg["best_effort"]["samples"], dtype=float)

    from orchestration.experiments.exp01_smoke.support import config as cfgmod, field as fld
    from path_planning.methods.spatial.corridor_window import corridor_window
    from path_planning.methods.spatial.occupancy_sdf import SdfField

    cfg = cfgmod.load_config()
    sc = fld.load_guangzhou(cfg)
    wf, _ = fld.load_or_build_field(cfg, sc)
    ep = leg["endpoints_m"]
    alt = tuple(leg["alt_m"])
    spacing = tuple(float(v) for v in cfg["field"]["spacing_m"])
    win = corridor_window(tuple(ep["start"]), tuple(ep["goal"]),
                          fld.corridor_rule(cfg), spacing, alt[0], alt[1])
    wfld = SdfField(win.grid, wf.window_sdf(win.grid))
    sdf = np.asarray(wfld.at(pts), dtype=float).ravel()

    # 段边界：polyline_positions 每段含两端，相邻段共享端点 → 连续重复点即节点
    step = np.concatenate([[0.0], np.linalg.norm(np.diff(pts, axis=0), axis=1)])
    cum = np.cumsum(step)
    dup = np.unique(np.where(np.linalg.norm(np.diff(pts, axis=0), axis=1) < 1e-9)[0])
    bounds = np.concatenate([[-1], dup, [len(pts) - 1]]).astype(int)
    # 节点索引 = bounds[:-1] + 1（每段首尾各一个节点，末节点即最后一点）
    node_idx = bounds[:-1] + 1
    node_cum = cum[node_idx]
    node_pts = pts[node_idx]

    k = int(np.argmin(sdf))
    worst_x, worst_sdf = float(cum[k]), float(sdf[k])
    # 最长段（节点分布极不均的那一段）
    seg_len = np.diff(node_cum)
    bad = int(np.argmax(seg_len))

    fig, (ax0, ax1) = plt.subplots(
        2, 1, figsize=(13.2, 8.2), sharex=True,
        gridspec_kw=dict(height_ratios=[1.35, 1.0], hspace=0.13),
    )

    # ---------- 上：净空剖面 ----------
    ax0.axhspan(0, DELIVER_CLEARANCE, color="#FBE9E7", zorder=0)
    ax0.plot(cum, sdf, color="#2E5C8A", lw=1.1, zorder=2, label="节点折线净空（4 m 采样，窗口场）")
    ax0.axhline(DELIVER_CLEARANCE, color=th.FAIL, lw=1.4, zorder=3,
                label="交付阈值 6.0 m")
    ax0.axhline(BASE_CLEARANCE, color="#B5651D", lw=1.1, ls="--", zorder=3,
                label="求解阈值 8.0 m（6.0 + 2.0 量化容差）")
    ax0.axhline(SOLVE_CLEARANCE, color="#8A8A8A", lw=1.1, ls=":", zorder=3,
                label="本次求解阈值 10.0 m（再加 2.0 抬阈）")

    # 节点：短竖线；每段 8 个 NLP 插值约束点：叉
    ax0.plot(node_cum, np.zeros_like(node_cum), "|", color="#9BB7D4", ms=7, mew=0.9, zorder=1)
    ip_x, ip_y = [], []
    for i in range(len(node_cum) - 1):
        x0, x1 = float(node_cum[i]), float(node_cum[i + 1])
        a = node_pts[i]
        b = node_pts[i + 1]
        for j in range(1, SEG_SAMPLES + 1):
            al = j / (SEG_SAMPLES + 1)
            q = (1 - al) * a + al * b
            ip_x.append(x0 + al * (x1 - x0))
            ip_y.append(float(np.asarray(wfld.at(q.reshape(1, 3))).ravel()[0]))
    ax0.plot(ip_x, ip_y, "x", color="#333333", ms=4.0, mew=0.9, zorder=4,
             label=f"NLP 硬约束取样点（每段 {SEG_SAMPLES} 个 + 节点）")

    # 最长段：淡黄带 + 端点
    bx0, bx1 = float(node_cum[bad]), float(node_cum[bad + 1])
    for ax in (ax0, ax1):
        ax.axvspan(bx0, bx1, color="#FFF3CD", alpha=0.75, zorder=0)
    ax0.annotate(f"最长一段：{seg_len[bad]:.0f} m 长直线\n"
                 f"两端节点净空 {sdf[node_idx[bad]]:.1f} / {sdf[node_idx[bad + 1]]:.1f} m，"
                 f"后段横穿障碍",
                 xy=(0.5 * (bx0 + bx1), 1.0), xycoords=("data", "axes fraction"),
                 xytext=(0.42 * (bx0 + bx1), 0.99), textcoords=("data", "axes fraction"),
                 ha="center", va="top", fontsize=9.2, color="#7A5B00",
                 arrowprops=dict(arrowstyle="-|>", color="#B58A00", lw=1.1))

    ax0.plot([worst_x], [worst_sdf], "o", ms=8, color=th.FAIL, zorder=6)
    ax0.annotate(f"最紧点 {worst_sdf:.2f} m\n（交付口径 0.2 m 稠密为 5.32 m）",
                 xy=(worst_x, worst_sdf), xytext=(worst_x - 210, 1.4),
                 fontsize=9.6, color=th.FAIL, fontweight="bold",
                 arrowprops=dict(arrowstyle="-|>", color=th.FAIL, lw=1.2))
    ax0.set_ylabel("离地净空 (m)", labelpad=8)
    ax0.set_title("周期1 机3  OPT_FAILED：T8 -> T23 航段的净空剖面与 NLP 约束取样点")
    ax0.set_ylim(-1.0, 34.0)
    ax0.grid(axis="y", lw=0.6)
    ax0.legend(loc="upper left", fontsize=9.0, ncol=1, framealpha=0.92,
               facecolor="white", edgecolor="#DDDDDD")

    # ---------- 下：高度剖面 ----------
    ax1.plot(cum, pts[:, 2], color="#2E7D4F", lw=1.4, zorder=3, label="节点折线高度")
    ax1.axhspan(10, 120, color="#EEF6EF", zorder=0)
    ax1.axhline(84, color="#B5651D", lw=1.2, ls="--", zorder=2)
    ax1.axhline(88.2, color="#8A6FB0", lw=1.2, ls=":", zorder=2)
    ax1.text(cum[-1] * 0.995, 100.0, "绕过该楼需爬到 84 m 以上", color="#B5651D",
             ha="right", va="bottom", fontsize=9.2)
    ax1.text(cum[-1] * 0.995, 89.5, "RRT 候选达到的绕行高度 88.2 m", color="#7A5B9A",
             ha="right", va="bottom", fontsize=9.2)
    ax1.axhline(61.5, color=th.FAIL, lw=1.0, ls="-.", zorder=2)
    ax1.text(cum[-1] * 0.995, 63.0, "NLP 解的最高点 61.5 m（未爬升）",
             color=th.FAIL, ha="right", va="bottom", fontsize=9.2)
    ax1.plot([worst_x], [pts[k, 2]], "o", ms=8, color=th.FAIL, zorder=6)
    ax1.annotate(f"撞上这里 z={pts[k, 2]:.1f} m", xy=(worst_x, pts[k, 2]),
                 xytext=(worst_x - 175, 14), fontsize=9.4, color=th.FAIL,
                 arrowprops=dict(arrowstyle="-|>", color=th.FAIL, lw=1.1))
    ax1.set_ylabel("绝对高度 z (m)", labelpad=8)
    ax1.set_xlabel("沿程水平距离 (m)")
    ax1.set_ylim(5, 118)
    ax1.grid(axis="y", lw=0.6)
    ax1.legend(loc="upper left", fontsize=9.2)

    fig.text(0.010, 0.993,
             "失败段：T8 (489.98, -997.09, 41.71) -> T23 (1101.53, -432.83, 20.34)，"
             "水平 859.6 m，走廊半宽 83.2 m",
             fontsize=9.6, color=th.INK, va="top")
    fig.text(0.010, 0.966,
             "RRT 5/5 种子几何可达；10 次 NLP 尝试中 3 次收敛但违反净空、7 次迭代耗尽",
             fontsize=9.6, color=th.INK, va="top")
    fig.text(0.008, 0.012,
             "口径：净空取自该段走廊窗口场（与 NLP 求解同源）；交付口径另按全域场 0.2 m 稠密采样，"
             "得 5.32 m < 6.0 m，故判 CLEARANCE_VIOLATION。节点折线即交付物，"
             "Hermite 曲线只作表示误差记账、不进判决。",
             fontsize=8.8, color=th.MUTED, va="bottom")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=th.DEFAULT_DPI, facecolor="white")
    plt.close(fig)
    print(f"[exp01] 诊断图 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
