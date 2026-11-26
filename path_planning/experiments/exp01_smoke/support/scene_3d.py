# -*- coding: utf-8 -*-
"""全域三维图：VTK 渲染场景 + PIL 排版图注，一条图看完整实验环境。

职责：把落盘航迹画在全域场景里（**全域**= `data_bounds_xy`，不是每条航迹自己的 bbox），
      标出全部机巢与任务停靠点；输出 PNG（投稿用）与 PDF（排版用）。
边界：只读结果文件与场景数据，不重跑规划、不做统计；几何由 ``scene_meshes.py`` 给。
相关：各实验的 ``plot/`` 的产物脚本（图写到 ``results/<seed>/figures/``）。

**为什么不用 Plotly**：Plotly 3D 的相机距离被内部归一化，掠视/俯视角下必然钻进楼群，
      "整片研究区"根本进不了画；VTK 有真实深度缓冲与可控相机，楼群遮挡关系才正确。
**为什么文字画在画布上**：VTK 的文字贴在三维窗口里，会压住地形、且中文字距难控；
      改为渲染干净的场景图，再用 PIL 在标题带/图例带排版，字号与对齐可精确控制。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pyvista as pv
from PIL import Image, ImageDraw, ImageFont

from .scene_caption import NEST_COLOR, TASK_COLOR, compose_canvas
from .scene_meshes import (
    BUILDING_CMAP, buildings_mesh, terrain_mesh,
)
from path_planning.methods.planning.leg_segments import sampled_positions

__all__ = ["render_scene"]

# 机号基色（1 绛红、2 靛蓝）；同一台机的第 2 段在同色上压暗，图例与"第几周期/第几周期"文字对应
_UAV_BASE = {1: "#c1282d", 2: "#2166ac", 3: "#1a7f5a", 4: "#d1691a",
             5: "#c1282d", 6: "#2166ac", 7: "#7b3294"}
_STAGE_BASE = {1: "#c1282d", 2: "#2166ac"}  # 单机实验里"周期/周期"直接换色，比同色压暗更易读
_UAV_NAME = {1: "无人机 1", 2: "无人机 2", 3: "无人机 3", 4: "无人机 4",
             5: "无人机 5", 6: "无人机 6", 7: "无人机 7（降级）"}
_LAND_COLOR = "#e4e5d6"      # 地面：低饱和的暖灰绿，让楼群与航迹成为主体
_SKIRT_COLOR = "#eeeada"     # 底座侧面：比地面略深，显出"地块"厚度


def render_scene(
    trajectories: Mapping[str, Any],
    tasks: Mapping[int, Sequence[float]],
    scenario,
    *,
    out_png: Path,
    title: str,
    subtitle: str = "",
    out_pdf: Path | None = None,
    stage_label: str = "周期",
    z_scale: float = 3.0,
    min_building_m: float = 0.0,
    terrain_step_m: float = 40.0,
    pad_m: float = 120.0,
    size: tuple[int, int] = (3600, 2700),
    azimuth: float = -55.0,
    elevation: float = 33.0,
    view_angle: float = 30.0,
    distance_ratio: float = 0.86,
    ssao: bool = True,
    focus_bounds: Sequence[float] | None = None,
    show_nests: bool = True,
    traj_radius_m: float = 26.0,
    task_glyph_m: float = 18.0,
    hover_radius_m: float = 95.0,
    hover_pole_m: float = 0.0,
) -> None:
    """``trajectories`` 的键形如 ``"段:机号"``（第几周期/第几周期 : 机号）。

    ``focus_bounds`` 给出 ``(x0,y0,x1,y1)`` 时只把相机对准该小块（几何仍按全域建），
    用于"全域一张 + 作业区放大一张"的两分面出图。``show_nests=False`` 用于无巢口径的
    实验：起终点是裸坐标，画上机巢会让人误读成"从巢起飞"。
    ``traj_radius_m`` 是航迹管径（米）：全域图用粗一点才看得见，放大图必须调细，
    否则管子会把任务点和楼群全糊住；``task_glyph_m`` 同理会按出图尺度调。
    """
    x0, y0, x1, y1 = (float(v) for v in scenario.data_bounds_xy)
    bounds = (x0 - pad_m, y0 - pad_m, x1 + pad_m, y1 + pad_m)
    view = tuple(float(v) for v in focus_bounds) if focus_bounds is not None else bounds

    presenter = pv.Plotter(off_screen=True, window_size=size)
    presenter.set_background("white")
    presenter.enable_anti_aliasing("ssaa")

    ground = terrain_mesh(scenario, bounds, step_m=terrain_step_m, z_scale=z_scale)
    # 底座：把地表往下拖 3 m 再拉伸，顶面藏在地表之下，既有厚度又不与地表争深度（避免麻点）
    skirt = ground.extract_surface(algorithm="dataset_surface").translate(
        (0.0, 0.0, -3.0), inplace=False).extrude((0.0, 0.0, -260.0), capping=True)
    presenter.add_mesh(skirt, color=_SKIRT_COLOR, smooth_shading=False, name="skirt",
                       ambient=0.5, diffuse=0.55, specular=0.0)
    presenter.add_mesh(ground, color=_LAND_COLOR, show_scalar_bar=False,
                       smooth_shading=True, name="terrain",
                       ambient=0.62, diffuse=0.5, specular=0.0)
    buildings = buildings_mesh(scenario, min_height_m=min_building_m, z_scale=z_scale)
    if buildings is not None:
        presenter.add_mesh(buildings, scalars="top", cmap=BUILDING_CMAP,
                           clim=(float(min_building_m), 120.0), show_scalar_bar=False,
                           smooth_shading=False, name="buildings",
                           ambient=0.3, diffuse=0.86, specular=0.03)

    legend: list[tuple[str, str]] = []  # (图例文字, 线色)
    single_uav = len({int(k.split(":")[1]) for k in trajectories}) == 1
    hover_marks: list[tuple[Sequence[float], str, str]] = []  # (点位, 文字, 线色)
    # 估算航迹（降级交付）常与某台实体航迹共用同一条走廊、几何完全重合。
    # 画成"点线"（沿线一串小球）而不是更粗的管：既不会盖掉共线的那台，
    # 也不会因为管径不同被误读成"两条平行航迹"。
    order = sorted(trajectories,
                   key=lambda k: (trajectories[k].time_source.value == "estimate",
                                  int(k.split(":")[1]), int(k.split(":")[0])))
    for key in order:
        stage, uid = (int(x) for x in key.split(":"))
        traj = trajectories[key]
        pts = np.asarray(sampled_positions(traj, 1.0), dtype=float)
        pts[:, 2] *= float(z_scale)
        color = _stage_color(uid, stage, single_uav)
        estimate = traj.time_source.value == "estimate"
        if estimate:
            stride = max(int(round(float(traj_radius_m) * 3.0)), 1)  # 采样间距 1 m，故按索引跳
            dots = pts[::stride]
            if not np.allclose(dots[-1], pts[-1]):
                dots = np.vstack([dots, pts[-1]])
            presenter.add_mesh(_glyphs(dots, pv.Sphere(radius=float(traj_radius_m) * 1.4)),
                               color=color, lighting=False, name=f"traj_{stage}_{uid}")
        else:
            presenter.add_mesh(
                pv.MultipleLines(pts).tube(radius=float(traj_radius_m), n_sides=12, capping=False),
                color=color, smooth_shading=True, name=f"traj_{stage}_{uid}",
                ambient=0.6, diffuse=0.55, specular=0.35)
        legend.append((f"{_UAV_NAME.get(uid, '机' + str(uid))}·第 {stage} {stage_label}"
                       f"　{traj.duration:.1f} s ／ {traj.path_len_m:.0f} m"
                       + ("（估算时间，点线；与共走廊的实体航迹同线）" if estimate else ""),
                       color))
        # 长悬停（>60 s）是"必撞锚点"所在，航迹本身只是一条线，画个球才看得见
        for seg in traj.segments:
            if seg.is_hover and (seg.t1 - seg.t0) > 60.0:
                p = (float(seg.p0[0]), float(seg.p0[1]), float(seg.p0[2]) * z_scale)
                hover_marks.append((p, f"悬停 {seg.t1 - seg.t0:.0f} s", color))
                break

    nest_pts = ([(float(n.position[0]), float(n.position[1]), float(n.position[2]) * z_scale)
                 for n in scenario.nests] if show_nests else [])
    task_pts = [(float(tasks[t][0]), float(tasks[t][1]), float(tasks[t][2]) * z_scale)
                for t in sorted(tasks)]
    if nest_pts:
        presenter.add_mesh(_glyphs(nest_pts, pv.Octahedron(radius=55.0)), color=NEST_COLOR,
                           smooth_shading=False, name="nests")
    if task_pts:
        # 停靠点画成小菱形（不是方块）：方块在高楼上会读成一栋楼，菱形只当点位标记
        presenter.add_mesh(_glyphs(task_pts, pv.Octahedron(radius=float(task_glyph_m))),
                           color=TASK_COLOR, lighting=False, name="tasks")
    for i, (p, _text_mark, color) in enumerate(hover_marks):
        # 线框球：标出"悬停体量"，又不把点位和航迹糊成一片
        presenter.add_mesh(pv.Sphere(radius=float(hover_radius_m), center=p,
                                     theta_resolution=24, phi_resolution=16),
                           style="wireframe", color=color, opacity=0.55, line_width=3,
                           lighting=False, name=f"hover_{i}")
        # 低空点位会被楼群包住（球体整颗藏在街谷里）：加一根竖杆把人从楼群里牵出来
        if hover_pole_m > 0.0:
            top = (p[0], p[1], p[2] + float(hover_pole_m) * z_scale)
            presenter.add_mesh(
                pv.MultipleLines(np.asarray([p, top], dtype=float)).tube(
                    radius=2.6, n_sides=8, capping=True),
                color=color, lighting=False, name=f"hover_pole_{i}")
            presenter.add_mesh(pv.Sphere(radius=5.0, center=top, theta_resolution=16,
                                         phi_resolution=12),
                               color=color, lighting=False, name=f"hover_pin_{i}")

    zs = np.concatenate([ground.points[:, 2], np.asarray([p[2] for p in nest_pts]),
                         np.asarray([p[2] for p in task_pts])])
    center = np.array([(view[0] + view[2]) / 2, (view[1] + view[3]) / 2,
                       float((zs.min() + zs.max()) / 2)])
    diag = float(np.linalg.norm([view[2] - view[0], view[3] - view[1],
                                 zs.max() - zs.min()]))
    az, el = np.radians(azimuth), np.radians(elevation)
    dist = distance_ratio * (diag / 2.0) / np.tan(np.radians(view_angle / 2.0))
    presenter.camera.position = center + dist * np.array(
        [np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])
    presenter.camera.focal_point = center
    presenter.camera.up = (0.0, 0.0, 1.0)
    presenter.camera.view_angle = float(view_angle)

    # 光：西北向主光（楼群有明暗面、地形有起伏感）+ 相机补光；SSAO 再加楼脚接触阴影
    presenter.remove_all_lights()
    presenter.add_light(pv.Light(light_type="scene light", intensity=0.85,
                                 position=center + diag * np.array([-0.55, 0.62, 0.72]),
                                 focal_point=center))
    presenter.add_light(pv.Light(light_type="camera light", intensity=0.3))
    if ssao:
        presenter.enable_ssao(kernel_size=32, bias=0.01, radius=0.03)

    presenter.show(auto_close=False)
    image = presenter.screenshot(return_img=True)
    nest_marks, task_marks = _project(presenter, nest_pts, task_pts)
    tags = ([((float(p[0]), float(p[1])), f"巢{int(n.id)}") for p, n in zip(nest_marks, scenario.nests)]
            if show_nests else [])
    tags += [((float(p[0]), float(p[1])), str(t)) for p, t in zip(task_marks, sorted(tasks))]
    if hover_marks:
        _project(presenter, [p for p, _, _ in hover_marks])  # 只为保持注记取点顺序一致
    scale = _px_per_km(presenter, center)
    north = _project(presenter, [(center[0], center[1], zs.max())],
                     [(center[0], center[1] + 1000.0, zs.max())])
    north_dir = np.asarray(north[1][0]) - np.asarray(north[0][0])
    presenter.close()

    compose_canvas(image, tags, float(scale), north_dir, legend, title, subtitle, view,
                   z_scale, min_building_m, out_png, out_pdf, show_nests=show_nests)


# ---------------------------------------------------------------------------


def _glyphs(points: Sequence[Sequence[float]], geom) -> pv.PolyData:
    """把一串点位换成等尺寸几何体（菱形/方块），不按标量缩放、不朝向。"""
    return pv.PolyData(np.asarray(points, dtype=float)).glyph(geom=geom, scale=False, orient=False)


def _stage_color(uid: int, stage: int, single_uav: bool) -> str:
    """单机：按"周期/周期"换色；多机：按机换色，第 2 段压暗（多机同周期换色会撞车）。"""
    if single_uav:
        return _STAGE_BASE.get(stage, "#555555")
    base = _UAV_BASE.get(uid, "#555555")
    return base if stage <= 1 else _blend(base, "#0d0d0d", 0.32)


def _blend(color: str, other: str, t: float) -> str:
    a = np.array([int(color[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)
    b = np.array([int(other[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)
    c = a * (1.0 - t) + b * t
    return "#" + "".join(f"{int(round(v)):02x}" for v in c)


def _vtk_renderer(presenter: pv.Plotter):
    return presenter.render_window.GetRenderers().GetFirstRenderer()


def _project(presenter: pv.Plotter, *groups: Sequence[Sequence[float]]) -> list[np.ndarray]:
    """世界坐标 → 图像像素坐标（左上为原点），供 PIL 在点位旁写注记。"""
    import vtk

    renderer = _vtk_renderer(presenter)
    height = presenter.window_size[1]
    out: list[np.ndarray] = []
    coord = vtk.vtkCoordinate()
    coord.SetCoordinateSystemToWorld()
    coord.SetViewport(renderer)
    for group in groups:
        rows = []
        for x, y, z in group:
            coord.SetValue(float(x), float(y), float(z))
            dx, dy = coord.GetComputedDisplayValue(renderer)
            rows.append((dx, height - dy))
        out.append(np.asarray(rows, dtype=float))
    return out


def _px_per_km(presenter: pv.Plotter, center: np.ndarray) -> float:
    """以场景中心 1 km 线段的像素长度做比例尺（近正交投影下足够准）。"""
    a, b = _project(presenter, [(center[0], center[1], 0.0)],
                    [(center[0] + 1000.0, center[1], 0.0)])
    return float(np.linalg.norm(b[0] - a[0]))
