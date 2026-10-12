# -*- coding: utf-8 -*-
"""exp01_smoke **图清单**：唯一事实来源（single source of truth）。

新增一张图 = 在 ``FIGURES`` 里加一行。改标题 / 调顺序 / 换分组，都只动这里。

命名与查找规则（四者同名）::

    模块名 ≡ 图 id ≡ 输出文件名 ≡ 绘制函数名去掉 ``plot_``

    plot/process/task_selection.py  → def plot_task_selection  → task_selection.png

即**看到图文件名就能定位代码，看到代码就知道图叫什么**。

分层（按「论文叙事线」划分——读者从设定读到结论的顺序）::

    setup     ① 实验设置：问题规模 / 算法超参 / 约束开关（参数即图）
    initial   ② 初始状态：地形、机巢、初始机队、首批任务（与算法无关）
    process   ③ 求解过程：滚动闭环逐周期「发生了什么」
    report    ④ 结果分析：跨 seed 结论、配置对照（可直接进论文）

每张图另带一个 ``topic`` 二级标签，用于在同一层内部再归类，但不建目录。

## 合并记录（2026-10-12 重组：20 张 → 11 张）

为消除「同一问题被多张图重复回答」的混乱，按四问框架重组，
**面板信息全部保留**（调试期观测不完备等于自断线索）::

    setup_params        新增（原参数只在 config.yaml）
    geo_overview        保留（scene → initial）
    task_selection      ← selection_funnel + selection_detail
    dmde_search         ← convergence + cr_curve（DMDE 交叉变异曲线）
    allocation          ← allocation_gantt + assignment_map + geo_evolution
    task_dynamics       ← task_flow + task_wait + geo_task_lifecycle + cycle_metrics(a)
    fleet_state         ← fleet_utilization + geo_fleet_transition + nest_choice
    constraint_state    ← constraint_audit + time_window
    outcome_summary     新增（论文核心结果图；吸收 cr_curve 旧完成率轨迹 +
                          task_flow(d) 累计进度 + cycle_metrics(b) 双目标）
    seed_summary        ← seed_summary + seed_distribution
    ab_compare          保留
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .initial.geo_overview import plot_geo_overview
from .process.allocation import plot_allocation
from .process.constraint_state import plot_constraint_state
from .process.dmde_search import plot_dmde_search
from .process.fleet_state import plot_fleet_state
from .process.task_dynamics import plot_task_dynamics
from .process.task_selection import plot_task_selection
from .report.ab_compare import plot_ab_compare
from .report.outcome_summary import plot_outcome_summary
from .report.seed_summary import plot_seed_summary
from .setup.setup_params import plot_setup_params

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

#: 四层——论文叙事线（读者从设定读到结论的顺序）
GROUPS: dict[str, str] = {
    "setup": "① 实验设置：问题规模、算法超参、约束开关（参数即图）",
    "initial": "② 初始状态：地形、机巢、初始机队、首批任务（与算法无关）",
    "process": "③ 求解过程：滚动闭环逐周期发生了什么",
    "report": "④ 结果分析：跨 seed 结论与配置对照（可直接进论文）",
}

#: 二级标签——同层内按"看什么"再归类
TOPICS: dict[str, str] = {
    "config": "实验配置快照",
    "spatial": "空间分布与地理演化",
    "allocation": "单周期分配结果",
    "mechanism": "跨周期机制与水位诊断",
    "solver": "求解器内部行为（交叉变异与收敛）",
    "constraint": "约束与时限咬合",
    "fleet": "机队与机巢资源",
    "robustness": "重复实验稳健性",
    "comparison": "跨配置对照",
    "outcome": "总体结果与完成率",
}


@dataclass(frozen=True)
class Figure:
    """一张图的登记信息。

    Attributes:
        id:      图 id，**同时是**模块名与输出文件名（不含扩展名）
        group:   所属层（setup / initial / process / report）
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
    # ── ① setup：实验设了什么 ───────────────────────────────────
    Figure(
        id="setup_params",
        group="setup",
        topic="config",
        title="实验设定总览：问题规模 / DMDE 超参 / 选择机制 / 约束开关 / 滚动设定",
        needs=("metrics", "solution"),
        source="config.yaml 全段 + solution.json 的 nests / tasks / uavs",
        func=plot_setup_params,
    ),
    # ── ② initial：初始状态 ─────────────────────────────────────
    Figure(
        id="geo_overview",
        group="initial",
        topic="config",
        title="初始态势：地形 · 机巢 · 初始机队 · 任务散布",
        needs=("metrics", "solution"),
        source="solution.json 的 dem / nests / tasks / uavs",
        func=plot_geo_overview,
    ),
    # ── ③ process：求解过程（六张，按一个周期里发生的顺序） ──────
    Figure(
        id="task_selection",
        group="process",
        topic="mechanism",
        title="动态任务选择：漏斗逐级收敛 / 保留率 / 能力上限 / 池守恒",
        needs=("metrics",),
        source="metrics.json 的 cycles[].selection 全字段 / muas.n_selected",
        func=plot_task_selection,
    ),
    Figure(
        id="dmde_search",
        group="process",
        topic="solver",
        title="DMDE 搜索机制：动态交叉率 CR（公式 3-9）/ 探索—开发平衡 / 收敛曲线 / 求解开销",
        needs=("solution",),
        source="solution.json 的 cycles[].cost_history（长度−1 即 S）+ config.yaml 的 solver.zeta",
        func=plot_dmde_search,
    ),
    Figure(
        id="allocation",
        group="process",
        topic="allocation",
        title="单周期分配结构：UAV—任务甘特 / 空间分配图 / 逐周期 DEM 演化",
        needs=("metrics", "solution"),
        source="solution.json 的 cycles[].tours + tasks[] / nests[] / dem",
        func=plot_allocation,
    ),
    Figure(
        id="task_dynamics",
        group="process",
        topic="mechanism",
        title="任务命运：池水位 / 流量 / 状态矩阵 / 等待与滞留 / 地理生命周期",
        needs=("metrics", "solution"),
        source="metrics.json 的 cycles[].pool_counts / completed_ids / deferred_ids + solution.json 的 tasks[]",
        func=plot_task_dynamics,
    ),
    Figure(
        id="fleet_state",
        group="process",
        topic="fleet",
        title="机队与机巢：三口径出动 / 占用热力图 / 选巢归因 / 机队迁移",
        needs=("metrics", "solution"),
        source="metrics.json 的 cycles[].muas / nest_occupancy + solution.json 的 tours / nests",
        func=plot_fleet_state,
    ),
    Figure(
        id="constraint_state",
        group="process",
        topic="constraint",
        title="约束与时限：预筛归因 / 约束状态 / 罚项量纲 / 时间窗满足度",
        needs=("metrics", "solution"),
        source="metrics.json 的 cycles[].selection.filter_reasons / muas.{violations,penalty,infeasible_tags} + tasks[].{earliest,latest,completed_at}",
        func=plot_constraint_state,
    ),
    # ── ④ report：结果分析 ──────────────────────────────────────
    Figure(
        id="outcome_summary",
        group="report",
        topic="outcome",
        title="结果总览：完成率 CR 轨迹 / 未完成任务终局构成 / 双目标 / 关键指标",
        needs=("metrics", "solution"),
        source="metrics.json 的 cycles[].pool_counts / completed_ids / summary + per_seed",
        func=plot_outcome_summary,
    ),
    Figure(
        id="seed_summary",
        group="report",
        topic="robustness",
        title="跨 seed 稳健性：三指标均值±σ / 分布箱线 / 逐 seed 演化 / 明细表",
        needs=("metrics",),
        source="metrics.json 的 per_seed / seed_summary / seeds（仅多 seed 运行）",
        func=plot_seed_summary,
    ),
    Figure(
        id="ab_compare",
        group="report",
        topic="comparison",
        title="两组配置对照：完成率 / 收益—代价 / 权衡 / Δ%",
        needs=("metrics",),
        source="results/metrics_<tag>.json 的 per_seed / seed_summary（需 ≥2 组对照）",
        func=plot_ab_compare,
    ),
)


def figure_ids() -> tuple[str, ...]:
    """全部图 id（顺序即生成顺序）。"""
    return tuple(f.id for f in FIGURES)


def group_ids() -> tuple[str, ...]:
    """四层 id（按 setup → initial → process → report 的定义顺序）。"""
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
