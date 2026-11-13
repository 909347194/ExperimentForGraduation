# -*- coding: utf-8 -*-
"""exp02_selection_scale **图清单**：唯一事实来源（single source of truth）。

新增一张图 = 在 ``FIGURES`` 里加一行。改标题 / 调顺序 / 换分组，都只动这里。

职责边界：本实验是**单周期任务规模可扩展性台**，六张图只描述
「规模 → 压缩 / 分段墙钟 / 现算边数 / 任务去向」的观测，
不产出方法或档位优劣、质量高低、标定选型结论。见 ``notes.md``。

命名与查找规则（四者同名，文件名另带「分类+编号」前缀）::

    模块名 ≡ 图 id ≡ 绘制函数名去掉 ``plot_``；输出文件名 = 分类编号前缀 + 图 id
    分类：S=setup(①) / R=report(④)，组内序号即叙事序
    例：plot/report/r01_tradeoff.py → plot_r01_tradeoff → R01_tradeoff.png

即**看到图文件名就能定位代码，看到代码就知道图叫什么**，且文件名按
``S/R + 两位序号`` 排序，figures 目录天然有序不乱。

分层（本实验只有两层，原因：单周期变体扫描，无逐周期过程数据）::

    setup     ① 实验设置：问题规模 / 档位矩阵 / 求解预算（参数即图）
    report    ④ 结果分析：规模压缩、分段墙钟与任务去向（描述性观测）

与 exp01_smoke 四层的对应关系：本实验没有「初始态势」（无 solution.json）
与「求解过程」（无逐周期 cycles），故 initial/process 两层整体缺位；
若将来补场景图件，从 exp01_smoke/plot/ 复制骨架即可。

## 六张图与职责的对应

    setup_params       实验设定总览（口径基准）
    r01_tradeoff       规模—耗时曲线：N × 档位的单周期墙钟（论文可扩展性主图）
    r02_compression    压缩是否发生、选择数是否随 N 解耦
    r03_time_breakdown 分段计时与现算边数随 N 的变化
    r04_bottleneck     名额族候选规模与选择数封顶（结构观察，不作选型）
    r05_capability     能力档位阶梯的选择规模、墙钟与任务去向
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .report.r01_tradeoff import plot_r01_tradeoff
from .report.r02_compression import plot_r02_compression
from .report.r03_time_breakdown import plot_r03_time_breakdown
from .report.r04_bottleneck import plot_r04_bottleneck
from .report.r05_capability import plot_r05_capability
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

#: 两层——论文叙事线（本实验无 initial/process，见模块顶说明）
GROUPS: dict[str, str] = {
    "setup": "① 实验设置：问题规模、档位矩阵、求解预算（参数即图）",
    "report": "④ 结果分析：规模压缩、分段墙钟与任务去向（描述性观测）",
}

#: 二级标签——同层内按"看什么"再归类
TOPICS: dict[str, str] = {
    "config": "实验配置快照",
    "scalability": "规模—耗时曲线",
    "compression": "规模压缩与任务去向",
    "timing": "分段计时与现算边数",
    "structure": "选择规模的结构观察",
}

#: 输出文件名「分类+编号」前缀映射（单一事实来源；与 FIGURES 顺序一致）。
#: 分类：S=setup / R=report；组内序号 = 叙事顺序。
#: 各 plot_* 模块里的保存文件名已同步为 ``{前缀}{id}``，本字典只用于文档与排查。
FIGURE_STEMS_PREFIX: dict[str, str] = {
    "setup_params":     "S01",
    "r01_tradeoff":     "R01",
    "r02_compression":  "R02",
    "r03_time_breakdown": "R03",
    "r04_bottleneck":   "R04",
    "r05_capability":   "R05",
}


@dataclass(frozen=True)
class Figure:
    """一张图的登记信息。

    Attributes:
        id:      图 id，**同时是**模块名与输出文件名（不含扩展名）
        group:   所属层（setup / report）
        topic:   二级标签，见 ``TOPICS``
        title:   中文短标题，供图注与日志使用
        needs:   绘制所需产物，``("metrics",)``（本实验无 solution.json）
        source:  数据来源说明（写到文档里，便于写论文图注）
        func:    绘制函数，统一签名 ``(metrics, solution, out_dir, formats, *, dpi)``
        animated: 是否为动画图（本实验暂无；保留字段以备扩展）
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
    # ── ① setup：实验设了什么 ───────────────────────────────────
    Figure(
        id="setup_params",
        group="setup",
        topic="config",
        title="实验设定总览：场景 / 档位矩阵 / 选择机制 / 求解预算",
        needs=("metrics",),
        source="config.yaml 全段 + metrics.json 的 scales[].selection 字段",
        func=plot_setup_params,
    ),
    # ── ④ report：结果分析（五张，按叙事序；全部描述性） ─────────────
    Figure(
        id="r01_tradeoff",
        group="report",
        topic="scalability",
        title="规模—耗时曲线：N × 档位的单周期端到端墙钟与任务去向",
        needs=("metrics",),
        source="metrics.json 的 scales[]（n_tasks / variant / t_total_ms / accounting）",
        func=plot_r01_tradeoff,
    ),
    Figure(
        id="r02_compression",
        group="report",
        topic="compression",
        title="规模压缩：候选数 / 选择数 / 交付数 / 未完成数随 N 的演化",
        needs=("metrics",),
        source="metrics.json 的 scales[].selection.{n_tasks_in,capability_n_cand,n_selected} + accounting.{n_done,n_remain}",
        func=plot_r02_compression,
    ),
    Figure(
        id="r03_time_breakdown",
        group="report",
        topic="timing",
        title="分段计时与惰性边：分段墙钟和现算边数的规模对照",
        needs=("metrics",),
        source="metrics.json 的 scales[].t_{hint,selection,build,muas}_ms + lazy_edges",
        func=plot_r03_time_breakdown,
    ),
    Figure(
        id="r04_bottleneck",
        group="report",
        topic="structure",
        title="结构观察：名额族候选规模与选择数封顶",
        needs=("metrics",),
        source="metrics.json 的 scales[]（quota_a2.5 vs quota_a5 同 sel 对照 + selection.capability_n_sel）",
        func=plot_r04_bottleneck,
    ),
    Figure(
        id="r05_capability",
        group="report",
        topic="structure",
        title="档位观察：cap_x* 阶梯的选择规模、墙钟与任务去向",
        needs=("metrics",),
        source="metrics.json 的 scales[]（cap_x* 阶梯的 n_sel / t_muas / accounting）",
        func=plot_r05_capability,
    ),
)


def figure_ids() -> tuple[str, ...]:
    """全部图 id（顺序即生成顺序）。"""
    return tuple(f.id for f in FIGURES)


def group_ids() -> tuple[str, ...]:
    """两层 id（按 setup → report 的定义顺序）。"""
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
