# -*- coding: utf-8 -*-
"""P01：两周期五机的全域三维图（整片研究区 + 全部建筑）。

一图一名 ``P01_multi_cycle_3d.{png,pdf}``；竖向放大 3× 只作阅读约定，写进图注。
"""

from __future__ import annotations

import sys

from path_planning.common.scenario_loader import load_scenario

from ..support.scene_3d import render_scene

NAME = "P01_multi_cycle_3d"


def build(ctx, *, z_scale=3.0, min_building_m=0.0, size=(3600, 2700),
          azimuth=-55.0, elevation=33.0) -> list:
    """全域视图：相机按整个研究区取景，五条航次只占一小块。"""
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
        title="多机多周期航迹（exp01_smoke）",
        subtitle="无巢口径：起终点是裸坐标｜周期1 机5/机6/机7（新-新协调；机7 为降级交付、估算时间）｜"
                 "周期2 机3/机4（既有航迹约束下规划，旧机 5 冻结）",
        stage_label="周期", z_scale=float(z_scale), min_building_m=float(min_building_m),
        size=size, azimuth=float(azimuth), elevation=float(elevation), show_nests=False,
    )
    return [ctx.fig_dir / f"{NAME}.png", ctx.fig_dir / f"{NAME}.pdf"]


if __name__ == "__main__":  # pragma: no cover
    from ._shared import run_single
    sys.exit(run_single("exp01 P01 全域三维图", build, None))

