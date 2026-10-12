# -*- coding: utf-8 -*-
"""图 ``task_wait``——任务等待时间与滞留：任务到底在等什么。

【process 层 · 运行过程观测】

process 层回答「滚动闭环到底怎么跑的」。本图从**任务个体**视角看同一个
过程：每个任务从到达到完成等了多久、被延期几个轮次、还在池子里的任务
窗口还剩多少余量。

三个本图独有的判断（其它图看不到）：

    右删失      未完成任务只观测到实验截断时刻，等待时间是**下界**（≥），
                不能与已完成任务同轴直接比大小——图中以斜纹 + 箭头标注
    批次倒挂    后到达的批次反而等得更少 → 队头阻塞（早到的难任务堵住池子）
    滞留≠违约   滞留任务的窗口余量仍很充足 → 未完成是时域太短，不是时间窗
                不允许；这决定了「该不该把滞留计入违约」的口径

数据来源：``solution.json`` 的 ``tasks[]``（arrival_time / completed_at /
status / earliest / latest），滞留轮次来自 ``metrics.json`` 的
``cycles[].deferred_ids``，实验截断时刻取最后一个周期的 ``current_time``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import PALETTE, plt, save_figure, style_axes

__all__ = ["plot_task_wait"]


def _horizon(metrics: dict[str, Any], solution: dict[str, Any]) -> float:
    times = [float(c.get("current_time") or 0.0)
             for c in (metrics.get("cycles") or [])]
    times += [float(c.get("current_time") or 0.0)
              for c in (solution.get("cycles") or [])]
    return max(times) if times else 0.0


def _defer_rounds(metrics: dict[str, Any]) -> dict[int, int]:
    """每个任务被延期的轮次总数（出现在几个周期的 deferred_ids）。"""
    rounds: dict[int, int] = {}
    for c in metrics.get("cycles") or []:
        for tid in c.get("deferred_ids") or []:
            rounds[int(tid)] = rounds.get(int(tid), 0) + 1
    return rounds


def plot_task_wait(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """画 2×2 任务等待面板；无任务表时返回 []。"""
    tasks = [t for t in (solution.get("tasks") or [])
             if t.get("arrival_time") is not None]
    if not tasks:
        return []
    horizon = _horizon(metrics, solution)
    rounds = _defer_rounds(metrics)
    n = len(tasks)

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.4), layout="constrained")
    ax_bar, ax_batch, ax_rounds, ax_slack = axes.ravel()

    ids = [int(t["id"]) for t in tasks]
    done = [str(t.get("status")) == "done" for t in tasks]
    end = [float(t["completed_at"]) if t.get("completed_at") is not None
           else horizon for t in tasks]
    wait = [e - float(t["arrival_time"]) for e, t in zip(end, tasks)]

    # ── (a) 每任务等待时间 ────────────────────────────────────────
    x = np.arange(n)
    colors = [PALETTE["executed"] if d else PALETTE["deferred"] for d in done]
    bars = ax_bar.bar(x, wait, 0.62, color=colors, edgecolor="white",
                      linewidth=0.6)
    for b, d in zip(bars, done):
        if not d:
            b.set_hatch("//")
            b.set_alpha(0.75)
    for xi, w, d in zip(x, wait, done):
        ax_bar.text(xi, w + horizon * 0.015, ("≥ " if not d else "") + f"{w:.0f}",
                   ha="center", fontsize=7.6, color="0.3")
    done_waits = [w for w, d in zip(wait, done) if d]
    if done_waits:
        mean_w = float(np.mean(done_waits))
        ax_bar.axhline(mean_w, color="0.4", linewidth=0.9, linestyle="--",
                       zorder=0)
        ax_bar.text(n - 0.4, mean_w + horizon * 0.015,
                    f"已完成者均值 {mean_w:.0f}s", ha="right", fontsize=8,
                    color="0.4")
    ax_bar.set_xticks(x, [str(i) for i in ids], fontsize=7.5)
    ax_bar.set_ylim(0, max(wait) * 1.18 + horizon * 0.05)
    style_axes(ax_bar, title=f"(a) 每任务等待时间（截断于 t={horizon:.0f}s）",
               xlabel="任务 id", ylabel="等待 (s)")
    handles = [
        plt.Rectangle((0, 0), 1, 1, facecolor=PALETTE["executed"],
                      edgecolor="white", label="已完成（实测等待）"),
        plt.Rectangle((0, 0), 1, 1, facecolor=PALETTE["deferred"], hatch="//",
                      edgecolor="white", alpha=0.75,
                      label="未完成（等待为下界 ≥，右删失）"),
    ]
    ax_bar.legend(handles=handles, loc="upper left", fontsize=8,
                  framealpha=0.92)

    # ── (b) 按到达批次的等待分布 ──────────────────────────────────
    batches = sorted({float(t["arrival_time"]) for t in tasks})
    rng = np.random.default_rng(3)
    for bi, arr in enumerate(batches):
        vals = [w for w, t in zip(wait, tasks)
                if float(t["arrival_time"]) == arr]
        ds = [d for d, t in zip(done, tasks) if float(t["arrival_time"]) == arr]
        pos = np.full(len(vals), bi) + rng.uniform(-0.16, 0.16, len(vals))
        for p, v, d in zip(pos, vals, ds):
            ax_batch.scatter(p, v, s=46, zorder=3,
                             color=PALETTE["executed"] if d else PALETTE["deferred"],
                             edgecolor="white", linewidth=0.7, alpha=0.9)
        bp = ax_batch.boxplot([vals], positions=[bi], widths=0.34,
                              patch_artist=True, showmeans=True,
                              medianprops=dict(color="0.25"),
                              meanprops=dict(marker="D", markerfacecolor="white",
                                             markeredgecolor="0.25", markersize=5),
                              flierprops=dict(marker=""))
        for box in bp["boxes"]:
            box.set(facecolor=PALETTE["new"], alpha=0.22,
                    edgecolor=PALETTE["new"], linewidth=1.1)
        ax_batch.text(bi, max(vals) + horizon * 0.06,
                      f"均值 {np.mean(vals):.0f}s\n(n={len(vals)})",
                      ha="center", fontsize=8, color="0.3")
    ax_batch.set_xticks(range(len(batches)),
                        [f"t={a:.0f}s" for a in batches])
    ax_batch.set_ylim(0, max(wait) * 1.35)
    style_axes(ax_batch, title="(b) 等待 × 到达批次：后到的等得更少？",
               xlabel="到达时刻", ylabel="等待 (s)")

    # ── (c) 滞留轮次分布 ──────────────────────────────────────────
    r_vals = [rounds.get(i, 0) for i in ids]
    palette_by_round = [PALETTE["executed"] if d else PALETTE["deferred"]
                        for d in done]
    counts = {}
    for r, d in zip(r_vals, done):
        counts.setdefault(r, {"done": 0, "pending": 0})
        counts[r]["done" if d else "pending"] += 1
    rr = sorted(counts)
    ax_rounds.bar([r - 0.19 for r in rr], [counts[r]["done"] for r in rr], 0.36,
                  color=PALETTE["executed"], edgecolor="white", linewidth=0.6,
                  label="最终完成")
    ax_rounds.bar([r + 0.19 for r in rr],
                  [counts[r]["pending"] for r in rr], 0.36,
                  color=PALETTE["deferred"], edgecolor="white", linewidth=0.6,
                  hatch="//", alpha=0.8, label="仍未完成")
    for r in rr:
        tot = counts[r]["done"] + counts[r]["pending"]
        ax_rounds.text(r, tot + 0.06, f"{tot}", ha="center", fontsize=8.5,
                       color="0.3")
    ax_rounds.set_xticks(rr)
    ax_rounds.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax_rounds.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax_rounds.set_ylim(0, max(sum(counts[r].values()) for r in rr) * 1.2 + 0.4)
    style_axes(ax_rounds, title="(c) 滞留轮次分布（被延期几次）",
               xlabel="滞留轮次", ylabel="任务数")
    ax_rounds.legend(loc="upper right", fontsize=8.5, framealpha=0.92)

    # ── (d) 滞留任务：窗口余量 vs 滞留轮次 ────────────────────────
    stuck = [(i, r, float(t.get("latest") or 0.0) - horizon, float(t.get("reward") or 1.0))
             for i, r, t in zip(ids, r_vals, tasks) if r > 0]
    if stuck:
        for tid, r, slack, rew in stuck:
            d = done[ids.index(tid)]
            ax_slack.scatter(r, slack, s=28 + 26 * (rew / max(
                (s[3] for s in stuck), default=1.0)),
                color=PALETTE["executed"] if d else PALETTE["deferred"],
                edgecolor="white", linewidth=0.8, zorder=3, alpha=0.9)
            ax_slack.annotate(f"{tid}", (r, slack), textcoords="offset points",
                              xytext=(0, 8), ha="center", fontsize=7.5,
                              color="0.3")
        ax_slack.axhline(0, color=PALETTE["release"], linewidth=1.1,
                         linestyle="--")
        ax_slack.text(max(r for _, r, _, _ in stuck), horizon * 0.02,
                      "窗口已过期（违约线）", ha="right", va="bottom",
                      fontsize=8, color=PALETTE["release"])
        min_slack = min(s[2] for s in stuck)
        if min_slack > 0:
            ax_slack.text(
                0.02, 0.97,
                f"全部滞留任务窗口余量 ≥ {min_slack:.0f}s（最小者）\n"
                f"→ 未完成是时域太短（仅 {len(metrics.get('cycles') or [])} 个周期），"
                f"不是时间窗不允许\n→ 继续滚动多数仍可赶上，不应计入违约",
                transform=ax_slack.transAxes, ha="left", va="top",
                fontsize=8.5, color="#3b6d11")
        ax_slack.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
        style_axes(ax_slack, title="(d) 滞留任务：窗口余量 vs 滞留轮次（点大小 ∝ 收益）",
                   xlabel="滞留轮次", ylabel="窗口余量 latest − horizon (s)")
    else:
        ax_slack.text(0.5, 0.5, "无任务被延期", transform=ax_slack.transAxes,
                      ha="center", va="center", fontsize=10, color="0.45")
        style_axes(ax_slack, title="(d) 滞留任务：窗口余量 vs 滞留轮次")

    fig.suptitle("任务等待与滞留：任务到底在等什么", fontsize=12.5)
    return save_figure(fig, Path(out_dir) / "task_wait", formats, dpi=dpi)
