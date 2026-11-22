# -*- coding: utf-8 -*-
"""全域三维图：VTK 渲染场景 + PIL 排版图注，一条图看完整实验环境。

职责：把落盘航迹画在全域场景里（**全域**= `data_bounds_xy`，不是每条航迹自己的 bbox），
      标出全部机巢与任务停靠点；输出 PNG（投稿用）与 PDF（排版用）。
边界：只读结果文件与场景数据，不重跑规划、不做统计；几何由 ``scene_meshes.py`` 给。
相关：各实验的 ``build_figures.py``（图写到 ``results/<seed>/figures/``）。

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

from .scene_meshes import (
    BUILDING_CMAP, buildings_mesh, terrain_mesh,
)
from path_planning.methods.planning.leg_segments import sampled_positions

__all__ = ["render_scene"]

# 机号基色（1 绛红、2 靛蓝）；同一台机的第 2 段在同色上压暗，图例与"第几轮/第几拍"文字对应
_UAV_BASE = {1: "#c1282d", 2: "#2166ac"}
_STAGE_BASE = {1: "#c1282d", 2: "#2166ac"}  # 单机实验里"轮/拍"直接换色，比同色压暗更易读
_UAV_NAME = {1: "无人机 1", 2: "无人机 2"}
_NEST_COLOR = "#1b1b1b"
_TASK_COLOR = "#c07a12"
_LAND_COLOR = "#e4e5d6"      # 地面：低饱和的暖灰绿，让楼群与航迹成为主体
_SKIRT_COLOR = "#eeeada"     # 底座侧面：比地面略深，显出"地块"厚度
_FONT_BOLD = "C:/Windows/Fonts/simhei.ttf"
_FONT_BODY = "C:/Windows/Fonts/Deng.ttf"
_CANVAS_W = 3200           # 成图宽度（像素）；300 dpi 打印约 27 cm 宽


def render_scene(
    trajectories: Mapping[str, Any],
    tasks: Mapping[int, Sequence[float]],
    scenario,
    *,
    out_png: Path,
    title: str,
    subtitle: str = "",
    out_pdf: Path | None = None,
    stage_label: str = "轮",
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
) -> None:
    """``trajectories`` 的键形如 ``"段:机号"``（第几轮/第几拍 : 机号）。"""
    x0, y0, x1, y1 = (float(v) for v in scenario.data_bounds_xy)
    bounds = (x0 - pad_m, y0 - pad_m, x1 + pad_m, y1 + pad_m)

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
    for key in sorted(trajectories, key=lambda k: (int(k.split(":")[1]), int(k.split(":")[0]))):
        stage, uid = (int(x) for x in key.split(":"))
        traj = trajectories[key]
        pts = np.asarray(sampled_positions(traj, 1.0), dtype=float)
        pts[:, 2] *= float(z_scale)
        color = _stage_color(uid, stage, single_uav)
        presenter.add_mesh(pv.MultipleLines(pts).tube(radius=26.0, n_sides=18, capping=False),
                           color=color, smooth_shading=True, name=f"traj_{stage}_{uid}",
                           ambient=0.6, diffuse=0.55, specular=0.35)
        legend.append((f"{_UAV_NAME.get(uid, '机' + str(uid))}·第 {stage} {stage_label}"
                       f"　{traj.duration:.1f} s ／ {traj.path_len_m:.0f} m", color))

    nest_pts = [(float(n.position[0]), float(n.position[1]), float(n.position[2]) * z_scale)
                for n in scenario.nests]
    task_pts = [(float(tasks[t][0]), float(tasks[t][1]), float(tasks[t][2]) * z_scale)
                for t in sorted(tasks)]
    presenter.add_mesh(_glyphs(nest_pts, pv.Octahedron(radius=55.0)), color=_NEST_COLOR,
                       smooth_shading=False, name="nests")
    if task_pts:
        presenter.add_mesh(_glyphs(task_pts, pv.Cube(bounds=(-40, 40, -40, 40, -40, 40))),
                           color=_TASK_COLOR, smooth_shading=False, name="tasks")

    zs = np.concatenate([ground.points[:, 2], np.asarray([p[2] for p in nest_pts]),
                         np.asarray([p[2] for p in task_pts])])
    center = np.array([(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2,
                       float((zs.min() + zs.max()) / 2)])
    diag = float(np.linalg.norm([bounds[2] - bounds[0], bounds[3] - bounds[1],
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
    tags = [((float(p[0]), float(p[1])), f"巢{int(n.id)}") for p, n in zip(nest_marks, scenario.nests)]
    tags += [((float(p[0]), float(p[1])), str(t)) for p, t in zip(task_marks, sorted(tasks))]
    scale = _px_per_km(presenter, center)
    north = _project(presenter, [(center[0], center[1], zs.max())],
                     [(center[0], center[1] + 1000.0, zs.max())])
    north_dir = np.asarray(north[1][0]) - np.asarray(north[0][0])
    presenter.close()

    _compose(image, tags, float(scale), north_dir, legend, title, subtitle, bounds,
             z_scale, min_building_m, out_png, out_pdf)


# ---------------------------------------------------------------------------


def _glyphs(points: Sequence[Sequence[float]], geom) -> pv.PolyData:
    """把一串点位换成等尺寸几何体（菱形/方块），不按标量缩放、不朝向。"""
    return pv.PolyData(np.asarray(points, dtype=float)).glyph(geom=geom, scale=False, orient=False)


def _stage_color(uid: int, stage: int, single_uav: bool) -> str:
    """单机：按"轮/拍"换色；多机：按机换色，第 2 段压暗（多机同拍换色会撞车）。"""
    if single_uav:
        return _STAGE_BASE.get(stage, "#555555")
    base = _UAV_BASE.get(uid, "#555555")
    return base if stage <= 1 else _blend(base, "#0d0d0d", 0.55)


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


def _compose(image: np.ndarray, tags, scale: float, north_dir: np.ndarray, legend,
             title: str, subtitle: str, bounds: Sequence[float], z_scale: float,
             min_building_m: float, out_png: Path, out_pdf: Path | None) -> None:
    """标题带 + 场景 + 图例带 三段式画布：注记不压主体，字号与对齐可控。"""
    width, band_top, band_bottom = _CANVAS_W, 250, 300
    scene_img, box = _trim(image)
    scale_px = width / float(scene_img.width)
    scene = scene_img.resize((width, int(round(scene_img.height * scale_px))), Image.LANCZOS)
    canvas = Image.new("RGB", (width, band_top + band_bottom + scene.height), "white")
    canvas.paste(scene, (0, band_top))

    draw = ImageDraw.Draw(canvas)
    f_title = ImageFont.truetype(_FONT_BOLD, 72)
    f_sub = ImageFont.truetype(_FONT_BODY, 40)
    f_body = ImageFont.truetype(_FONT_BODY, 40)
    f_tag = ImageFont.truetype(_FONT_BODY, 34)
    f_note = ImageFont.truetype(_FONT_BODY, 28)

    _text(draw, (72, 34), title, f_title, "#111111")
    if subtitle:
        _text(draw, (74, 138), subtitle, f_sub, "#555555")

    # 点位注记：文字带白描边，压在楼群上也读得清；位置按裁剪+缩放后的画布坐标换算
    for (x, y), label in tags:
        px = (x - box[0]) * scale_px + 28
        py = (y - box[1]) * scale_px + band_top - 16
        _text(draw, (px, py), label, f_tag, "#1b1b1b", halo=5)

    # 比例尺（场景左下）
    bar = int(round(scale * scale_px))
    bx, by = 110, band_top + scene.height - 110
    draw.line([(bx, by), (bx + bar, by)], fill="#333333", width=6)
    for xx in (bx, bx + bar, bx + bar // 2):
        draw.line([(xx, by - 10), (xx, by + 10)], fill="#333333", width=6)
    _text(draw, (bx, by - 62), "1 km", f_tag, "#333333", halo=5)

    # 指北针（场景右上）：方向由投影后的 +y 单位向量给出，跟着相机转
    nd = np.asarray(north_dir, dtype=float)
    nd = nd / max(float(np.linalg.norm(nd)), 1e-6)
    nx, ny = width - 170, band_top + 175
    tip, tail = np.array([nx, ny]) + nd * 62.0, np.array([nx, ny]) - nd * 62.0
    side = np.array([-nd[1], nd[0]])
    draw.line([tuple(tail), tuple(tip)], fill="#333333", width=6)
    draw.polygon([tuple(tip), tuple(tip - nd * 26 + side * 15),
                  tuple(tip - nd * 26 - side * 15)], fill="#333333")
    _text(draw, tuple(np.array([nx, ny]) + nd * 86.0 - np.array([16, 40])), "北", f_tag,
          "#333333", halo=5)

    # 图例带：色条 + 文字（左列），符号说明（右列）
    lx, ly = 74, band_top + scene.height + 42
    for text, color in legend:
        draw.rounded_rectangle([lx, ly + 18, lx + 96, ly + 34], radius=4, fill=color)
        _text(draw, (lx + 118, ly + 2), text, f_body, "#222222")
        ly += 64
    sym_x, sy = lx + 1180, band_top + scene.height + 48
    draw.polygon([(sym_x, sy), (sym_x + 17, sy + 17), (sym_x, sy + 34), (sym_x - 17, sy + 17)],
                 fill=_NEST_COLOR)
    _text(draw, (sym_x + 34, sy + 1), "机巢", f_body, "#222222")
    draw.rectangle([sym_x + 236, sy + 4, sym_x + 268, sy + 36], fill=_TASK_COLOR)
    _text(draw, (sym_x + 290, sy + 1), "任务停靠点（编号＝任务 id）", f_body, "#222222")

    note = (f"全域 {bounds[2] - bounds[0]:.0f}×{bounds[3] - bounds[1]:.0f} m，含全部机巢与"
            f"{'全部建筑' if min_building_m <= 0 else f' ≥{min_building_m:g} m 建筑'}；"
            f"竖向放大 {z_scale:g}× 仅作阅读约定，不改数据口径")
    _text_right(draw, (width - 72, band_top + scene.height + 238), note, f_note, "#666666")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png)
    if out_pdf is not None:
        canvas.save(out_pdf, resolution=300.0)


def _trim(image: np.ndarray, margin: int = 24) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """裁掉四周纯白，把场景图收紧；返回裁剪后的图与裁剪框（供注记坐标换算）。"""
    img = Image.fromarray(image).convert("RGB")
    gray = np.asarray(img.convert("L"))
    rows = np.where(gray.min(axis=1) < 250)[0]
    cols = np.where(gray.min(axis=0) < 250)[0]
    if rows.size == 0 or cols.size == 0:
        return img, (0, 0, img.width, img.height)
    box = (max(int(cols.min()) - margin, 0), max(int(rows.min()) - margin, 0),
           min(int(cols.max()) + margin, img.width), min(int(rows.max()) + margin, img.height))
    return img.crop(box), box


def _text(draw, xy, text: str, font, fill, halo: int = 0) -> None:
    draw.text(xy, text, font=font, fill=fill,
              stroke_width=halo, stroke_fill="white" if halo else None)


def _text_right(draw, xy, text: str, font, fill) -> None:
    w = draw.textlength(text, font=font)
    draw.text((xy[0] - w, xy[1]), text, font=font, fill=fill)
