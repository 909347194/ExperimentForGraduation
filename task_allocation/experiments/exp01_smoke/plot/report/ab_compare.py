# -*- coding: utf-8 -*-
"""图 ``ab_compare``——两组配置对照：结论能不能站住。

【report 层 · 结论与论文插图】

config 里改动一个开关（本轮是 ``constraints.time_window``），跑同一批 seed，
看结论往哪边倒——这张图把 ``PIPELINE.md`` §3 的对照表格变成可以直接插进
论文的证据：

    (a) 两组**比率类**指标对比：最终完成率 / 周期可行率（同量纲可同轴，带 ±σ）
    (b) 两组**收益—代价**对比：F1 双轴看"多赚 vs 多飞"，配逐 seed 散点
    (c) **权衡散点**：每个 seed 一个点（最终完成率 × 总代价），看两组是否分离
    (d) **相对变化**：以首组为基准的 Δ%，一眼看清每项指标变好还是变坏

读法要点：**代价下降未必是好事**。若某组代价低但最终完成率更低，多半是"少干活"
而非"更省"——(c) 面板的逐 seed 散点就是用来识破这一点的。

需要多份 ``metrics``，故走 ``extras["variants"]`` 通道（见
``plot/__init__.py:discover_variants``）；只有 ``results/`` 下存在归档对照
``metrics_<tag>.json`` 时才出图，否则返回空列表自动跳过。

统一签名之外多一个 ``extras`` 形参——这是唯一需要跨文件数据的图，
其余图签名保持 ``(metrics, solution, out_dir, formats, *, dpi)`` 不变。

数据来源：``results/metrics_<tag>.json`` 的 ``per_seed[].summary`` /
``seed_summary`` / ``summary``。
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

from ..theme import PALETTE, plt, save_figure, style_axes

__all__ = ["plot_ab_compare"]

#: tag → 图例标签（未列出的 tag 用原名）
_LABELS = {
    "tw_off": "A：时间窗关闭",
    "tw_on": "B：时间窗开启 + 惩罚解耦",
}

#: 组配色（最多 4 组）
_GROUP_COLORS = ("#4c78a8", "#e45756", "#54a24b", "#f58518")


def _label(tag: str) -> str:
    return _LABELS.get(tag, tag)


def _series(metrics: dict[str, Any], key: str) -> list[float]:
    """取该组每个 seed 的某个标量（优先 per_seed，退回单 seed summary）。"""
    per_seed = metrics.get("per_seed") or {}
    out: list[float] = []
    for row in per_seed.values():
        v = (row.get("summary") or {}).get(key)
        if isinstance(v, (int, float)) and np.isfinite(float(v)):
            out.append(float(v))
    if not out:
        v = (metrics.get("summary") or {}).get(key)
        if isinstance(v, (int, float)) and np.isfinite(float(v)):
            out.append(float(v))
    return out


def _mean_std(vals: Sequence[float]) -> tuple[float, float]:
    if not vals:
        return 0.0, 0.0
    arr = np.asarray(vals, dtype=float)
    return float(arr.mean()), float(arr.std(ddof=0))


def plot_ab_compare(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
    extras: dict[str, Any] | None = None,
) -> list[Path]:
    """绘制两组（或多组）配置的对照图。"""
    variants = (extras or {}).get("variants") or {}
    if len(variants) < 2:
        return []

    tags = sorted(variants)[:4]
    groups = [(t, variants[t]) for t in tags]
    n = len(groups)
    colors = _GROUP_COLORS[:n]

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.2), layout="constrained")
    ((ax_rate, ax_obj), (ax_trade, ax_delta)) = axes

    labels_short = [chr(ord("A") + i) for i in range(n)]
    x = np.arange(n)
    bar_w = 0.36

    # ── (a) 比率类指标：完成率 / 周期可行率 ───────────────────────
    rate_keys = [("completion_rate_final", "最终完成率"), ("cycle_feasible_rate", "周期解可行率")]
    offsets = [-bar_w / 2, bar_w / 2]
    for i, (key, name) in enumerate(rate_keys):
        means, stds = [], []
        for _, met in groups:
            vals = [v * 100.0 for v in _series(met, key)]
            me, sd = _mean_std(vals)
            means.append(me)
            stds.append(sd)
        cols = [PALETTE["selected"], PALETTE["executed"]][i]
        bars = ax_rate.bar(
            x + offsets[i], means, bar_w, yerr=stds, capsize=4,
            label=name, color=cols, edgecolor="white", linewidth=0.6,
            error_kw={"elinewidth": 1.0, "ecolor": "0.35"},
        )
        for b, me, sd in zip(bars, means, stds):
            ax_rate.text(
                b.get_x() + b.get_width() / 2, me + sd + 2.0, f"{me:.1f}%",
                ha="center", va="bottom", fontsize=8, color="0.25",
            )
    ax_rate.set_xticks(x, [_label(t) for t, _ in groups])
    ax_rate.set_ylim(0, 124)
    style_axes(ax_rate, title="(a) 比率类指标（mean ± σ，%）", ylabel="%")
    ax_rate.legend(loc="upper center", ncol=2, framealpha=0.9)

    # ── (b) 收益 vs 代价（双轴）─────────────────────────────────
    rewards = [_series(met, "total_reward") for _, met in groups]
    costs = [[c / 1000.0 for c in _series(met, "total_cost")] for _, met in groups]
    rew_mean, rew_std = zip(*[_mean_std(v) for v in rewards])
    cost_mean, cost_std = zip(*[_mean_std(v) for v in costs])

    b1 = ax_obj.bar(
        x - bar_w / 2, rew_mean, bar_w, yerr=rew_std, capsize=4,
        label="总收益 F1", color=PALETTE["reward"], edgecolor="white", linewidth=0.6,
        error_kw={"elinewidth": 1.0, "ecolor": "0.35"},
    )
    ax_obj.set_ylabel("总收益 F1", color=PALETTE["reward"])
    ax_obj.tick_params(axis="y", labelcolor=PALETTE["reward"])

    ax_obj_r = ax_obj.twinx()
    b2 = ax_obj_r.bar(
        x + bar_w / 2, cost_mean, bar_w, yerr=cost_std, capsize=4,
        label="总路径代价", color=PALETTE["cost"], edgecolor="white", linewidth=0.6,
        error_kw={"elinewidth": 1.0, "ecolor": "0.35"},
    )
    ax_obj_r.set_ylabel("总路径代价 (km)", color=PALETTE["cost"])
    ax_obj_r.tick_params(axis="y", labelcolor=PALETTE["cost"])
    ax_obj_r.grid(False)
    style_axes(ax_obj, title="(b) 收益—代价对比（mean ± σ）")
    ax_obj.set_xticks(x, labels_short)
    ax_obj.legend([b1, b2], ["总收益 F1", "总路径代价"], loc="upper right", ncol=1)

    # ── (c) 权衡散点：每 seed 一个点 ─────────────────────────────
    rng = np.random.default_rng(7)
    for i, (tag, met) in enumerate(groups):
        rs = [v * 100.0 for v in _series(met, "completion_rate_final")]
        cs = [c / 1000.0 for c in _series(met, "total_cost")]
        if not rs or not cs:
            continue
        m = min(len(rs), len(cs))
        jitter = rng.uniform(-0.12, 0.12, m)
        ax_trade.scatter(
            rs[:m], cs[:m], s=64, color=colors[i], alpha=0.85,
            edgecolor="white", linewidth=0.8, label=_label(tag),
            zorder=3,
        )
        if m > 1:
            ax_trade.scatter(
                [float(np.mean(rs[:m]))], [float(np.mean(cs[:m]))],
                marker="X", s=180, color=colors[i], edgecolor="0.25",
                linewidth=1.0, zorder=4,
            )
    style_axes(
        ax_trade, title="(c) 权衡：最终完成率 × 总代价（× 为组内均值）",
        xlabel="最终完成率 (%)", ylabel="总路径代价 (km)",
    )
    ax_trade.legend(loc="upper left", framealpha=0.9)

    # ── (d) 相对变化 Δ%（以首组为基准）───────────────────────────
    delta_keys = [
        ("completion_rate_final", "最终完成率", +1),
        ("total_reward", "总收益", +1),
        ("total_cost", "总代价", -1),
        ("avg_wait", "平均等待", -1),
        ("n_completed", "完成任务数", +1),
    ]
    base = groups[0][1]
    y_pos = np.arange(len(delta_keys))
    handles = []
    for i, (tag, met) in enumerate(groups[1:], start=1):
        deltas: list[float] = []
        for key, _, _ in delta_keys:
            b_vals = _series(base, key)
            v_vals = _series(met, key)
            bm, _ = _mean_std(b_vals)
            vm, _ = _mean_std(v_vals)
            deltas.append(((vm - bm) / bm * 100.0) if bm else 0.0)
        offs = (i - 1 - (n - 2) / 2) * bar_w
        bars = ax_delta.barh(
            y_pos + offs, deltas, bar_w * 0.92, color=colors[i],
            label=f"{labels_short[i]} 相对 {labels_short[0]}", edgecolor="white",
            linewidth=0.5,
        )
        handles.append(bars)
        for b, dd in zip(bars, deltas):
            ax_delta.text(
                dd + (2.5 if dd >= 0 else -2.5), b.get_y() + b.get_height() / 2,
                f"{dd:+.0f}%", va="center",
                ha="left" if dd >= 0 else "right", fontsize=8, color="0.25",
            )
    ax_delta.axvline(0, color="0.4", linewidth=0.9)
    ax_delta.set_yticks(y_pos, [name for _, name, _ in delta_keys])
    style_axes(ax_delta, title="(d) 相对变化 Δ%（须与方向一起读）", xlabel="Δ %")
    if handles:
        ax_delta.legend(loc="lower right", framealpha=0.9)
    lim = max(abs(v) for v in ax_delta.get_xlim()) * 1.25
    ax_delta.set_xlim(-lim, lim)

    fig.suptitle(
        "两组配置对照：" + " vs ".join(_label(t) for t, _ in groups),
        fontsize=12.5,
    )
    legend_note = "　".join(f"{l} = {_label(t)}" for l, (t, _) in zip(labels_short, groups))
    fig.text(0.5, -0.008, legend_note, ha="center", fontsize=8.5, color="0.35")
    fig.text(
        0.5, -0.034,
        "代价下降需与最终完成率同读——只看代价下降会把“少干活”误判为“更省”。",
        ha="center", fontsize=8.5, color="0.35",
    )

    return save_figure(fig, Path(out_dir) / "R03_ab_compare", formats, dpi=dpi)

if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from task_allocation.experiments.exp01_smoke.plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
