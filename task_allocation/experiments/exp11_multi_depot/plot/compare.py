# -*- coding: utf-8 -*-
"""exp11 事后绘图（本实验自持；跑批阶段**不调用**）。

读 ``results/<preset>/summary.json`` + 逐 run 的 metrics.json。数值结论
一律以 summary.json / summary.csv / tables.* 为准，图只作直观核对。

产出（``results/<preset>/figures/``）：
  fig_accum.png     三臂相对 MCF 的额外航程随梯度 T 的累积曲线（主图）
  fig_retleg_T*.png 逐周期累计返航段（3 seed 均值）
  fig_share.png     异巢降落占比 × 梯度（三臂并列柱）

用法::

    python plot/compare.py --preset formal
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paths import EXP_DIR  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ARMS = ("home", "nearest", "mcf")
ARM_LABEL = {"home": "强制返原巢", "nearest": "就近贪心终点", "mcf": "本文 MCF"}
COLOR = {"home": "#c0392b", "nearest": "#e67e22", "mcf": "#1f6fb4"}


def _setup_font() -> None:
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def fig_accum(summary: Mapping[str, Any], tiers: Sequence[int], out: Path) -> Path:
    """左右两栏：累计返航段差（随周期增长） vs 每航次返航段差（近似持平）。

    只画返航段通道：它是终点策略的直接效应。总航程/f 混着任务选择与排序，
    端到端存在 seed 级反向格子，另见 tables_effects.tex。
    """
    acc = summary["accumulation"]
    xs = [acc[str(t)]["n_tasks_total"] for t in tiers]
    h_leg = [acc[str(t)]["extra_return_leg_m_home_vs_mcf"] / 1000 for t in tiers]
    n_leg = [acc[str(t)]["extra_return_leg_m_nearest_vs_mcf"] / 1000 for t in tiers]
    h_unit = [
        summary["tiers_report"][str(t)]["effects"]["return_leg_per_sortie_m"]
        ["home_minus_mcf"]["mean"] for t in tiers
    ]
    n_unit = [
        summary["tiers_report"][str(t)]["effects"]["return_leg_per_sortie_m"]
        ["nearest_minus_mcf"]["mean"] for t in tiers
    ]
    fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.4), dpi=300)
    ax = axes[0]
    ax.plot(xs, h_leg, "o-", color=COLOR["home"], label="返原巢-MCF")
    ax.plot(xs, n_leg, "s--", color=COLOR["nearest"], label="就近-MCF")
    ax.set_xlabel("累计任务数（每周期 10 个）")
    ax.set_ylabel("累计额外返航段 / km")
    ax.set_title("(a) 累计节省随周期增长")
    ax.set_xticks(xs)
    ax.grid(alpha=0.3, ls=":")
    ax.legend(fontsize=8, frameon=False)

    ax = axes[1]
    ax.plot(xs, h_unit, "o-", color=COLOR["home"], label="返原巢-MCF")
    ax.plot(xs, n_unit, "s--", color=COLOR["nearest"], label="就近-MCF")
    ax.set_xlabel("累计任务数（每周期 10 个）")
    ax.set_ylabel("每航次返航段差 / m")
    ax.set_title("(b) 单位节省近似持平")
    ax.set_xticks(xs)
    ax.grid(alpha=0.3, ls=":")
    ax.legend(fontsize=8, frameon=False)

    fig.tight_layout()
    path = out / "fig_accum.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_counterfactual(summary: Mapping[str, Any], tiers: Sequence[int], out: Path) -> Path:
    """归因分账：同一组路径上换终点的直接价值 vs 三臂端到端的实际差。"""
    acc = summary["accumulation"]
    xs = [acc[str(t)]["n_tasks_total"] for t in tiers]
    gap_mcf = [acc[str(t)].get("same_routes_gap_per_sortie_mcf", 0.0) for t in tiers]
    gap_near = [acc[str(t)].get("same_routes_gap_per_sortie_nearest", 0.0) for t in tiers]
    arm_diff = [
        summary["tiers_report"][str(t)]["effects"]["return_leg_per_sortie_m"]
        ["home_minus_mcf"]["mean"] for t in tiers
    ]
    fig, ax = plt.subplots(figsize=(6.6, 3.4), dpi=300)
    ax.plot(xs, gap_mcf, "o-", color=COLOR["mcf"], label="MCF 同解Δ（直接价值）")
    ax.plot(xs, gap_near, "s--", color=COLOR["nearest"], label="就近 同解Δ（直接价值）")
    ax.plot(xs, arm_diff, "^-", color=COLOR["home"], label="返原巢-MCF 臂间差（端到端）")
    ax.set_xlabel("累计任务数（每周期 10 个）")
    ax.set_ylabel("返航段 / m·航次$^{-1}$")
    ax.set_title("同一路径换终点的价值 vs 端到端实际差（保留率 = 臂间差/同解Δ）")
    ax.set_xticks(xs)
    ax.grid(alpha=0.3, ls=":")
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    path = out / "fig_counterfactual.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def _load_cells(preset: str, tier: int) -> dict[str, list[dict[str, Any]]]:
    root = EXP_DIR / "results" / preset / f"T{tier}"
    cells: dict[str, list[dict[str, Any]]] = {}
    for arm in ARMS:
        rows = []
        for d in sorted((root / arm).glob("seed_*")):
            f = d / "metrics.json"
            if f.exists():
                rows.append(json.loads(f.read_text(encoding="utf-8")))
        cells[arm] = rows
    return cells


def fig_retleg(preset: str, tier: int, out: Path) -> Path | None:
    cells = _load_cells(preset, tier)
    if not any(cells.values()):
        return None
    fig, ax = plt.subplots(figsize=(6.2, 3.6), dpi=300)
    for arm in ARMS:
        rows = [r["return_leg_by_cycle"] for r in cells[arm]
                if r.get("return_leg_by_cycle")]
        if not rows:
            continue
        n = min(len(r) for r in rows)
        cum = 0.0
        ys = []
        for i in range(n):
            cum += sum(r[i] for r in rows) / len(rows)
            ys.append(cum / 1000)
        ax.plot(range(1, n + 1), ys, "o-", ms=3, color=COLOR[arm],
                label=ARM_LABEL[arm])
    ax.set_xlabel("滚动周期")
    ax.set_ylabel("累计返航段 / km")
    ax.set_title(f"逐周期累计返航段（T={tier}，3 seed 均值）")
    ax.grid(alpha=0.3, ls=":")
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    path = out / f"fig_retleg_T{tier}.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_share(summary: Mapping[str, Any], tiers: Sequence[int], out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(6.2, 3.4), dpi=300)
    width = 0.26
    for i, arm in enumerate(ARMS):
        vals = [summary["tiers_report"][str(t)]["arms"][arm]["cross_nest_share"]["mean"]
                for t in tiers]
        ax.bar([j + (i - 1) * width for j in range(len(tiers))], vals, width,
               color=COLOR[arm], label=ARM_LABEL[arm])
    ax.set_xticks(range(len(tiers)))
    ax.set_xticklabels([f"T={t}\n({t * 10} 任务)" for t in tiers])
    ax.set_ylabel("异巢降落占比")
    ax.set_ylim(0, 1.05)
    ax.set_title("异巢降落占比随梯度变化（返原巢臂恒 0 为设计使然）")
    ax.grid(axis="y", alpha=0.3, ls=":")
    ax.legend(fontsize=8, frameon=False, ncol=3, loc="upper left")
    fig.tight_layout()
    path = out / "fig_share.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="exp11 图生成")
    ap.add_argument("--preset", default="formal")
    args = ap.parse_args(argv)
    _setup_font()
    root = EXP_DIR / "results" / args.preset
    sp = root / "summary.json"
    if not sp.exists():
        print(f"[plot] 缺 summary.json：{sp}", flush=True)
        return 2
    summary = json.loads(sp.read_text(encoding="utf-8"))
    tiers = [int(t) for t in summary.get("tiers", [])]
    out = root / "figures"
    out.mkdir(parents=True, exist_ok=True)
    made = [fig_accum(summary, tiers, out), fig_share(summary, tiers, out),
            fig_counterfactual(summary, tiers, out)]
    for t in tiers:
        p = fig_retleg(args.preset, t, out)
        if p:
            made.append(p)
    for p in made:
        print(f"[plot] {p}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
