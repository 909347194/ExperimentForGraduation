# -*- coding: utf-8 -*-
"""图 ``setup_params``——实验设定总览：参数即图。

【setup 层 · 实验设置】

回答「实验设了多大问题、扫了哪些变体、求解预算多少」。
读者不翻 ``config.yaml`` 就能判断实验设定；也是全部报告图的口径基准
（例如看懂 r04_bottleneck 需要知道 max_ratio·K，看懂 r05_capability
需要知道 budget_scale 与 cap(t) 的定义）。

三联面板（等宽字体参数表，与 exp01 S01 同风格；本实验无地图小图——
单周期扫描没有场景空间图件）::

    (a) 场景与问题参数      K / 机巢 / 航程 / 任务规模与生成 / 种子
    (b) 变体矩阵            两个标定族的名额与能力参数（intersect / primary）
    (c) 求解预算与口径      DMDE 超参 / polish 预算 / 距离与适应度口径

数据来源：同目录 ``config.yaml`` + ``metrics.json`` 的 ``scales[].selection``。
"""

from __future__ import annotations

# ── 独立运行自举：支持直接 `python plot/.../xxx.py`（脚本方式）──
if __name__ == "__main__" and __package__ in (None, ""):
    import sys, runpy
    from pathlib import Path as _P
    _here = _P(__file__).resolve()
    _root = _here.parents[2]   # 实验根 = plot 的上两级（使 `plot` 包可导入）
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    _rel = _here.relative_to(_root / "plot")
    _mod = "plot." + ".".join(_rel.with_suffix("").parts)
    runpy.run_module(_mod, run_name="__main__", alter_sys=True)
    raise SystemExit(0)


from pathlib import Path
from typing import Any, Sequence

from ..theme import FIG_WIDE, FS, MONO_ZH, plt, save_figure

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
        return "—"
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

    problem = cfg.get("problem") or {}
    tasks_cfg = cfg.get("tasks") or {}
    solver = cfg.get("solver") or {}
    stage = cfg.get("stage") or {}
    sel_cfg = cfg.get("selection") or {}
    variants = list(cfg.get("variants") or [])

    scales = [int(s) for s in (cfg.get("scales") or [])]
    k_avail = int(metrics.get("K", metrics.get("K_avail", problem.get("n_uavs", 0))))
    max_range_km = float(metrics.get("uav_max_range_m", 0.0)) / 1000.0

    # (a) 场景与问题参数
    rows_a = [
        ("可用无人机 K", f"{k_avail} 架"),
        ("机巢数 / 容量", f"{problem.get('n_nests', '—')} 个 / 每巢 {problem.get('nest_capacity', '—')} 架"),
        ("满电航程", f"{max_range_km:g} km（每周期末充电恢复）"),
        ("任务规模 N", " / ".join(str(s) for s in scales)),
        ("任务撒点", f"{tasks_cfg.get('domain', 'nest_anchored')}，spread={float(tasks_cfg.get('spread_m', 0))/1000:g} km"),
        ("奖励范围", f"{tasks_cfg.get('reward_min', '—')} ~ {tasks_cfg.get('reward_max', '—')}"),
        ("时间窗", f"{float(tasks_cfg.get('deadline_min', 0))/3600:g} ~ {float(tasks_cfg.get('deadline_max', 0))/3600:g} h"),
        ("随机种子", str(cfg.get("experiment", {}).get("seed", "—"))),
    ]

    # (b) 变体矩阵
    rows_b: list[tuple[str, str]] = []
    for v in variants:
        name = str(v.get("name", "?"))
        cap_d = v.get("capability") or {}
        mode = str(cap_d.get("mode", "intersect"))
        bs = cap_d.get("budget_scale")
        alpha = v.get("alpha")
        fam = "名额族" if str(name).startswith("quota") else (
            "能力族" if str(name).startswith("cap") else "对照")
        rows_b.append((
            name,
            f"{fam}｜α={_fmt(alpha)}｜{mode}｜budget_scale={_fmt(bs)}",
        ))
    if not rows_b:
        rows_b = [("（config 未定义 variants）", "—")]

    # (c) 求解预算与口径
    rows_c = [
        ("DMDE", f"pop={solver.get('pop_size', '—')} × gens={solver.get('max_generations', '—')}"),
        ("polish", f"≤{stage.get('polish_max_rounds', '—')} 轮 / ≤{stage.get('polish_max_evals', '—')} 评估"),
        ("选择机制", f"优先级→n_cand 预筛→可行性→边际收益；α={_fmt(sel_cfg.get('alpha'))}"),
        ("capability", "cap(t)=下取整(budget_scale·B/c)，c=最近机代价中位数"),
        ("距离口径", "垂直切面 DEM 三维航程（全链路程统一 CostProvider）"),
        ("适应度", "四层准字典序：硬违约→收益→迟到→航程"),
        ("全量对照", f"N ∈ {sorted(cfg.get('run', {}).get('full_muas_baseline_scales') or [])}"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(FIG_WIDE, 4.6))
    _draw_param_table(axes[0], "(a) 场景与问题参数", rows_a)
    _draw_param_table(axes[1], "(b) 变体矩阵（两个标定族）", rows_b)
    _draw_param_table(axes[2], "(c) 求解预算与口径", rows_c)
    fig.suptitle(
        f"exp02_selection_scale 实验设定：K={k_avail}，N ∈ {scales}，"
        f"{len(variants)} 个变体",
        fontsize=FS["suptitle"],
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))

    stem = Path(out_dir) / "S01_setup_params"
    return save_figure(fig, stem, formats, dpi=dpi)


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
