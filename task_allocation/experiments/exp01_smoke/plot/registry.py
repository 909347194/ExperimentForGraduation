# -*- coding: utf-8 -*-
"""exp01_smoke **图清单**：唯一事实来源（single source of truth）。

新增一张图 = 在 ``FIGURES`` 里加一行。改标题 / 调顺序 / 换分组，都只动这里。

命名与查找规则（三层结构的关键约定）::

    模块名 ≡ 图 id ≡ 输出文件名 ≡ 绘制函数名去掉 ``plot_``

    plot/scene/geo_overview.py        → def plot_geo_overview   → geo_overview.png
    plot/process/geo_evolution.py     → def plot_geo_evolution  → geo_evolution.png
    plot/report/seed_summary.py       → def plot_seed_summary   → seed_summary.png

即**看到图文件名就能定位代码，看到代码就知道图叫什么**，不再有
``cycles.py`` 产出 ``cycle_metrics.png`` 这类需要猜的错位。

分层（按「看图目的」划分，而不是按实现技术）::

    scene    实验长什么样——一次性、与算法无关的环境与初始配置
    process  实验怎么跑的——滚动闭环的逐周期观测、演化与诊断
    report   实验说明什么——可直接进论文的结论图（稳健性 / 对照）

每张图另带一个 ``topic`` 二级标签，用于在同一层内部再归类（例如 process 层里
区分"空间演化"与"数值诊断"），但不建目录——避免目录层级深过头。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .process.allocation_gantt import plot_allocation_gantt
from .process.assignment_map import plot_assignment_map
from .process.constraint_audit import plot_constraint_audit
from .process.convergence import plot_convergence
from .process.cycle_metrics import plot_cycle_metrics
from .process.fleet_utilization import plot_fleet_utilization
from .process.geo_evolution import plot_geo_evolution
from .process.geo_fleet_transition import plot_geo_fleet_transition
from .process.geo_task_lifecycle import plot_geo_task_lifecycle
from .process.nest_choice import plot_nest_choice
from .process.selection_detail import plot_selection_detail
from .process.selection_funnel import plot_selection_funnel
from .process.task_flow import plot_task_flow
from .process.task_wait import plot_task_wait
from .process.time_window import plot_time_window
from .report.ab_compare import plot_ab_compare
from .report.seed_distribution import plot_seed_distribution
from .report.seed_summary import plot_seed_summary
from .scene.geo_overview import plot_geo_overview

__all__ = [
    "Figure",
    "FIGURES",
    "GROUPS",
    "TOPICS",
    "figure_ids",
    "group_ids",
    "describe",
    "resolve",
]

#: 三层——看图目的
GROUPS: dict[str, str] = {
    "scene": "环境与初始配置：地形、机巢、初始机队、任务散布（与算法无关）",
    "process": "运行过程观测：滚动闭环逐周期的结构、数值与空间演化",
    "report": "结论与论文插图：跨 seed 稳健性、配置对照等可直接引用的成图",
}

#: 二级标签——同层内按"看什么"再归类
TOPICS: dict[str, str] = {
    "config": "实验配置快照",
    "spatial": "空间分布与地理演化",
    "allocation": "单周期分配结果",
    "mechanism": "跨周期机制与水位诊断",
    "solver": "求解器内部行为",
    "constraint": "约束与时限咬合",
    "fleet": "机队与机巢资源",
    "robustness": "重复实验稳健性",
    "comparison": "跨配置对照",
}


@dataclass(frozen=True)
class Figure:
    """一张图的登记信息。

    Attributes:
        id:      图 id，**同时是**模块名与输出文件名（不含扩展名）
        group:   所属层（scene / process / report）
        topic:   二级标签，见 ``TOPICS``
        title:   中文短标题，供图注与日志使用
        needs:   绘制所需产物，``("metrics",)`` 或 ``("metrics", "solution")``
        source:  数据来源说明（写到文档里，便于写论文图注）
        func:    绘制函数，统一签名 ``(metrics, solution, out_dir, formats, *, dpi)``
    """

    id: str
    group: str
    topic: str
    title: str
    needs: tuple[str, ...]
    source: str
    func: Callable[..., list]


FIGURES: tuple[Figure, ...] = (
    # ── scene：环境长什么样 ──────────────────────────────────────
    Figure(
        id="geo_overview",
        group="scene",
        topic="config",
        title="初始态势：地形 · 机巢 · 初始机队 · 任务散布",
        needs=("metrics", "solution"),
        source="solution.json 的 dem / nests / tasks / uavs",
        func=plot_geo_overview,
    ),
    # ── process：滚动闭环怎么跑 ──────────────────────────────────
    Figure(
        id="cycle_metrics",
        group="process",
        topic="mechanism",
        title="周期指标面板：任务池水位 / 双目标 / 泊位占用 / 求解开销",
        needs=("metrics",),
        source="metrics.json 的 cycles[] / problem / nests",
        func=plot_cycle_metrics,
    ),
    Figure(
        id="task_flow",
        group="process",
        topic="mechanism",
        title="任务状态随周期变化：存量 / 流量 / 个体轨迹 / 累计进度",
        needs=("metrics", "solution"),
        source="metrics.json 的 cycles[] + solution.json 的 tasks[]",
        func=plot_task_flow,
    ),
    Figure(
        id="task_wait",
        group="process",
        topic="mechanism",
        title="任务等待与滞留：每任务等待 / 批次倒挂 / 滞留轮次 / 窗口余量",
        needs=("metrics", "solution"),
        source="solution.json 的 tasks[] + metrics.json 的 cycles[].deferred_ids",
        func=plot_task_wait,
    ),
    Figure(
        id="selection_funnel",
        group="process",
        topic="mechanism",
        title="动态任务选择漏斗：候选如何被逐级收敛到 T_sel",
        needs=("metrics",),
        source="metrics.json 的 cycles[].selection / cycles[].muas",
        func=plot_selection_funnel,
    ),
    Figure(
        id="selection_detail",
        group="process",
        topic="mechanism",
        title="选择机制细节：预选参数是否咬合 / 池守恒 / 强制入选",
        needs=("metrics",),
        source="metrics.json 的 cycles[].selection 全字段 + muas.n_selected",
        func=plot_selection_detail,
    ),
    Figure(
        id="fleet_utilization",
        group="process",
        topic="fleet",
        title="机队利用率与机巢压力：可用 / 编码参与 / 实际出动三口径",
        needs=("metrics", "solution"),
        source="metrics.json 的 cycles[].muas / nest_occupancy / fleet.stats + solution.json 的 tours / nests",
        func=plot_fleet_utilization,
    ),
    Figure(
        id="nest_choice",
        group="process",
        topic="fleet",
        title="机巢选择归因：按成本（距离）选定，容量全程不咬合",
        needs=("metrics", "solution"),
        source="solution.json 的 nests / tasks / cycles[].tours + metrics.json 的 cycles[].nest_occupancy",
        func=plot_nest_choice,
    ),
    Figure(
        id="time_window",
        group="process",
        topic="constraint",
        title="时间窗满足度：窗口在哪、完成在哪、余量多少",
        needs=("metrics", "solution"),
        source="solution.json 的 tasks[].{earliest,latest,completed_at} + metrics.json 周期边界",
        func=plot_time_window,
    ),
    Figure(
        id="constraint_audit",
        group="process",
        topic="constraint",
        title="约束审计：预筛拦截 / 可行状态 / 罚项量纲 / 闸门归因",
        needs=("metrics",),
        source="metrics.json 的 cycles[].selection.filter_reasons / muas.{violations,penalty} / infeasible_tags",
        func=plot_constraint_audit,
    ),
    Figure(
        id="allocation_gantt",
        group="process",
        topic="allocation",
        title="UAV—任务 分配甘特图：谁做什么、什么顺序、降哪个巢",
        needs=("metrics", "solution"),
        source="solution.json 的 cycles[].tours + tasks[] / nests[]",
        func=plot_allocation_gantt,
    ),
    Figure(
        id="assignment_map",
        group="process",
        topic="allocation",
        title="机巢—任务—航次空间分配图：这件事发生在哪",
        needs=("metrics", "solution"),
        source="solution.json 的 nests / tasks / cycles[].tours",
        func=plot_assignment_map,
    ),
    Figure(
        id="geo_evolution",
        group="process",
        topic="spatial",
        title="逐周期空间演化：航次与分配的全过程回放",
        needs=("metrics", "solution"),
        source="solution.json 的 dem / cycles[].tours + metrics.json 的计数",
        func=plot_geo_evolution,
    ),
    Figure(
        id="geo_task_lifecycle",
        group="process",
        topic="spatial",
        title="任务生命周期：谁到了、谁在等、谁做完了",
        needs=("metrics", "solution"),
        source="metrics.json 的 completed_ids / deferred_ids + solution.json 的 tasks[]",
        func=plot_geo_task_lifecycle,
    ),
    Figure(
        id="geo_fleet_transition",
        group="process",
        topic="spatial",
        title="机队迁移与周期末机巢占用",
        needs=("metrics", "solution"),
        source="metrics.json 的 cycles[].nest_occupancy + solution.json 的 tours",
        func=plot_geo_fleet_transition,
    ),
    Figure(
        id="convergence",
        group="process",
        topic="solver",
        title="DMDE 收敛曲线：每代最优适应度与收敛速度",
        needs=("solution",),
        source="solution.json 的 cycles[].cost_history",
        func=plot_convergence,
    ),
    # ── report：结论说明什么 ─────────────────────────────────────
    Figure(
        id="ab_compare",
        group="report",
        topic="comparison",
        title="两组配置对照：完成率 / 收益—代价 / 权衡 / Δ%",
        needs=("metrics",),
        source="results/metrics_<tag>.json 的 per_seed / seed_summary（需 ≥2 组对照）",
        func=plot_ab_compare,
    ),
    Figure(
        id="seed_summary",
        group="report",
        topic="robustness",
        title="多 seed 复现一致性：完成率 / 总代价 / 总收益 的均值±σ",
        needs=("metrics",),
        source="metrics.json 的 per_seed / seed_summary / seeds（仅多 seed 运行）",
        func=plot_seed_summary,
    ),
    Figure(
        id="seed_distribution",
        group="report",
        topic="robustness",
        title="跨 seed 分布：全部指标的个体散点 + 逐 seed 演化 + 明细表",
        needs=("metrics",),
        source="metrics.json 的 per_seed / seed_summary / seeds（仅多 seed 运行）",
        func=plot_seed_distribution,
    ),
)


def figure_ids() -> tuple[str, ...]:
    """全部图 id（顺序即生成顺序）。"""
    return tuple(f.id for f in FIGURES)


def group_ids() -> tuple[str, ...]:
    """三层 id（按 scene → process → report 的定义顺序）。"""
    return tuple(GROUPS)


def _by_id() -> dict[str, Figure]:
    return {f.id: f for f in FIGURES}


def resolve(
    *,
    names: Iterable[str] | None = None,
    groups: Iterable[str] | None = None,
) -> list[Figure]:
    """按图名 / 分层筛选图清单，返回仍按 ``FIGURES`` 原序排列的结果。

    ``names`` 给出时按名字精确匹配（顺序以清单为准，方便固定出图次序）；
    ``groups`` 给出时取该层全部图；两者都省略则返回全部。未知名字或层会报
    ``ValueError``，避免"拼错文件名却静默少出一张图"。
    """
    table = _by_id()
    if names is not None:
        wanted = [str(n) for n in names]
        unknown = [n for n in wanted if n not in table]
        if unknown:
            raise ValueError(f"未知图名 {unknown}；可用：{list(table)}")
        picked = {n: table[n] for n in wanted}
        return [f for f in FIGURES if f.id in picked]
    if groups is not None:
        asked = [str(g) for g in groups]
        unknown = [g for g in asked if g not in GROUPS]
        if unknown:
            raise ValueError(f"未知分层 {unknown}；可用：{list(GROUPS)}")
        return [f for f in FIGURES if f.group in asked]
    return list(FIGURES)


def describe() -> str:
    """生成一张 Markdown 表，用于文档（PIPELINE.md / notes.md）直接粘贴。"""
    lines = [
        "| 层 | 图 id | 说明 | 数据来源 |",
        "|---|---|---|---|",
    ]
    for fig in FIGURES:
        lines.append(
            f"| {fig.group} | `{fig.id}` | {fig.title} | `{fig.source}` |"
        )
    return "\n".join(lines)


def needs_solution(figures: Sequence[Figure]) -> bool:
    """这批图是否需要 ``solution.json``（不需要时可跳过加载大文件）。"""
    return any("solution" in f.needs for f in figures)
