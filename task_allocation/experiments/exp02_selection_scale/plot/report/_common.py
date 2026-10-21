# -*- coding: utf-8 -*-
"""report 层共享数据件：metrics.json ``scales`` 列表的读取与变体元数据。

本实验的全部数值产物是**单份** ``metrics.json`` 的 ``scales`` 列表
（N × variant 矩阵，每行一格运行）。本模块把「行 → 字段」的提取、
变体显示名 / 配色 / 机型标记、按 N 分组收拢成一处，五张报告图共用，
避免每个模块各自遍历字典、字段名写错却只在出图时才暴露。

放入 ``report/`` 而非 ``base/``：它依赖实验的 metrics 语义（变体名、
selection 诊断字段），不是通用绘制件。
"""

from __future__ import annotations

from typing import Any, Iterable

# ── 变体元数据（顺序即图例/绘图顺序）────────────────────────────────
VARIANT_ORDER: tuple[str, ...] = (
    "quota_a2.5", "quota_a5", "cap_x0.25", "cap_x0.5", "cap_x1", "full_baseline",
)

#: 变体显示名（图例 / 轴刻度 / 标注统一用）
VARIANT_LABEL: dict[str, str] = {
    "quota_a2.5": "名额 α=2.5",
    "quota_a5": "名额 α=5",
    "cap_x0.25": "能力 ×0.25",
    "cap_x0.5": "能力 ×0.5",
    "cap_x1": "能力 ×1.0",
    "full_baseline": "全量对照",
}

#: 变体配色（Tableau 10 派生，与 theme.PALETTE 同族；色盲友好）
VARIANT_COLOR: dict[str, str] = {
    "quota_a2.5": "#4c78a8",
    "quota_a5": "#b279a2",
    "cap_x0.25": "#f58518",
    "cap_x0.5": "#72b7b2",
    "cap_x1": "#54a24b",
    "full_baseline": "#e45756",
}

#: 任务规模 N → 散点标记（同图内用颜色分变体、形状分规模）
N_MARKER: dict[int, str] = {50: "o", 100: "s", 200: "^"}

#: 变体族（用于「族内平均线」等族级样式）
VARIANT_FAMILY: dict[str, str] = {
    "quota_a2.5": "quota",
    "quota_a5": "quota",
    "cap_x0.25": "cap",
    "cap_x0.5": "cap",
    "cap_x1": "cap",
    "full_baseline": "full",
}


def rows_of(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """metrics 的 ``scales`` 列表（缺省空表）。"""
    return list(metrics.get("scales") or [])


def grouped(metrics: dict[str, Any]) -> dict[int, dict[str, dict[str, Any]]]:
    """``{N: {variant: row}}`` 两级索引（full_baseline 也在其中）。"""
    out: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows_of(metrics):
        out.setdefault(int(row["n_tasks"]), {})[str(row["variant"])] = row
    return out


def sel_of(row: dict[str, Any]) -> dict[str, Any]:
    """一阶段选择诊断（full_baseline 行为空 dict，调用方需容忍）。"""
    return row.get("selection") or {}


def muas_of(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("muas") or {}


def f1(row: dict[str, Any]) -> float:
    return float(muas_of(row).get("f1_reward", 0.0))


def f2_km(row: dict[str, Any]) -> float:
    return float(muas_of(row).get("f2_cost", 0.0)) / 1000.0


def t_muas_s(row: dict[str, Any]) -> float:
    return float(row.get("t_muas_ms", 0.0)) / 1000.0


def t_total_s(row: dict[str, Any]) -> float:
    return float(row.get("t_total_ms", 0.0)) / 1000.0


def t_hint_s(row: dict[str, Any]) -> float:
    return float(row.get("t_hint_ms", 0.0)) / 1000.0


def t_selection_s(row: dict[str, Any]) -> float:
    return float(row.get("t_selection_ms", 0.0)) / 1000.0


def t_build_s(row: dict[str, Any]) -> float:
    return float(row.get("t_build_ms", 0.0)) / 1000.0


def n_candidate(row: dict[str, Any]) -> int | None:
    """候选任务数 n_cand（优先级预筛后规模；full_baseline 为 None）。

    2026-10-21 符号统一：诊断键 capability_preselect_k → capability_n_cand；
    旧存档 json 回退读旧键，保证可重渲染。
    """
    sel = sel_of(row)
    v = sel.get("capability_n_cand", sel.get("capability_preselect_k"))
    return None if v is None else int(v)


def sel_n(row: dict[str, Any]) -> int:
    """最终参与 MUAS 的任务数（full_baseline = N）。"""
    return int(row.get("n_selected", 0))


def exec_n(row: dict[str, Any]) -> int:
    return int(muas_of(row).get("n_executed", 0))


def edges(row: dict[str, Any]) -> int:
    """惰性表实际现算边数（full_baseline 为全量矩阵边数）。"""
    return int(row.get("lazy_edges", 0))


def cap_t(row: dict[str, Any]) -> int | None:
    """capability_cap（机队能力上限 cap(t)）；full_baseline 为 None。"""
    v = sel_of(row).get("capability_cap")
    return None if v is None else int(v)


def n_sel_cap(row: dict[str, Any]) -> int | None:
    """选择数封顶值 n_sel = ⌈max_ratio·K⌉（intersect 口径下真正的瓶颈）。

    2026-10-21 符号统一：诊断键 capability_marginal_limit → capability_n_sel；
    旧存档 json 回退读旧键，保证可重渲染。
    """
    sel = sel_of(row)
    v = sel.get("capability_n_sel", sel.get("capability_marginal_limit"))
    return None if v is None else int(v)


def compression(row: dict[str, Any]) -> float:
    return float(row.get("compression_ratio", 1.0))


def variants_present(metrics: dict[str, Any]) -> list[str]:
    """按 VARIANT_ORDER 过滤出实际出现的变体（保序）。"""
    seen = {str(r["variant"]) for r in rows_of(metrics)}
    return [v for v in VARIANT_ORDER if v in seen]


def scales_present(metrics: dict[str, Any]) -> list[int]:
    """实际出现的 N（升序）。"""
    return sorted({int(r["n_tasks"]) for r in rows_of(metrics)})


def label(variant: str) -> str:
    return VARIANT_LABEL.get(variant, variant)


def color(variant: str) -> str:
    return VARIANT_COLOR.get(variant, "#9aa5b1")


def marker(n_tasks: int) -> str:
    return N_MARKER.get(int(n_tasks), "o")


def _fmt_ms(v: float) -> str:
    """毫秒 → 人类可读（图注 / 标注用）。"""
    if v >= 1000.0:
        return f"{v / 1000.0:.1f} s"
    return f"{v:.0f} ms"


def iter_variant_rows(
    metrics: dict[str, Any],
    variants: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """按 (N, variant) 字典序产举行—— grouped() 的保序展平。"""
    g = grouped(metrics)
    want = list(variants) if variants is not None else variants_present(metrics)
    out: list[dict[str, Any]] = []
    for n in sorted(g):
        for v in want:
            row = g[n].get(v)
            if row is not None:
                out.append(row)
    return out
