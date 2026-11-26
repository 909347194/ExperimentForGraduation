# -*- coding: utf-8 -*-
"""P03：两周期五机的全域三维**交互** HTML（Plotly）。

一图一名 ``P03_interactive_3d.html``；plotly.js 内嵌、离线可开。
与 P01（VTK 静态投稿图）同一套场景口径：全域取景、竖向放大 3×、建筑按楼顶高程
渐变、长悬停画半透明球；差别只在"谁定视角"——P01 相机写死、P03 用户自己转。
"""

from __future__ import annotations

import sys

from path_planning.common.scenario_loader import load_scenario
from path_planning.common.scene_html import render_interactive

from ..support.scene_3d import _stage_color, _UAV_NAME

NAME = "P03_interactive_3d"


def build(ctx, *, z_scale=3.0, min_building_m=0.0, **_style_ignored) -> list:
    """全域交互视图：复用 exp01 的机号配色与图例命名（与 P01 图注一致）。"""
    scenario = load_scenario(
        ctx.cfg.scenario.data_dir, name=ctx.cfg.scenario.name,
        nest_count=ctx.cfg.scenario.nest_count,
        terrain_step_m=float(ctx.cfg.scenario.terrain_step_m),
        bbox_margin_m=float(ctx.cfg.scenario.bbox_margin_m),
        # 地形填充口径与计算侧一致，否则图里的地面高度与 SDF 基准对不上
        water_level_m=ctx.cfg.scenario.water_level_m,
    )

    def style(stage: int, uid: int, single_uav: bool, traj):
        color = _stage_color(uid, stage, single_uav)
        label = (_UAV_NAME.get(uid, "机" + str(uid)) + f"·第 {stage} 周期"
                 f"　{traj.duration:.1f} s ／ {traj.path_len_m:.0f} m")
        dashed = traj.time_source.value == "estimate"
        if dashed:
            label += "（估算时间，虚线）"
        return color, label, dashed

    out = ctx.fig_dir / f"{NAME}.html"
    render_interactive(
        ctx.trajs, ctx.tasks, scenario,
        out_html=out,
        title="多机多周期航迹（exp01_smoke，交互）",
        subtitle="无巢口径：起终点是裸坐标｜周期1 机5/机6/机7（新-新协调；机7 为降级交付、估算时间，虚线）｜"
                 "周期2 机3/机4（既有航迹约束下规划，旧机 5 冻结）｜竖向放大 3× 只作阅读约定",
        stage_label="周期", z_scale=float(z_scale), min_building_m=float(min_building_m),
        show_nests=False, traj_style=style,
    )
    return [out]


if __name__ == "__main__":  # pragma: no cover
    from ._shared import run_single
    sys.exit(run_single("exp01 P03 全域交互三维 HTML", build, None))
