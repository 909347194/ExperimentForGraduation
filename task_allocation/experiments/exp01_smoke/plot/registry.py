# -*- coding: utf-8 -*-
"""exp01_smoke **图清单**：唯一事实来源（single source of truth）。

新增一张图 = 在 ``FIGURES`` 里加一行。改标题 / 调顺序 / 换分组，都只动这里。

命名与查找规则（四者同名，文件名另带「分类+编号」前缀）::

    模块名 ≡ 图 id ≡ 绘制函数名去掉 ``plot_``；输出文件名 = 分类编号前缀 + 图 id
    分类：S=setup(①) / I=initial(②) / P=process(③) / R=report(④)，组内序号即叙事序
    例：plot/process/allocation_anim.py → plot_allocation_anim → P06_allocation_anim.gif

即**看到图文件名就能定位代码，看到代码就知道图叫什么**，且文件名按
``S/I/P/R + 两位序号`` 排序，figures 目录天然有序不乱。裁剪后编号不重排
（P06 等旧编号被报告与论文引用，改名即断引用）。

分层（按「论文叙事线」划分——读者从设定读到结论的顺序）::

    initial   ② 初始状态：地形、机巢、初始机队、首批任务（与算法无关）
    process   ③ 求解过程：滚动闭环逐周期「发生了什么」
    report    ④ 结果分析：总体结果（可直接进论文）

每张图另带一个 ``topic`` 二级标签，用于在同一层内部再归类，但不建目录。

## 合并与裁剪记录

* 2026-10-12 重组：20 张 → 11 张（四问框架合并，面板信息保留）。
* 2026-10-26 裁剪：16 张 → **3 张**（用户裁定「图有点过多，只保留必要的：
  初始环境、过程、结果；gif 只保留 P06_allocation_anim.gif」）::

      I01_geo_overview      初始环境
      P04_allocation_gantt  过程（甘特图，2026-10-26 追加保留「保持原样」）
      P06_allocation_anim   过程（GIF，唯一保留的动图）
      R01_outcome_summary   结果

  其余图（setup 参数面板、selection/dmde/allocation 系列静态图、
  task_dynamics/fleet_state/constraint_state、seed_summary/ab_compare、
  P07/P08 动图）从清单与代码一并移除；P06 的末帧海报仍随动画产出
  （poster，供 PDF 静态插图用）。需要恢复时从 git 历史找回对应模块再加回
  一行 FIGURES 即可。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .initial.geo_overview import plot_geo_overview
from .process.allocation import plot_allocation
from .process.allocation_gantt import plot_allocation_gantt
from .process.allocation_anim import plot_allocation_anim
from .report.outcome_summary import plot_outcome_summary

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

#: 三层——论文叙事线（读者从设定读到结论的顺序）
GROUPS: dict[str, str] = {
    "initial": "① 初始状态：地形、机巢、初始机队、首批任务（与算法无关）",
    "process": "② 求解过程：滚动闭环逐周期发生了什么",
    "report": "③ 结果分析：总体结果（可直接进论文）",
}

#: 二级标签——同层内按"看什么"再归类
TOPICS: dict[str, str] = {
    "config": "实验配置快照",
    "allocation": "单周期分配结果",
    "outcome": "总体结果与完成率",
}

#: 输出文件名「分类+编号」前缀映射（单一事实来源；与 FIGURES 顺序一致）。
#: 分类：S=setup / I=initial / P=process / R=report；组内序号 = 叙事顺序。
#: 编号沿用裁剪前的全局序号（P06 不重编为 P01），保持报告/论文引用稳定；
#: 各 plot_* 模块里的保存文件名已同步为 ``{前缀}{id}``（见各文件 ``gif_name``
#: / ``stem``），本字典只用于文档与排查，不参与出图。
FIGURE_STEMS_PREFIX: dict[str, str] = {
    "geo_overview":     "I01",
    "allocation":       "P03",
    "allocation_gantt": "P04",
    "allocation_anim":  "P06",
    "outcome_summary":  "R01",
}


@dataclass(frozen=True)
class Figure:
    """一张图的登记信息。

    Attributes:
        id:      图 id，**同时是**模块名与输出文件名（不含扩展名）
        group:   所属层（initial / process / report）
        topic:   二级标签，见 ``TOPICS``
        title:   中文短标题，供图注与日志使用
        needs:   绘制所需产物，``("metrics",)`` 或 ``("metrics", "solution")``
        source:  数据来源说明（写到文档里，便于写论文图注）
        func:    绘制函数，统一签名 ``(metrics, solution, out_dir, formats, *, dpi)``
        animated: 是否为动画图。只有动画图接受 ``gif``/``mp4`` 格式；
                  ``make_plots`` 会把动图格式从静态图的 ``formats`` 里剔除，
                  避免「补 gif 刷新动画」时给每张静态图都落一个单帧 GIF。
    """

    id: str
    group: str
    topic: str
    title: str
    needs: tuple[str, ...]
    source: str
    func: Callable[..., list]
    animated: bool = False


FIGURES: tuple[Figure, ...] = (
    # ── ① initial：初始状态 ─────────────────────────────────────
    Figure(
        id="geo_overview",
        group="initial",
        topic="config",
        title="初始态势：背景（建筑 / 地形）· 机巢 · 初始机队 · 任务散布",
        needs=("metrics", "solution"),
        source="solution.json 的 dem / nests / tasks / uavs + data/<scenario>/building/*.shp"
               "（背景层规则：有建筑优先画建筑，只有地形才画 DEM）",
        func=plot_geo_overview,
    ),
    # ── ② process：求解过程 ────────────────────────────────────
    Figure(
        id="allocation",
        group="process",
        topic="allocation",
        title="分配结构与逐周期空间演化：甘特航迹条 + 背景底图逐周期演化",
        needs=("metrics", "solution"),
        source="solution.json 的 cycles[].tours（est_cost）+ tasks[] / nests[] / dem"
               "（背景层：有建筑画建筑、只有地形画 DEM；逐周期面板只画有真实航次的"
               "活跃周期，未执行任务只标已到达者，与 allocation_anim 口径一致）",
        func=plot_allocation,
    ),
    Figure(
        id="allocation_gantt",
        group="process",
        topic="allocation",
        title="分配航迹条：线长 ∝ 真实航程 · 方块=任务 · 两端起降巢",
        needs=("metrics", "solution"),
        source="solution.json 的 cycles[].tours（est_cost）+ tasks[] / nests[]（线长按 est_cost 标定，段间距按三维几何）",
        func=plot_allocation_gantt,
    ),
    Figure(
        id="allocation_anim",
        group="process",
        topic="allocation",
        title="分配过程动画演变：滚动闭环航迹生长 / 任务点亮 + 当前周期 DMDE 收敛（GIF）",
        needs=("metrics", "solution"),
        source="solution.json 的 cycles[].tours + cost_history + tasks[] / nests[] / dem"
               "（地图背景：有建筑画建筑、只有地形画 DEM）；左右两栏——地图下方的画布级状态行（周期/已到达/已完成百分比/里程，白底随文字自动贴合；置于图外因图内任何角落都会压住贴顶任务标号），右栏当前周期收敛曲线；完成进度只用百分比数字表达，不画进度条",
        func=plot_allocation_anim,
        animated=True,
    ),
    # ── ③ report：结果分析 ──────────────────────────────────────
    Figure(
        id="outcome_summary",
        group="report",
        topic="outcome",
        title="结果总览：完成率 CR 轨迹 / 未完成任务终局构成 / 双目标 / 关键指标",
        needs=("metrics", "solution"),
        source="metrics.json 的 cycles[].pool_counts / completed_ids / summary + per_seed",
        func=plot_outcome_summary,
    ),
)


def figure_ids() -> tuple[str, ...]:
    """全部图 id（顺序即生成顺序）。"""
    return tuple(f.id for f in FIGURES)


def group_ids() -> tuple[str, ...]:
    """各层 id（按 initial → process → report 的定义顺序）。"""
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
