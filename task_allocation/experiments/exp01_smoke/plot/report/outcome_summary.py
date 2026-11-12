# -*- coding: utf-8 -*-
"""图 ``outcome_summary``——结果总览：正确执行吞吐与任务终局。

【report 层 · 结果分析】

回答 Q4「实验说明什么」的**核心图**：滚动闭环跑完，任务到底做得怎么样。

口径（长程协议）：**完成率不是目标**，周期可无限延长；真实目标 =
持续到达下「尽可能多且正确地完成任务」。因此主面板是正确执行与吞吐，
完成率降为辅助对账。

2×2 四联面板：

    (a) 每周期执行构成   出动 = 按时交付 + 迟到交付 + 废飞（堆叠柱）；右轴 = 累计
                         吞吐（累计完成 / 运营周期数）
    (b) 任务终局构成     到达任务的最终去向：完成 / 不可达（出池）/ 服务暂缓 /
                         滞留池（非暂缓）四段分账（互斥、合计 = 到达），另附
                         超期滞留 / 物理拦回两段诊断叠层（无超期淘汰，过期任务
                         滞留不出池；拦回 = 解被判物理不可行、解绑回池——迟到
                         交付已计入完成；服务暂缓 = 时变能力不足暂缓、等能力恢复
                         唤醒，图示弃用「休眠」旧称）
    (c) 双目标值         每周期 F1 收益（迟到折减后）/ F2 代价，不可行周期红字标记
    (d) 关键指标表       summary 全字段 + 单/多 seed 标注

数据来源：``metrics.json`` 的 cycles[] / summary / seeds。
本图对**调试期同样有用**：按时率塌陷、全周期不可行、物理拦回等回归问题，
在这张图上一眼可见（而不是被平均进某个数字里）。
"""

from __future__ import annotations

# ── 独立运行自举：支持直接 `python plot/.../xxx.py`（脚本方式）──
# 脚本方式运行时 __package__ 为空，模块内相对导入不可用；此处转以 -m 语义
# 重跑自身（runpy 会正确设置包上下文），再触发文件末尾的 __main__ 守卫出图。
if __name__ == "__main__" and __package__ in (None, ""):
    import sys, runpy
    from pathlib import Path as _P
    _here = _P(__file__).resolve()
    _root = _here.parents[2]   # 实验根 = plot 的上两级（使 `plot` 包可导入）
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    # 同时把项目根（含 task_allocation 包）加入路径：否则 plotting 内
    # `from task_allocation.methods.utils import GeoTIFFDEM` 会因找不到包而
    # 静默回退到 solution.json 的降采样 DEM（115x101px），地图被放大 10~24 倍变糊。
    _proj = _here
    while not (_proj / "task_allocation" / "__init__.py").is_file() and _proj.parent != _proj:
        _proj = _proj.parent
    if (_proj / "task_allocation" / "__init__.py").is_file() and str(_proj) not in sys.path:
        sys.path.insert(0, str(_proj))
    _rel = _here.relative_to(_root / "plot")
    _mod = "plot." + ".".join(_rel.with_suffix("").parts)
    runpy.run_module(_mod, run_name="__main__", alter_sys=True)
    raise SystemExit(0)


from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import FIG_WIDE, FS, MONO_ZH, PALETTE, footnote, plt, save_figure, style_axes

__all__ = ["plot_outcome_summary"]


def plot_outcome_summary(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    cycles = list(metrics.get("cycles") or [])
    summary = metrics.get("summary") or {}
    if not cycles or not summary:
        return []

    n_cycles = len(cycles)
    cyc_labels = [f"C{c.get('cycle')}" for c in cycles]
    x = np.arange(n_cycles)
    w = 0.38

    # ── 数据准备 ─────────────────────────────────────────────────
    n_exec = [int(c.get("n_executed", 0)) for c in cycles]
    n_done = [int(c.get("n_completed", 0)) for c in cycles]
    # 迟到不是废飞：完成 = 按时交付 + 迟到交付；
    # 废飞只剩物理不可行的弹回（n_infeasible）
    n_late = [int(c.get("n_late", 0)) for c in cycles]
    n_on_time = [d - l for d, l in zip(n_done, n_late)]
    n_waste = [int(c.get("n_infeasible", 0)) for c in cycles]
    cum_done = np.cumsum(n_done)
    # 累计正确执行吞吐 = 累计完成（含迟到）/ 周期序号（长程协议主指标）。
    # 口径统一：吞吐分母 = 运营周期数（本图周期轴长 = 落盘周期
    # 裁掉尾部收敛空周期后的数目，绘图裁剪见 plot._trim_cycles_for_plot，
    # 指标侧见 metrics.summary.n_oper_cycles）。曲线终点、(d) 表与
    # metrics.json 的 throughput_per_cycle 同源同值（统一前 (a) 按绘制
    # 周期数、(d) 按落盘全周期数，分母双值、不要求相等——已废）。
    thru_curve = [d / (i + 1) for i, d in enumerate(cum_done)]
    thru_final = thru_curve[-1] if thru_curve else 0.0

    done_ids: set[int] = set()
    overdue_ids: set[int] = set()
    gated_ids: set[int] = set()
    for c in cycles:
        done_ids.update(int(i) for i in (c.get("completed_ids") or []))
        overdue_ids.update(int(i) for i in (c.get("overdue_ids") or []))
        gated_ids.update(int(i) for i in (c.get("infeasible_ids") or []))
    n_arrived = int(summary.get("n_arrived", 0))
    n_done_total = int(summary.get("n_completed", len(done_ids)))
    # 无超期淘汰：过期任务滞留池中、不出池，不减完成。
    #口径约定 A 分账恒等式：arrived = done + unreachable + 暂缓 + remain。
    # n_remaining_pool = len(pool) 含服务暂缓 DORMANT（暂缓不出池、仍计在
    # 池内）；单列：不可达与服务暂缓各占终局构成一段，「滞留池中」
    # = remain − 暂缓（非暂缓部分）。静态几何不可达已出池、不在池内。
    # fallback 仅在旧记录缺 n_remaining_pool 键时按 arrived−done−unreachable
    # 反推，新产物走主分支。
    n_dormant_end = int(summary.get("n_dormant_end", 0))
    n_unreach = int(summary.get("n_unreachable", 0))
    n_remain_total = int(summary.get("n_remaining_pool",
                                     max(n_arrived - n_done_total - n_unreach, 0)))
    n_remain_plain = max(n_remain_total - n_dormant_end, 0)  # 滞留（非暂缓）
    n_overdue = len(overdue_ids)
    n_gated = len(gated_ids - done_ids - overdue_ids)
    n_late_total = int(summary.get("n_late", 0))
    # A2：删除出动池化的 on_time_rate 键后，(d) 表「按时率」
    # 取交付集条件率 on_time_rate_final（论文口径）；出动池化正确执行率
    # 由 correct_exec_rate（逐周期均值）承担，两个量都留在表里、各有名号。
    on_time_final = float(summary.get("on_time_rate_final", 0.0))
    avg_late = float(summary.get("avg_lateness_s", 0.0))

    f1 = [float((c.get("muas") or {}).get("f1_reward", 0.0)) for c in cycles]
    f2 = [float((c.get("muas") or {}).get("f2_cost", 0.0)) / 1000.0 for c in cycles]
    feas = [bool((c.get("muas") or {}).get("feasible", False)) for c in cycles]

    _seeds = list(metrics.get("seeds") or [])
    n_seeds = len(_seeds)
    first_seed = _seeds[0] if _seeds else "?"
    feas_rate = float(summary.get("cycle_feasible_rate", 0.0))
    # A1：naive 完成率键已删，完成率只报 final 口径（主口径）。
    #口径约定 A：η_final 分母 = arrived − unreachable − dormant_end
    # （排除静态几何不可达 + 停机时仍服务休眠两类）；自然收敛时 dormant_end=0。
    completion = float(summary.get("completion_rate_final", 0.0))
    correct_rate = float(summary.get("correct_exec_rate", 0.0))
    # 吞吐与 (a) 曲线同源（统一口径）：分母 = 图上周期轴长
    # = 运营周期数；新 metrics.json 的 summary.throughput_per_cycle 同值。
    # 不直读 summary 是为了旧产物（全周期分母）重新出图时仍与曲线自洽。
    throughput = thru_final
    n_waste_total = int(summary.get("n_infeasible_delivery", 0))
    # B1：废飞已飞航程（与计数同权，防「总航程下降靠废飞换来」）
    waste_path_m = float(summary.get("infeasible_wasted_path_m", 0.0))
    n_eliminated_total = int(summary.get("n_eliminated", 0))
    # B2：候选池命中率（验证「候选池该富」设计声明）
    cand_hit = float(summary.get("cand_hit_rate", 0.0))
    # E：门禁诊断（门禁是否按设计工作的直接证据）
    n_gate_rescued = int(summary.get("n_gate_rescued", 0))
    n_skip = int(summary.get("n_voluntary_skip", 0))

    fig, ((ax_a, ax_b), (ax_c, ax_d)) = plt.subplots(
        2, 2, figsize=(FIG_WIDE, 9.0))

    # ── (a) 每周期执行构成 + 累计吞吐 ────────────────────────────
    ax_a.bar(x, n_on_time, 0.62, color=PALETTE["executed"], alpha=0.9,
             label="按时交付", edgecolor="white", linewidth=0.4)
    ax_a.bar(x, n_late, 0.62, bottom=n_on_time, color=PALETTE["deferred"],
             alpha=0.9, label="迟到交付（计完成）", edgecolor="white",
             linewidth=0.4)
    ax_a.bar(x, n_waste, 0.62, bottom=n_done, color=PALETTE["release"],
             alpha=0.85, label="废飞（物理不可行）", edgecolor="white",
             linewidth=0.4, hatch="//")
    style_axes(ax_a, title=f"(a) 每周期执行构成（柱高=出动；"
                           f"终点吞吐 {thru_final:.2f} 任务/周期）",
               xlabel="滚动周期", ylabel="任务数")
    ax_a.set_xticks(x, cyc_labels)
    ax_a2 = ax_a.twinx()
    ax_a2.plot(x, thru_curve, "-o", color=PALETTE["cost"],
               lw=1.6, ms=4, label="累计吞吐（右轴）")
    ax_a2.set_ylabel("累计吞吐（任务/周期）", color=PALETTE["cost"],
                     fontsize=FS["base"])
    ax_a2.set_ylim(bottom=0)
    ax_a2.tick_params(axis="y", labelcolor=PALETTE["cost"])
    ax_a2.spines["right"].set_visible(True)
    ax_a2.grid(False)
    h1, l1 = ax_a.get_legend_handles_labels()
    h2, l2 = ax_a2.get_legend_handles_labels()
    ax_a.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=FS["legend"])
    # 图例在左上角，抬高左轴上限给它留出净空，避免压住 C1 堆叠柱顶。
    ax_a.set_ylim(0, max(max(n_exec, default=1) * 1.45, 1.0))

    # ── (b) 任务终局构成 ─────────────────────────────────────────
    # 终局分账恒等式（口径约定 A）：到达 = 完成 + 不可达 + 服务暂缓
    # + 滞留（非暂缓），前四段互斥、合计 = 到达；后两段为诊断叠层（可与
    # 分账重叠、非终局损失）。「服务暂缓」= 时变能力不足暂缓（DORMANT），
    # 不出池、等能力恢复唤醒；图示弃用「休眠」旧称（表意不清）。
    parts = [
        # (图例名, 柱内短名, 值, 颜色)
        ("已完成", "已完成", n_done_total, PALETTE["executed"]),
        ("不可达·出池", "不可达", n_unreach, "#8d99ae"),
        ("服务暂缓·待恢复", "服务暂缓", n_dormant_end, PALETTE["deferred"]),
        ("滞留池中·非暂缓", "滞留池中", n_remain_plain, "#4c78a8"),
        ("超期滞留·诊断", "超期滞留", n_overdue, PALETTE["release"]),
        ("物理拦回·诊断", "物理拦回", n_gated, "#b279a2"),
    ]
    parts = [p for p in parts if p[2] > 0]
    if not parts:
        parts = [("无任务", "无任务", 0, "#c9ced4")]
    bottom = 0
    for leg, short, v, col in parts:
        ax_b.bar([0], [v], 0.5, bottom=bottom, color=col, alpha=0.9,
                 edgecolor="white", linewidth=0.6, label=f"{leg}（{v}）")
        if v > 0:
            ax_b.text(0, bottom + v / 2, f"{short}\n{v}", ha="center", va="center",
                      fontsize=FS["annotation"], color="white")
        bottom += v
    ax_b.set_xlim(-0.6, 0.6)
    ax_b.set_xticks([])
    style_axes(ax_b, title=f"(b) 任务终局构成（到达 {n_arrived} 个）",
               ylabel="任务数")
    ax_b.legend(loc="upper left", fontsize=FS["legend"],
                labelspacing=0.35, handlelength=1.2)

    # ── (c) 双目标值 ─────────────────────────────────────────────
    ax_c.bar(x - w / 2, f1, w, color=PALETTE["reward"], alpha=0.85,
             label="F1 收益", edgecolor="white", linewidth=0.4)
    ax_c.bar(x + w / 2, f2, w, color=PALETTE["cost"], alpha=0.85,
             label="F2 代价（km）", edgecolor="white", linewidth=0.4)
    for xi, ok in zip(x, feas):
        if not ok and f1:
            ax_c.annotate("不可行", (xi, max(f1) * 0.96),
                          textcoords="offset points", xytext=(0, 4),
                          ha="center", fontsize=FS["annotation"],
                          color=PALETTE["release"], fontweight=500)
    style_axes(ax_c, title="(c) 双目标值（每周期）", xlabel="滚动周期",
               ylabel="收益 / 代价")
    ax_c.set_xticks(x, cyc_labels)
    ax_c.legend(loc="upper right", fontsize=FS["legend"])

    # ── (d) 关键指标表 ───────────────────────────────────────────
    rows = [
        ("到达 / 完成任务", f"{n_arrived} / {n_done_total}"),
        ("不可达（geo/service）",
         f"{n_unreach}（{int(summary.get('n_unreachable_geo', 0))} / "
         f"{int(summary.get('n_unreachable_service', 0))}）"),
        ("服务暂缓（停机未唤醒）", f"{n_dormant_end}"),
        ("滞留池中（非暂缓）", f"{n_remain_plain}"),
        ("正确执行吞吐（完成/运营周期）", f"{throughput:.3f}"),
        ("正确执行率（逐周期）", f"{correct_rate:.1%}"),
        ("按时率 / 迟到交付", f"{on_time_final:.1%} / {n_late_total}"),
        ("平均迟到", f"{avg_late / 60:.1f} min"),
        ("有效完成率 / 奖励产出率",
         f"{summary.get('effective_completion_rate', 0.0):.1%} / "
         f"{summary.get('reward_yield', 0.0):.1%}"),
        ("废飞 / 已飞航程",
         f"{n_waste_total} / {waste_path_m:.0f} m"),
        ("淘汰（须为 0）", str(n_eliminated_total)),
        ("完成率 η_final（主口径）", f"{completion:.1%}"),
        ("候选池命中率", f"{cand_hit:.1%}"),
        ("门禁救回 / 可装未装", f"{n_gate_rescued} / {n_skip}"),
        ("周期解可行率", f"{feas_rate:.0%}"),
        ("总收益 F1（折减后）", f"{summary.get('total_reward', 0.0):.1f}"),
        ("总路径代价", f"{summary.get('total_cost', 0.0) / 1000:.1f} km"),
        ("平均等待", f"{summary.get('avg_wait', 0.0):.0f} s"),
        ("平均可用机数 K", f"{summary.get('K_mean', summary.get('K_avail_mean', 0.0)):.1f}"),
        ("求解耗时（MUAS）",
         f"{summary.get('solve_time_ms', 0.0) / 1000:.1f} s"),
        # (d) 全表取自 metrics.summary（只记首 seed）；跨 seed 统计在
        # seed_summary / per_seed。旧标注写「N seeds 均值」会把首 seed
        # 数值误报成均值，与「顶层 summary 仅首 seed」的口径冲突。
        ("运行口径", "单 seed（调试）" if n_seeds <= 1 else
         f"首 seed {first_seed}；跨 seed 见 seed_summary"),
    ]
    ax_d.set_axis_off()
    ax_d.set_xlim(0, 1)
    # 单列塞 21 行会让相邻行文字互相压叠；拆成左右两列后行距翻倍，
    # 信息一条不丢。跨 seed 统计仍以 seed_summary / per_seed 为准。
    n_left = (len(rows) + 1) // 2
    ax_d.set_ylim(0, n_left + 1.2)
    ax_d.text(0.0, n_left + 0.55, "(d) 关键指标（首 seed summary）",
              fontsize=FS["title"], fontweight=500, va="center", color="#1f2933")
    for i, (k, v) in enumerate(rows):
        col = 0 if i < n_left else 1
        j = i if col == 0 else i - n_left
        y = n_left - j - 0.5
        x0 = 0.01 if col == 0 else 0.51
        x1 = 0.49 if col == 0 else 0.99
        if j % 2 == 0:
            ax_d.add_patch(plt.Rectangle((x0, y - 0.40), 0.48, 0.80,
                                         facecolor="#f5f6f7", edgecolor="none",
                                         zorder=0))
        warn = (k == "周期解可行率" and feas_rate < 0.999) or \
               (k == "完成率 η_final（主口径）" and completion < 0.05) or \
               (k.startswith("淘汰") and n_eliminated_total > 0) or \
               (k.startswith("门禁救回") and n_skip > 0)
        ax_d.text(x0 + 0.01, y, k, fontsize=FS["tick"] - 1.0, va="center",
                  family=MONO_ZH, color="#3c4148", zorder=2)
        ax_d.text(x1, y, v, fontsize=FS["tick"] - 1.0, va="center", ha="right",
                  family=MONO_ZH,
                  color=PALETTE["release"] if warn else "#0f4c81", zorder=2)

    fig.suptitle("exp01_smoke：结果总览（正确执行 · 终局构成 · 双目标 · 关键指标）",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    footnote(fig, "主指标为吞吐与逐周期执行率；迟到计完成、废飞为物理不可行弹回。完整口径见 PIPELINE.md。")
    return save_figure(fig, Path(out_dir) / "R01_outcome_summary", formats, dpi=dpi)

if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from task_allocation.experiments.exp01_smoke.plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
