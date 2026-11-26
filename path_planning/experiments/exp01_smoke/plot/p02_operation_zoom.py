# -*- coding: utf-8 -*-
"""P02：作业区放大图（相机对准五条航次的包围盒）。

全域图里五条航次只占一小块，放大一张才看得出"谁在谁头上悬停、谁给谁让路"；
长悬停（>60 s）画成线框球，它才是必撞锚点。航迹管径与点位尺寸在这里压细：
放大后仍用全域图的粗管，会把点位和楼群糊成一片。
"""

from __future__ import annotations

import sys

import numpy as np

from path_planning.common.scenario_loader import load_scenario

from ..support.scene_3d import render_scene

NAME = "P02_operation_zoom"


def build(ctx, *, z_scale=3.0, min_building_m=0.0, size=(3600, 2700),
          azimuth=-55.0, elevation=33.0, pad_m=120.0,
          traj_radius_m=5.0, task_glyph_m=12.0, hover_radius_m=10.0,
          hover_pole_m=45.0) -> list:
    scenario = load_scenario(
        ctx.cfg.scenario.data_dir, name=ctx.cfg.scenario.name,
        nest_count=ctx.cfg.scenario.nest_count,
        terrain_step_m=float(ctx.cfg.scenario.terrain_step_m),
        bbox_margin_m=float(ctx.cfg.scenario.bbox_margin_m),
        # 地形填充口径与计算侧一致，否则图里的地面高度与 SDF 基准对不上
        water_level_m=ctx.cfg.scenario.water_level_m,
    )
    ctx.fig_dir.mkdir(parents=True, exist_ok=True)
    render_scene(
        ctx.trajs, ctx.tasks, scenario,
        out_png=ctx.fig_dir / f"{NAME}.png", out_pdf=ctx.fig_dir / f"{NAME}.pdf",
        title="多机多周期航迹（作业区放大）",
        subtitle="红色竖杆（顶端圆点）＝机5 在 101 的长悬停（500 s，街谷内 20.3 m）；机6、机3 的第二航段都从该点穿过；机7 为点线、与机4 共用一条走廊（降级交付）",
        stage_label="周期", z_scale=float(z_scale), min_building_m=float(min_building_m),
        size=size, azimuth=float(azimuth), elevation=float(elevation), show_nests=False,
        focus_bounds=focus_bounds(ctx.trajs, pad_m=float(pad_m)),
        traj_radius_m=float(traj_radius_m), task_glyph_m=float(task_glyph_m),
        hover_radius_m=float(hover_radius_m), hover_pole_m=float(hover_pole_m),
    )
    return [ctx.fig_dir / f"{NAME}.png", ctx.fig_dir / f"{NAME}.pdf"]


def focus_bounds(trajs, pad_m: float = 120.0) -> tuple[float, float, float, float]:
    """五条航次起终点的包围盒 + 边距（放大视图的取景框）。"""
    pts = []
    for traj in trajs.values():
        pts.append(np.asarray(traj.start_position, dtype=float))
        pts.append(np.asarray(traj.end_position, dtype=float))
    box = np.vstack(pts)
    return (float(box[:, 0].min() - pad_m), float(box[:, 1].min() - pad_m),
            float(box[:, 0].max() + pad_m), float(box[:, 1].max() + pad_m))


if __name__ == "__main__":  # pragma: no cover
    from ._shared import run_single
    sys.exit(run_single("exp01 P02 作业区放大图", build, None))


