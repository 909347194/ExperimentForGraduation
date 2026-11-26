# -*- coding: utf-8 -*-
"""exp01_smoke **图清单**：唯一事实来源（新增一张图 = 在这里加一行）。

这张实验的图按「**这张图在给谁记账**」分三族，文件名前缀就是族号：

======  ==========================================================
前缀    这一族在回答什么
======  ==========================================================
``I``   **输入/设定**（不带 TA/PP 标签）：任务池、机巢、机队怎么摆的。
        与算法无关——换任何求解器，这几张图一模一样。
``TA``  **上层 task allocation 自己的账**：任务派给谁、第几站、每航次估计多少、
        机位/巢位负载、谁闲置、任务回没回池。**只看分配就已经定了**的东西。
``PP``  **下层 path planning 自己的账**：每航次真飞成什么形状、点的先后、
        周期内时序、高度与离地净空、断在哪一段。**只有求解之后才知道**的东西。
``S``   **整个系统**（TA+PP 闭环，不带上层/下层标签）：失败如何被剔除与吸收、
        失败如何被吸收成补飞、终局完成率与审计。单个模块回答不了的问题。
======  ==========================================================

为什么这么分：写论文时"第 4 章 上层分配"要引 TA 图、"第 5 章 下层规划"要引 PP 图、
"第 6 章 闭环编排"要引 S 图；评审问"你这张图到底证明了哪一层"，文件名就能答。
混在一起的图（例如原来 R01 一张图里既有分配终局、又有航程估计偏差、又有审计）
必须拆开：它们各自属于不同章节。

叙事顺序（``FIGURES`` 的次序 = 读者从设定读到结论的顺序）::

    I01  任务池长什么样
    TA01 每个任务被谁、第几站、哪个周期执行（分配账）
    TA02 每条航次装了什么、估计多少、机位/巢位负载（分配结构）
    PP01 每周期真飞成什么形状、断在哪一段（航迹几何）
    PP02 周期内怎么排、谁整周期没动（时序）
    PP03 沿航程怎么爬、离地多少（垂直剖面）
    S01  跨层闭环：事实回灌 + 回池→补飞（系统过程）
    S02  终局分账 + 机队实测负荷 + 审计（系统结果）

命名约定（四者同名）::

    模块文件名 ≡ 图 id（小写） ≡ 落盘文件名（大写前缀版本）
    例：plot/pp/pp01_cycle_tracks.py → id "PP01_cycle_tracks"
        → 落盘 figures/PP01_cycle_tracks.png

**标题/图注/读法/待澄清也在本文件**：这里的 ``title`` / ``caption`` / ``reading`` /
``clarify`` 会被 ``plot/captions.py`` 生成 ``figures/CAPTIONS.md``。图内只留标题、
图例和一行脚注，长解释一律搬到那份文档——版面留给几何与时序。

``--group`` 可选 ``initial / ta / pp / system``；``--only`` 可用 id 前缀（``pp``、``PP01``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .initial.i01_task_pool import build as _b_i01
from .ta.ta01_task_assignment import build as _b_ta01
from .ta.ta02_alloc_load import build as _b_ta02
from .pp.pp01_cycle_tracks import build as _b_pp01
from .pp.pp02_tour_timing import build as _b_pp02
from .pp.pp03_altitude_profile import build as _b_pp03
from .system.s01_loop_closure import build as _b_s01
from .system.s02_outcome import build as _b_s02

__all__ = [
    "Figure", "FIGURES", "GROUPS", "OWNERS", "figure_ids", "group_ids",
    "by_id", "resolve", "describe",
]

#: 四层——每层一种「账」，不是按绘制技术分（matplotlib 在四层里都一样）
GROUPS: dict[str, str] = {
    "initial": "① 输入/设定：任务池、机巢、机队（与算法无关）",
    "ta": "② 上层 task allocation：分配账（派给谁 / 第几站 / 估计多少）",
    "pp": "③ 下层 path planning：规划账（飞成什么形状 / 时序 / 净空）",
    "system": "④ 系统：TA+PP 闭环与终局（不带上层/下层标签）",
}

#: 族标签（与文件名前缀一致），供文档与日志使用
OWNERS: dict[str, str] = {
    "IN": "输入（不带 TA/PP 标签）",
    "TA": "上层 task allocation 结果",
    "PP": "下层 path planning 结果",
    "SYS": "整个系统（TA+PP 闭环，不带标签）",
}


@dataclass(frozen=True)
class Figure:
    """一张图的登记信息。

    Attributes:
        id:      图 id，同时是模块名（小写）与落盘文件名（大写前缀）
        group:   所属层（initial / ta / pp / system）
        owner:   这张图在给谁记账（IN / TA / PP / SYS）
        phase:   叙事相位（initial / process / report），用于在层内排序说明
        title:   图题（写进 suptitle 的主标题）
        question: 这张图回答的唯一问题（一句话；答不出就别画）
        source:  数据来源（写进论文图注，便于复现）
        needs_scene: 是否需要重建场景（建筑/地形）
        build:   绘制入口 ``build(ctx, *, dpi) -> list[Path]``
        caption: 图注（论文里可直接用的一句：这张图看到的是什么）
        reading: 读法要点（2–4 条，教读者从哪看起、怎么判合格）
        clarify: 需要澄清的前提/口径/坑（图内写不下，落在 ``figures/CAPTIONS.md``）
    """

    id: str
    group: str
    owner: str
    phase: str
    title: str
    question: str
    source: str
    needs_scene: bool
    build: Callable[..., list]
    caption: str = ""
    reading: tuple[str, ...] = ()
    clarify: str = ""


FIGURES: tuple[Figure, ...] = (
    Figure(
        id="I01_task_pool",
        group="initial",
        owner="IN",
        phase="initial",
        title="任务池台账（输入）：任务在哪、多高、站不站得住",
        question="这一批任务点是从哪来的、什么高度、端点净空够不够？",
        source="tasks.json（产生侧落盘的全精度点位与合规证据）+ run_meta 的机巢站点",
        needs_scene=True,
        build=_b_i01,
        caption="30 个任务点在场景数据域内均匀撒点（与巢、机无关），★ 为三个启用机巢；"
                "右侧为逐任务台账。",
        reading=(
            "先看空间分布：点位在全城数据域内均匀抽取，不看机巢位置、不看机队能力；"
            "归属只按水平最近巢标注（本批巢0/1/2 = 8/14/8 个），"
            "负载不对称是场地形状造成的。",
            "再看台账两列：作业高 AGL 10.0–52.7 m 在 [10,120] 内；端点净空最小 9.03 m，"
            "高于生成侧 9 m 门槛（复核阈值 8 m = clearance 6 + 竖直量化 2）。",
            "任务号 T1–T30 = 清单序号，全文所有图与台账统一用它，不再有第二套编号。",
        ),
        clarify="点位由任务产生侧给出（全域均匀 + 场内/真高带/绝对带/全城 SDF/棱柱净空五道硬条件，"
                "水域与空地不排除），算法侧只接收、不改点；坐标全精度未取整，"
                "点位指纹 12a4555c98dd，`tasks.json` 的 generation 段记了抽样次数、"
                "过滤门槛与每巢归属。同址复访的任务会在图上并成一个位置、标号并列。",
    ),
    Figure(
        id="TA01_task_assignment",
        group="ta",
        owner="TA",
        phase="process",
        title="任务分配账（TA）：每个任务被哪架机、在第几站、于哪个周期执行",
        question="上层把 30 个任务派给了谁、排在航次第几站、失败后回没回池？",
        source="loop_trace.json 的 cycles[].tickets[].task_ids + reopened；tasks.json 点位",
        needs_scene=False,
        build=_b_ta01,
        caption="一格一个任务：格内「机号·第几站」，颜色是终局——到达、未到达（整航次失败"
                "退回池）、在池（本周期没进任何航次）、未出生。",
        reading=(
            "逐行 = 逐任务；行内格子的先后不是时间顺序，时间顺序看 PP02。",
            "橙格 + 右上红点 = 进了航次、但该航次一条航迹都没排出来，任务退回池。",
            "底部小结给一次性的「到达 / 未到达 / 在池」总数，与本图格子自洽。",
        ),
        clarify="本图只记分配，不判断是否真飞成（真交付看 PP01，回池补飞看 S01）。"
                "周期数不预设：只有一周期时，图上只有一列，没有中缝。",
    ),
    Figure(
        id="TA02_alloc_load",
        group="ta",
        owner="TA",
        phase="report",
        title="分配结构与负载（TA）：每条航次装了什么、估计多少、机位/巢位均衡吗",
        question="每航次装几个任务、按什么站序、上层估计多少里程、机队与机巢是否被用偏？",
        source="loop_trace.json 的 cycles[].tickets（task_ids / est_cost）+ est_cost + uav_config",
        needs_scene=False,
        build=_b_ta02,
        caption="(a) 每条航次一条估计航程条，条长 ∝ 上层 est_cost，条上刻度为站序；"
                "(b) 逐机派入的站数。斜纹条 = 派了但下层整航次无解。",
        reading=(
            "先看 (a) 的条长：它代表上层「以为」要飞多远，事后核对看 S01，不看本图。",
            "再看机号/机型标签：1–6 号机是否均衡；整周期无航次的机单独聚成一行。",
            "(b) 用来判断站数是否平摊——站数偏多意味着该机航次更长、更容易超时。",
        ),
        clarify="站序刻度按「巢→各站」平面距离比例摆放，只用于读顺序，不代表真实弧长；"
                "真实弧长与断点见 PP01，跨层代价偏差见 S01。",
    ),
    Figure(
        id="PP01_cycle_tracks",
        group="pp",
        owner="PP",
        phase="process",
        title="逐周期真实航迹（PP）：飞了哪几条、按什么次序、哪一段断了",
        question="下层每周期真正交付的航迹长什么样、站序对不对、失败航次断在哪一段？",
        source="trajectories.json（1 s 采样 ENU）+ failure_diag 逐航段诊断",
        needs_scene=True,
        build=_b_pp01,
        caption="下层真正交付的航迹（不是站点连线）：颜色为机号，圈外数字为任务号 T#，"
                "> 为飞行方向；失败航次断点前已解段画实线、断掉的一段画红色虚线。",
        reading=(
            "先看面板上方的逐航次明细：站数、里程、时长、以及确定的站序串 T#→T#——"
            "站序以这行串为准，图上箭头只用于快速核对方向。",
            "实线一律是「已解出来的几何」；失败航次的前缀段也是实线，但它不交付、只报告。",
            "红色虚线 = 断掉的那一段（该类多次尝试里净空最大的一手），X = 净空最紧点；"
            "不存在「虚线也算飞成」的读法。",
        ),
        clarify="航迹为 1 s 采样 ENU 米制；失败航次不交付整条航迹，前缀段只作诊断。"
                "断裂段虚线仍不可行（最紧点净空 < 6 m），是「最接近可行」的候选，不是解。"
                "建筑底图按屋顶高度分层设色，只作尺度参照，与航迹无因果关系。",
    ),
    Figure(
        id="PP02_tour_timing",
        group="pp",
        owner="PP",
        phase="process",
        title="航次时刻表（PP）：谁先飞、何时到哪一站、谁整周期没动",
        question="周期内的时间怎么排、到站时刻落在哪、同巢错峰有没有生效？",
        source="loop_trace 的 t0 / t_end / duration_s + trajectories 的到站采样时刻",
        needs_scene=False,
        build=_b_pp02,
        caption="每行一条航次：粗线 = 起飞到落地，两端短竖为起落时刻，白圈为到站时刻并标 T#。",
        reading=(
            "横轴是**周期内**时间（0–1800 s），不是跨周期全时长。",
            "看同巢的两条航次是否错开（离巢错峰 10 s）；重叠意味着上层没排开。",
            "整行空白 = 该机本周期一条航次都没有；有航次但无圈 = 到站时刻落在周期外。",
        ),
        clarify="到站时刻取 1 s 采样中最近的一点，误差 ≤ 0.5 s；标签拥挤时只留圈不标号，"
                "此时以面板/台账为准。",
    ),
    Figure(
        id="PP03_altitude_profile",
        group="pp",
        owner="PP",
        phase="report",
        title="航次高度剖面（PP）：沿程怎么爬、飞多高、离地多少",
        question="每航次的垂直剖面是否安全、最小离地净空还有多少余量、跨岸爬升在哪里？",
        source="trajectories 的 x/y/z + scenario.ground_height（地形/屋顶）",
        needs_scene=True,
        build=_b_pp03,
        caption="沿程距离上的绝对高度曲线，叠加地形/屋顶剖面，用于判离地净空是否够。",
        reading=(
            "先看曲线与地形之间的竖直距离：最小处即该航次的最小 AGL。",
            "看爬升集中在哪：跨江/跨岸的爬升段通常是净空与风扰最敏感的地方。",
            "只看交付成功的航次；失败航次没有完整剖面。",
        ),
        clarify="AGL 由 scenario.ground_height 反算，屋顶处取屋顶面而非地面，"
                "因此高楼旁的 AGL 会比地面口径小。缺 trajectories.json 时本图自动跳过。",
    ),
    Figure(
        id="S01_loop_closure",
        group="system",
        owner="SYS",
        phase="process",
        title="跨层闭环账（TA+PP）：可行性回灌怎么剔边、失败的账怎么被吸收",
        question="交付解里的几何不可行边有没有被剔掉、重解了几次、回池的任务下一周期补飞了几个？",
        source="metrics / loop_trace 的 n_resolves / infeasible_edges / unresolved_infeasible_edges / reopened + 台账",
        needs_scene=False,
        build=_b_s01,
        caption="(a) 逐周期 写进代价的不可飞边 vs 残留不可飞边；(b) 逐航次 计划 vs 实测 里程（对角线 = 完全一致）；"
                "(c) 累计到达任务数。",
        reading=(
            "重解条件只有一个：交付解含几何不可行边（severity=infeasible 且在白名单）；估计偏差不构成理由。",
            "(b) 的点越贴对角线，上层估计越准；偏在上方 = 实际比估计远，这是诊断不是触发。",
            "「补飞」= 回池任务在下一周期重新排进航次并到达；没触发重解时面板会明说。",
        ),
        clarify="只统计有真实航迹的成功航次——失败航次没有实测里程，不进对账。",
    ),
    Figure(
        id="S02_outcome",
        group="system",
        owner="SYS",
        phase="report",
        title="系统结果（TA+PP）：终局分账 · 机队实测负荷 · 审计",
        question="这一轮编排最终交付了多少、谁在飞、契约自洽性有没有破？",
        source="metrics.json + audit.json + tour 台账（实测里程）",
        needs_scene=False,
        build=_b_s02,
        caption="(a) 任务终局分账（到达 / 派出未到达 / 从未派出）；(b) 机队实测里程；"
                "(c) 审计逐项结论。",
        reading=(
            "「到达」= 有真实航迹；「派出未到达」= 进了航次但整条没排出、任务退回池；"
            "「从未派出」= 全程没进任何航次。",
            "(b) 只统计交付成功航次的实测里程，未交付航次不计入柱长（脚注会标数量）。",
            "(c) 任一硬判为 FAIL 时图面直接标红——FAIL 不等于「实验白做」，是契约被破。",
        ),
        clarify="完成率以 metrics.json 为准（有航迹任务数 / 任务总数，「排进航次」不算完成）；"
                "audit.json 判的是契约自洽，本实验 `require_full_completion=false`，"
                "所以 PASS 不等于 30/30——要看 (a) 栏与 metrics 的 flown_tasks。",
    ),
)


def figure_ids() -> tuple[str, ...]:
    return tuple(f.id for f in FIGURES)


def group_ids() -> tuple[str, ...]:
    return tuple(GROUPS)


def by_id(figure_id: str) -> Figure:
    """按 id 取图；找不到时报出全部可用 id（不静默跳过）。"""
    table = {f.id: f for f in FIGURES}
    if figure_id not in table:
        raise KeyError(f"未知图 id={figure_id!r}；可用：{list(table)}")
    return table[figure_id]


def resolve(
    *,
    names: Iterable[str] | None = None,
    groups: Iterable[str] | None = None,
    only: str | None = None,
) -> list[Figure]:
    """按 id / 层 / 前缀筛选，返回仍按 ``FIGURES`` 叙事序排列的结果。"""
    picked = list(FIGURES)
    if names is not None:
        table = {f.id: f for f in FIGURES}
        missing = [n for n in names if n not in table]
        if missing:
            raise ValueError(f"未知图名 {missing}；可用：{list(table)}")
        wanted = set(names)
        picked = [f for f in picked if f.id in wanted]
    if groups is not None:
        asked = list(groups)
        unknown = [g for g in asked if g not in GROUPS]
        if unknown:
            raise ValueError(f"未知分层 {unknown}；可用：{list(GROUPS)}")
        picked = [f for f in picked if f.group in set(asked)]
    if only:
        low = only.lower()
        picked = [f for f in picked if f.id.lower().startswith(low)]
    return picked


def describe() -> str:
    """生成 Markdown 表（供 `results/README.md` 直接粘贴）。"""
    label = {"IN": "输入（无层标）", "TA": "TA 仅上层", "PP": "PP 仅下层",
             "SYS": "SYS TA+PP 闭环"}
    lines = ["| 文件 | 层 | 回答的问题 | 数据来源 |", "|---|---|---|---|"]
    for f in FIGURES:
        lines.append(f"| `{f.id}.png` | {label.get(f.owner, f.owner)} | "
                     f"{f.question} | {f.source} |")
    return "\n".join(lines)
