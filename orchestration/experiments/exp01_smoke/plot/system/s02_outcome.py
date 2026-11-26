# -*- coding: utf-8 -*-
"""S02 系统结果（TA+PP）：终局分账 · 机队实测负荷 · 审计。

【系统层 · 完整 TA+PP 闭环 · 结果相位 · 不带上层/下层标签】

1×3 三格，一屏回答"这一轮编排最后跑成了什么"：

(a) **任务终局**：每个周期新派的任务最后怎么了——当期飞掉 / 之后补飞 /
    派出但没飞成（回池）/ 从未派出。四段互斥、每周期合计等于该周期投放数。
    当期与补飞分开着色，是为了让"某航次失败、下周期补回几个"在柱子上看得见，
    而不是被一个完成率数字抹平。
(b) **机队实测负荷**：每架机的航次数、站数、**实测**航程。整场没起飞的机画成 0
    并用灰字标注——闲置是审计项之一，不能在结果图里消失。注意这里用实测里程，
    分配侧的估计里程在 TA02；两处刻意不合并。
(c) **审计**：八条契约逐条判定。哪条不过、为什么，直接写在图里。

原来那张 2×2 的"结果总览"把 (c) 闭环账也塞进来，等于把跨层反馈混进结果图：
闭环过程已单独成 S01（事实回灌 + 回池→补飞），本图只留"结果"。
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from .. import theme as th
from ..base import scene as sc
from ..base import ledger as iv

NAME = "S02_outcome"

_GREEN = "#2E7D4F"
_GREEN_LIGHT = "#A8CDB4"
_ORANGE = "#D68910"
_GRAY = "#D0D0D0"

_CRITERIA_ZH = {
    "require_all_tickets_ok": "全部航次成功",
    "require_gate_ok": "交付净空门禁通过",
    "require_reassignment": "失败任务被改派",
    "require_full_completion": "任务全部完成",
    "require_endpoint_compliant": "端点高度/净空合规",
    "require_no_idle_with_pending": "无「闲置机 + 未飞任务」并存",
    "require_estimate_within_tol": "估计偏差在容差内",
    "require_failure_detail": "失败航次留下逐段诊断",
}

_MODEL_ZH = {"short-range": "短航程", "long-range": "长航程"}


def build(ctx: sc.Ctx, *, dpi: int | None = None) -> list[Path]:
    import matplotlib.pyplot as plt
    import numpy as np

    inv = iv.build(ctx)
    m = ctx.metrics
    audit = ctx.audit
    cycles = list(inv.cycles)

    # 画布按内容定：三栏分别是 30 个任务的分账、6 架机的负荷、8 条契约开关，
    # 原来 14.6 in 宽 + 5.7 in 高时 (c) 的下半幅与右缘全是空的，白占版面。
    fig = plt.figure(figsize=(13.2, 5.0))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.05, 1.10, 0.95], left=0.05, right=0.985,
                          top=0.775, bottom=0.195, wspace=0.28)
    ax_a, ax_b, ax_c = [fig.add_subplot(gs[0, k]) for k in range(3)]

    # ── (a) 任务终局（按出生周期分账） ──────────────────────────
    x = np.arange(len(cycles))
    on_time, later, failed, never = [], [], [], []
    for cyc in cycles:
        born = [t for t in inv.tasks if t.cycle == cyc]
        n_same = sum(1 for t in born if inv.arrived_cycle(t.id) == cyc)
        n_later = sum(1 for t in born
                      if inv.arrived_cycle(t.id) is not None and inv.arrived_cycle(t.id) != cyc)
        n_fail = sum(1 for t in born if inv.final_state(t.id) == iv.FAILED)
        n_never = sum(1 for t in born if inv.final_state(t.id) == iv.POOL)
        on_time.append(n_same)
        later.append(n_later)
        failed.append(n_fail)
        never.append(n_never)

    w = 0.52
    ax_a.bar(x, on_time, w, color=_GREEN, edgecolor="white", lw=0.5, label="当期到达")
    ax_a.bar(x, later, w, bottom=on_time, color=_GREEN_LIGHT, edgecolor="white", lw=0.5,
             label="后续周期补飞到达")
    ax_a.bar(x, failed, w, bottom=np.add(on_time, later), color=_ORANGE,
             edgecolor="white", lw=0.5, label="派出但未飞成（已回池）")
    ax_a.bar(x, never, w, bottom=np.add(np.add(on_time, later), failed), color=_GRAY,
             edgecolor="white", lw=0.5, label="从未派出")
    # 文字颜色按**底色深浅**定，不按数值：深绿/橙底用白字，浅绿/灰底用深字
    for xi, (o, l, f, n) in enumerate(zip(on_time, later, failed, never)):
        for val, base, txt in ((o, 0, "white"), (l, o, "#22303A"),
                               (f, o + l, "white"), (n, o + l + f, "#22303A")):
            if val:
                ax_a.text(xi, base + val / 2, str(val), ha="center", va="center",
                          fontsize=9.6, color=txt, fontweight="bold")
    ax_a.set_xticks(x, [f"周期 {c} 新派" for c in cycles])
    # 每周期投放多少任务由清单决定（单周期 30 个），柱高上限按实际值给，
    # 并在顶部留 1/3 余量放四项图例——写死 11.8 是按旧的"每周期 10 个"算的。
    n_max = max((len([t for t in inv.tasks if t.cycle == c]) for c in cycles), default=1)
    ax_a.set_ylim(0, max(n_max, 1) * 1.34)
    ax_a.set_ylabel("任务数")
    ax_a.set_title(f"(a) 任务终局：{m['flown_tasks']}/{m['task_total']} 到达"
                   f"（{m['completion_rate']:.1%}）", fontsize=12.5, pad=8)
    ax_a.legend(fontsize=8.8, ncol=2, loc="upper center", frameon=False)
    for side in ("top", "right"):
        ax_a.spines[side].set_visible(False)

    # ── (b) 机队实测负荷 ────────────────────────────────────────
    meta = sc.uav_meta()
    fleet = sorted(meta)
    tours_by = {u: [t for t in inv.tours if t.uav == u] for u in fleet}
    km = [sum(t.path_len_m for t in tours_by[u]) / 1000.0 for u in fleet]
    yy = np.arange(len(fleet))
    colors = [th.UAV_COLOR.get(u, "#444444") if tours_by[u] else "#C8C8C8" for u in fleet]
    ax_b.barh(yy, km, 0.55, color=colors, edgecolor="white", lw=0.5)
    for i, u in enumerate(fleet):
        ts = tours_by[u]
        n_stop = sum(t.n_stops for t in ts)
        if ts:
            label = f"{len(ts)} 航次 · {n_stop} 站 · {km[i]:.2f} km"
        else:
            label = "0 航次（全程闲置）"
        ax_b.text(max(km[i], 0.05) + 0.12, i, label, va="center", fontsize=8.8,
                  color="#333333" if ts else th.MUTED)
    ax_b.set_yticks(yy, [f"机{u}\n{_MODEL_ZH.get(meta[u]['model'], meta[u]['model'])}"
                         f"·巢{meta[u]['nest_id']}" for u in fleet], fontsize=8.8)
    ax_b.invert_yaxis()
    ax_b.set_xlim(0, max(km) * 1.72 if max(km) > 0 else 1)
    ax_b.set_xlabel("累计实测航程（km）")
    ax_b.set_title("(b) 机队负荷：谁真的飞了", fontsize=12.5, pad=8)
    ax_b.grid(axis="x", alpha=0.22)
    for side in ("top", "right", "left"):
        ax_b.spines[side].set_visible(False)

    # ── (c) 审计 ───────────────────────────────────────────────
    ax_c.set_axis_off()
    ax_c.set_xlim(0, 1)
    rows = [(k, _CRITERIA_ZH.get(k, k), bool(v)) for k, v in (audit.get("criteria") or {}).items()]
    problems = audit.get("problems") or []
    n_bad = len(problems)
    # 没有问题行时不留空槽：原 -5.8 的下界是给"审计问题"预留的，会把 8 行挤到上半幅。
    ax_c.set_ylim((-5.8 if problems else -0.45), len(rows) + 2.2)
    n_on = sum(1 for r in rows if r[2])
    ax_c.text(0.0, len(rows) + 1.45,
              f"(c) 审计：{'通过' if audit.get('passed') else '未通过'}"
              f"（{n_bad} 条问题；8 条契约里启用 {n_on} 条）",
              fontsize=12.5, fontweight="bold", va="center",
              color=_GREEN if audit.get("passed") else th.FAIL)
    # criteria 只记录「这条检查有没有被要求」，不是判定结果；判定 = problems 非空。
    ax_c.text(0.0, len(rows) + 0.72, "契约开关（启用 = 这一轮真查）",
              fontsize=8.4, va="center", color=th.MUTED)
    if not rows:
        ax_c.text(0.0, 0.6, "本轮 audit.json 未记录契约开关", fontsize=9.4, va="center",
                  color=th.MUTED)
    for i, (key, zh, v) in enumerate(rows):
        y = len(rows) - i - 0.5
        if i % 2 == 0:
            ax_c.add_patch(plt.Rectangle((0, y - 0.42), 1.0, 0.84, facecolor="#F4F5F6",
                                         edgecolor="none", zorder=0))
        # ✓/✗ 在 Microsoft YaHei 里缺字（会画成方块），改用状态方块。
        # 文字不用浅灰：300 dpi 缩到论文单栏后，浅灰字会整片糊掉（曾整列看不见）。
        ax_c.plot([0.022], [y], marker="s", ms=7.5,
                  color="#3c4148" if v else "#B9BEC4", zorder=2)
        ax_c.text(0.065, y, zh, fontsize=9.4, va="center", color="#33383D", zorder=2)
        ax_c.text(0.985, y, "已启用" if v else "未启用", fontsize=9.0, va="center",
                  ha="right", color="#3c4148" if v else "#7C8389", zorder=2)
    # 审计文本已在 support/audit.py 用 T# 写（同一口径），图上照抄，不再二次改号
    if problems:
        body = "\n".join(textwrap.fill(f"· {p}", 34) for p in problems)
        ax_c.text(0.0, -0.35, f"审计问题（{n_bad} 条，判定就靠这里）：",
                  fontsize=9.2, va="top", color=th.FAIL, fontweight="bold")
        ax_c.text(0.0, -1.00, body, fontsize=8.4, va="top", color=th.FAIL, linespacing=1.6)

    # 副标题只放本轮真有的读数：指纹缺失（合成 run）时不要留个孤零零的「任务指纹 ·」
    sha = str(m.get("tasks_sha256") or "")[:12]
    head = [f"航次 {m['tickets_ok']}/{m['tickets_total']} 成功",
            f"重解 {m.get('total_resolves', 0)} 次",
            f"不可飞边 {m.get('infeasible_edges_written', 0)} 条"]
    th.fingerprint("系统结果（TA+PP）：终局分账 · 机队实测负荷 · 审计",
                  ("指纹 " + sha + " · " if sha else "") + " · ".join(head), y=0.975)
    n_fail = len([t for t in inv.tours if not t.ok])
    th.footnote(fig, "「到达」= 有真实航迹；「派出未到达」= 航次整条无解、任务回池；"
                     "「从未派出」= 全程未进航次"
                     + (f"；(b) 未交付 {n_fail} 航次不计入柱长" if n_fail else ""),
                y=0.010)
    return th.save(fig, ctx.fig_dir / NAME, dpi=dpi)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(th.run_script(NAME, sys.argv[1:]))
