# -*- coding: utf-8 -*-
"""图 ``setup_params``——实验设定总览：参数即图。

【setup 层 · 实验设置】

回答 Q1「实验设了多大问题、什么算法超参、哪些约束开着」。
读者不翻 ``config.yaml`` 就能判断实验设定；也是全部过程图的口径基准
（例如看懂 dmde_search 的 CR 曲线需要知道 S 与 ζ，看懂 constraint_state
需要知道哪些约束开着、罚权多少）。

五联面板：

    (a) 问题与场景参数      规模 / 机队 / 任务生成 / 滚动设定
    (b) 算法与机制参数      DMDE 超参 / 两阶段选择 / MUAS 双目标
    (c) 约束开关矩阵        11 类约束的开合与惩罚权重
    (d) 时间窗标定          任务窗口长度分布 vs 典型航时（尺度同量级才构成选择性压力）
    (e) 场景缩略            小地图：DEM + 机巢 + 首批任务（与 geo_overview 同源）

数据来源：同目录 ``config.yaml`` + ``solution.json`` 的 tasks / nests / dem。
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


import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..base.dem import _draw_dem
from ..base.geo import _draw_nests, _set_scene_lim, _task_size
from ..theme import FIG_WIDE, FS, MONO_ZH, PALETTE, footnote, plt, save_figure, style_axes

__all__ = ["plot_setup_params"]


def _load_config(out_dir: Path) -> dict[str, Any]:
    """读实验目录下的 config.yaml；失败返回空 dict（各面板自行降级）。"""
    cfg = Path(out_dir).resolve().parent.parent / "config.yaml"
    try:
        import yaml

        return yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _fmt(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "开" if v else "关"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _draw_param_table(ax, title: str, rows: list[tuple[str, str]]) -> None:
    """等宽字体参数表（两列：参数 / 值），行交替浅底色。"""
    ax.set_axis_off()
    n = len(rows)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, n + 1.2)
    ax.text(0.0, n + 0.55, title, fontsize=FS["title"], fontweight=500,
            va="center", color="#1f2933")
    for i, (k, v) in enumerate(rows):
        y = n - i - 0.5
        if i % 2 == 0:
            ax.add_patch(plt.Rectangle((0, y - 0.42), 1, 0.84,
                                       facecolor="#f5f6f7", edgecolor="none",
                                       zorder=0))
        ax.text(0.015, y, k, fontsize=FS["tick"], va="center",
                family=MONO_ZH, color="#3c4148", zorder=2)
        ax.text(0.985, y, v, fontsize=FS["tick"], va="center", ha="right",
                family=MONO_ZH, color="#0f4c81", zorder=2)
    ax.plot([0, 1], [n + 0.1, n + 0.1], color="#d0d5da", lw=0.8)


def plot_setup_params(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    cfg = _load_config(out_dir)
    if not cfg:
        return []

    nests = list(solution.get("nests") or [])
    tasks = list(solution.get("tasks") or [])
    if not nests:
        return []

    problem = cfg.get("problem") or {}
    tasks_cfg = cfg.get("tasks") or {}
    sel = cfg.get("selection") or {}
    muas = cfg.get("muas") or {}
    solver = cfg.get("solver") or {}
    rolling = cfg.get("rolling") or {}
    cons = cfg.get("constraints") or {}
    cost = cfg.get("cost") or {}
    arrivals = (rolling.get("arrivals") or {}).get("counts") or []
    weights = sel.get("weights") or {}

    fig = plt.figure(figsize=(FIG_WIDE, 6.0))
    gs = fig.add_gridspec(
        2, 3, width_ratios=[1.0, 1.0, 1.0], height_ratios=[1.0, 0.92],
        hspace=0.38, wspace=0.22,
    )
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[0, 2])
    ax_d = fig.add_subplot(gs[1, 0])
    ax_e = fig.add_subplot(gs[1, 1:])

    # ── (a) 问题与场景 ───────────────────────────────────────────
    rows_a = [
        ("机巢数 × 容量", f"{_fmt(problem.get('n_nests'))} × {_fmt(problem.get('nest_capacity'))} 泊位"),
        ("机队规模", f"{_fmt(problem.get('n_uavs'))} 架"),
        ("巡航速度 / 满电航程", f"{_fmt(problem.get('uav_speed'))} m/s / "
                          f"{float(problem.get('uav_max_range', 0)) / 1000:g} km"),
        ("任务批次", f"首批 {_fmt(tasks_cfg.get('n_tasks'))} 个 + 每周期 {arrivals}"),
        ("窗口长度", f"{_fmt(tasks_cfg.get('deadline_cycles_min'))}~"
                    f"{_fmt(tasks_cfg.get('deadline_cycles_max'))} 周期"),
        ("收益范围", f"{_fmt(tasks_cfg.get('reward_min'))}~{_fmt(tasks_cfg.get('reward_max'))}"),
        ("滚动设定", f"{_fmt(rolling.get('n_cycles'))} 周期 × "
                    f"{float(rolling.get('cycle_length', 0)):g} s"),
        ("完成闸门 / 充电", f"{_fmt(rolling.get('completion_feasibility_gate'))} / "
                        f"{_fmt(rolling.get('recharge_at_nest'))}"),
        ("多 seed", f"{len(cfg.get('experiment', {}).get('seeds') or [])} 个"),
    ]
    _draw_param_table(ax_a, "(a) 问题与场景", rows_a)

    # ── (b) 算法与机制 ───────────────────────────────────────────
    rows_b = [
        ("DMDE 种群 / 代数", f"{_fmt(solver.get('population'))} / {_fmt(solver.get('max_iter'))}"),
        ("曲率指数 ζ / 灭绝 δ", f"{_fmt(solver.get('zeta'))} / {_fmt(solver.get('delta'))}"),
        ("预筛系数 α", _fmt(sel.get("alpha"))),
        ("五权重 r/u/w/s/c", " / ".join(
            _fmt(weights.get(k)) for k in ("w_r", "w_u", "w_w", "w_s", "w_c"))),
        ("名额倍数", _fmt((sel.get("marginal") or {}).get("max_ratio"))),
        ("双目标口径", "字典序" if muas.get("lexicographic") else "加权和"),
        ("罚权 λ_c / w_v / w_t", " / ".join(
            _fmt(muas.get(k)) for k in ("lambda_cost", "w_violation", "w_time"))),
        ("Lamarckian 回写", _fmt(muas.get("lamarckian"))),
        ("DEM 步长 / 离地", f"{_fmt(cost.get('dem_step_m'))} m / "
                        f"{_fmt(cost.get('min_clearance'))} m"),
    ]
    _draw_param_table(ax_b, "(b) 算法与机制", rows_b)

    # ── (c) 约束开关矩阵 ─────────────────────────────────────────
    ax_c.set_axis_off()
    switch_rows = [(k, v) for k, v in cons.items()
                   if isinstance(v, bool)]
    weight_rows = {k: v for k, v in cons.items()
                   if not isinstance(v, bool)}
    ax_c.set_xlim(0, 1)
    ax_c.set_ylim(0, len(switch_rows) + 2.2)
    ax_c.text(0.0, len(switch_rows) + 1.6, "(c) 约束开关（11 类）",
              fontsize=FS["title"], fontweight=500, va="center", color="#1f2933")
    for i, (name, on) in enumerate(switch_rows):
        y = len(switch_rows) - i - 0.3
        if i % 2 == 0:
            ax_c.add_patch(plt.Rectangle((0.0, y - 0.32), 1.0, 0.64,
                                         facecolor="#f5f6f7", edgecolor="none",
                                         zorder=0))
        ax_c.text(0.02, y, ("✓ " if on else "✗ ") + name, fontsize=FS["tick"],
                  va="center", family=MONO_ZH,
                  color=PALETTE["executed"] if on else "#b6bcc4", zorder=3)
        w = weight_rows.get(f"w_{name}") or weight_rows.get(f"w_{name.replace('end_nest_', '')}")
        if w is not None:
            ax_c.text(0.99, y, f"w={_fmt(w)}", fontsize=FS["tick"], va="center",
                      ha="right", family=MONO_ZH, color="#3c4148", zorder=3)

    # ── (d) 时间窗标定：窗口长度 vs 典型航时 ─────────────────────
    speed = float(problem.get("uav_speed") or 15.0)
    if tasks:
        wins = [float(t["latest"]) - float(t["earliest"]) for t in tasks
                if t.get("latest") is not None and t.get("earliest") is not None]
        # 典型航时：任务到最近机巢的直线距离 / 巡航速度（3D 直线代理）
        taus = []
        for t in tasks:
            if t.get("x") is None:
                continue
            d = min(math.dist((float(t["x"]), float(t["y"]), float(t.get("z", 0.0))),
                              (float(n["x"]), float(n["y"]), float(n.get("z", 0.0))))
                    for n in nests)
            taus.append(d / speed)
    else:
        wins, taus = [], []
    if wins:
        bins = np.linspace(0, max(wins) * 1.05, 13)
        ax_d.hist(wins, bins=bins, color=PALETTE["new"], alpha=0.85,
                  edgecolor="white", linewidth=0.5)
        med = float(np.median(taus)) if taus else float("nan")
        p90 = float(np.percentile(taus, 90)) if taus else float("nan")
        if med == med:
            ax_d.axvline(med, color=PALETTE["cost"], lw=1.4, ls="--")
            ax_d.annotate(f"中位航时 {med:.0f}s", (med, ax_d.get_ylim()[1]),
                          textcoords="offset points", xytext=(4, -4),
                          fontsize=FS["annotation"], color=PALETTE["cost"],
                          va="top")
        if p90 == p90:
            ax_d.axvline(p90, color="#b279a2", lw=1.4, ls="--")
            ax_d.annotate(f"p90 航时 {p90:.0f}s", (p90, ax_d.get_ylim()[1]),
                          textcoords="offset points", xytext=(-4, -22),
                          fontsize=FS["annotation"], color="#b279a2",
                          va="top", ha="right")
        style_axes(ax_d, title="(d) 时间窗标定：窗口长度 vs 典型航时",
                   xlabel="任务窗口长度（s）", ylabel="任务数")
    else:
        style_axes(ax_d, title="(d) 时间窗标定")
        ax_d.text(0.5, 0.5, "无任务时间窗数据", transform=ax_d.transAxes,
                  ha="center", va="center", fontsize=FS["base"], color="0.5")

    # ── (e) 场景缩略 ─────────────────────────────────────────────
    sm = _draw_dem(ax_e, solution.get("dem"), solution)
    if tasks:
        first_cycle = [t for t in tasks if float(t.get("arrival_time", 0)) < 1e-9]
        shown = first_cycle or tasks
        ax_e.scatter(
            [float(t["x"]) for t in shown], [float(t["y"]) for t in shown],
            s=[_task_size(t["reward"]) for t in shown],
            color="#9aa5b1", edgecolors="white", linewidths=0.6, zorder=6,
            label=f"首批任务 {len(shown)}",
        )
    _draw_nests(ax_e, nests)
    _set_scene_lim(ax_e, nests, tasks, [], solution.get("dem"))
    ax_e.set_aspect("equal", adjustable="box")
    from ..base.geo import _strip_axes
    _strip_axes(ax_e)
    ax_e.set_title("(e) 场景缩略（DEM + 机巢 + 首批任务）",
                   fontsize=FS["title"])
    ax_e.legend(loc="upper right", fontsize=FS["legend"], frameon=True,
                framealpha=0.92, edgecolor="none")

    fig.suptitle("exp01_smoke：实验设定（config.yaml 即图）",
                 fontsize=FS["suptitle"])
    footnote(fig, "口径：关 = 既不判可行也不计惩罚（消融口径）；(d) 窗口与航时同量级"
                  "→ 时间窗构成真实的选择性压力。")
    return save_figure(fig, Path(out_dir) / "S01_setup_params", formats, dpi=dpi)

if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
