# -*- coding: utf-8 -*-
"""exp01_smoke **图清单**：唯一事实来源（single source of truth）。

新增一张图 = 在 ``FIGURES`` 里加一行。改标题 / 调顺序 / 换分组，都只动这里。

命名与查找规则（三者同名）::

    模块名 ≡ 图 id ≡ 绘制函数名去掉 ``plot_``；输出文件名 = ``fig_<图 id>.png``
    例：plot/route_vs_path.py → plot_route_vs_path → fig_route_vs_path.png

即**看到图文件名就能定位代码，看到代码就知道图叫什么**。

分组（按「论文叙事线」划分——读者从设定读到结论的顺序）::

    overview  ① 总览：区域全景、场景俯视（与算法无关的态势）
    process   ② 规划过程：route/path/trajectory 三者区分、竖直剖面
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass
class PlotContext:
    """一次出图所需的全部上下文（run.py 组装，绘图模块只读）。

    Attributes:
        env:         裁剪后的 CityEnvironment（活跃窗口）
        env_full:    全量 CityEnvironment（区域总览背景）
        net:         扩展控制网络
        results:     metrics.od_results 的记录列表（dict）
        planned:     [(origin, dest, PlanResult)]，side_view 需要完整 path.pts
        scenario_dir: 场景目录（region.geojson 定位用）
    """

    env: Any
    env_full: Any
    net: Any
    results: Sequence[dict]
    planned: Sequence[tuple]
    scenario_dir: str


@dataclass(frozen=True)
class Figure:
    """一张图的登记信息。

    Attributes:
        id:    图 id，**同时是**模块名与输出文件名（不含扩展名）
        group: 所属层（overview / process），见模块 docstring
        title: 中文短标题，供图注与日志使用
        source: 数据来源说明（写到文档里，便于写论文图注）
        func:  绘制函数，统一签名 ``(ctx: PlotContext, out_dir: str) -> list[str]``
    """

    id: str
    group: str
    title: str
    source: str
    func: Callable[..., list]


# ── 惰性导入各图模块（registry 只登记，不养依赖）─────────────────────────
from .region_overview import plot_region_overview  # noqa: E402
from .overview import plot_overview  # noqa: E402
from .route_vs_path import plot_route_vs_path  # noqa: E402
from .trajectory import plot_trajectory  # noqa: E402
from .side_view import plot_side_view  # noqa: E402
from .scene_3d import plot_scene_3d  # noqa: E402

FIGURES: tuple[Figure, ...] = (
    # ── ① overview：态势总览 ──────────────────────────────────
    Figure(
        id="region_overview",
        group="overview",
        title="整个区域总览：全城建筑密度 + 研究区边界 + 禁飞区 + 裁剪窗口",
        source="env_full 建筑质心 hexbin + region.geojson + 静/动态禁飞 + 规划路径",
        func=plot_region_overview,
    ),
    Figure(
        id="scene_3d",
        group="overview",
        title="三维场景：建筑棱柱 + 禁飞区棱柱 + 3D 轨迹（plotly 交互式）",
        source="env.buildings(棱柱) + 静/动态禁飞(0→120m 示意) + planned[].path.pts/route.points",
        func=plot_scene_3d,
    ),
    Figure(
        id="overview",
        group="overview",
        title="场景俯视：建筑 / 控制点 / 规划路径",
        source="env.buildings + net.points + od_results[].path_xy",
        func=plot_overview,
    ),
    # ── ② process：route / path / trajectory 与剖面 ──────────
    Figure(
        id="route_vs_path",
        group="process",
        title="route（虚线=控制点走廊） vs path（实线=Dubins 平滑曲线）",
        source="od_results[].route_control_points + path_xy",
        func=plot_route_vs_path,
    ),
    Figure(
        id="trajectory",
        group="process",
        title="trajectory = path + 时间参数化（颜色=到达时刻，匀速占位）",
        source="od_results[].path_xy + trajectory.times_s",
        func=plot_trajectory,
    ),
    Figure(
        id="side_view",
        group="process",
        title="竖直剖面：轨迹弧长-高度 + 近旁建筑 + 禁飞约束参照",
        source="planned[].res.path.pts + env.buildings + env.dynamic_nofly",
        func=plot_side_view,
    ),
)


def figure_ids() -> list[str]:
    return [f.id for f in FIGURES]


def resolve(fig_id: str) -> Figure:
    for f in FIGURES:
        if f.id == fig_id:
            return f
    raise KeyError(f"未登记的图 id: {fig_id}（已登记: {figure_ids()}）")
