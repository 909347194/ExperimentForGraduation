# -*- coding: utf-8 -*-
"""场景成图的"画布排版"：标题带、点位注记、比例尺、指北针、图例带。

从 ``scene_3d.py`` 拆出来只为控单文件长度：这里全部是 PIL 排版，不碰三维几何。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

CANVAS_W = 3200              # 成图宽度（像素）；300 dpi 打印约 27 cm 宽
NEST_COLOR = "#1b1b1b"
TASK_COLOR = "#c07a12"
FONT_BOLD = "C:/Windows/Fonts/simhei.ttf"
FONT_BODY = "C:/Windows/Fonts/Deng.ttf"


def compose_canvas(image: np.ndarray, tags, scale: float, north_dir: np.ndarray, legend,
                   title: str, subtitle: str, bounds: Sequence[float], z_scale: float,
                   min_building_m: float, out_png: Path, out_pdf: Path | None, *,
                   show_nests: bool = True) -> None:
    """标题带 + 场景 + 图例带 三段式画布：注记不压主体，字号与对齐可控。"""
    width, band_top = CANVAS_W, 250
    band_bottom = max(300, 130 + 64 * len(legend))  # 条目多时图例带自动加高，别裁掉最后一条
    scene_img, box = _trim(image)
    scale_px = width / float(scene_img.width)
    scene = scene_img.resize((width, int(round(scene_img.height * scale_px))), Image.LANCZOS)
    canvas = Image.new("RGB", (width, band_top + band_bottom + scene.height), "white")
    canvas.paste(scene, (0, band_top))

    draw = ImageDraw.Draw(canvas)
    f_title = ImageFont.truetype(FONT_BOLD, 72)
    f_sub = ImageFont.truetype(FONT_BODY, 40)
    f_body = ImageFont.truetype(FONT_BODY, 40)
    f_tag = ImageFont.truetype(FONT_BODY, 34)
    f_note = ImageFont.truetype(FONT_BODY, 28)

    text(draw, (72, 34), title, f_title, "#111111")
    if subtitle:
        text(draw, (74, 138), subtitle, f_sub, "#555555")

    # 点位注记：文字带白描边，压在楼群上也读得清；位置按裁剪+缩放后的画布坐标换算。
    # 多个停留点可能投影到同一块像素上（同层悬停、让路前后重合都常见），这里按"先到先占"避让。
    placed: list[tuple[float, float, float, float]] = []
    for (x, y), label in tags:
        px = (x - box[0]) * scale_px + 28
        py = (y - box[1]) * scale_px + band_top - 16
        tw = float(draw.textlength(label, font=f_tag)) + 10.0
        for dx, dy in ((0, 0), (0, -46), (0, 46), (-tw - 46, 0), (0, -92), (0, 92)):
            item = (px + dx, py + dy, px + dx + tw, py + dy + 40)
            if any(boxes_overlap(item, other) for other in placed):
                continue
            placed.append(item)
            text(draw, (px + dx, py + dy), label, f_tag, "#1b1b1b", halo=5)
            break

    # 比例尺（场景左下）：按可取整的档位选，别让尺条戳出画布（放大图里 1 km 常常太长）
    metres = next((m for m in (1000, 500, 200, 100)
                   if scale * m / 1000.0 <= width * 0.45), 100)
    bar = int(round(scale * metres / 1000.0))
    bx, by = 110, band_top + scene.height - 110
    draw.line([(bx, by), (bx + bar, by)], fill="#333333", width=6)
    for xx in (bx, bx + bar, bx + bar // 2):
        draw.line([(xx, by - 10), (xx, by + 10)], fill="#333333", width=6)
    text(draw, (bx, by - 62), "1 km" if metres == 1000 else f"{metres} m",
         f_tag, "#333333", halo=5)

    # 指北针（场景右上）：方向由投影后的 +y 单位向量给出，跟着相机转
    nd = np.asarray(north_dir, dtype=float)
    nd = nd / max(float(np.linalg.norm(nd)), 1e-6)
    nx, ny = width - 170, band_top + 175
    tip, tail = np.array([nx, ny]) + nd * 62.0, np.array([nx, ny]) - nd * 62.0
    side = np.array([-nd[1], nd[0]])
    draw.line([tuple(tail), tuple(tip)], fill="#333333", width=6)
    draw.polygon([tuple(tip), tuple(tip - nd * 26 + side * 15),
                  tuple(tip - nd * 26 - side * 15)], fill="#333333")
    text(draw, tuple(np.array([nx, ny]) + nd * 86.0 - np.array([16, 40])), "北", f_tag,
         "#333333", halo=5)

    # 图例带：色条 + 文字（左列），符号说明（右列）
    lx, ly = 74, band_top + scene.height + 42
    for label, color in legend:
        draw.rounded_rectangle([lx, ly + 18, lx + 96, ly + 34], radius=4, fill=color)
        text(draw, (lx + 118, ly + 2), label, f_body, "#222222")
        ly += 64
    sym_x, sy = lx + 1180, band_top + scene.height + 48
    if show_nests:
        draw.polygon([(sym_x, sy), (sym_x + 17, sy + 17), (sym_x, sy + 34), (sym_x - 17, sy + 17)],
                     fill=NEST_COLOR)
        text(draw, (sym_x + 34, sy + 1), "机巢", f_body, "#222222")
        tx = sym_x + 236
    else:
        tx = sym_x
    # 符号与图上几何一致：停靠点是菱形标记，不是方块
    draw.polygon([(tx + 20, sy + 4), (tx + 36, sy + 20), (tx + 20, sy + 36), (tx + 4, sy + 20)],
                 fill=TASK_COLOR)
    text(draw, (tx + 58, sy + 1), "任务停靠点（编号＝任务 id）", f_body, "#222222")

    note = (f"取景 {bounds[2] - bounds[0]:.0f}×{bounds[3] - bounds[1]:.0f} m，"
            f"{'含全部机巢与' if show_nests else ''}"
            f"{'全部建筑' if min_building_m <= 0 else f' ≥{min_building_m:g} m 建筑'}；"
            f"竖向放大 {z_scale:g}× 仅作阅读约定，不改数据口径")
    text_right(draw, (width - 72, band_top + scene.height + band_bottom - 62), note,
               f_note, "#666666")

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


def text(draw, xy, s: str, font, fill, halo: int = 0) -> None:
    draw.text(xy, s, font=font, fill=fill,
              stroke_width=halo, stroke_fill="white" if halo else None)


def text_right(draw, xy, s: str, font, fill) -> None:
    w = draw.textlength(s, font=font)
    draw.text((xy[0] - w, xy[1]), s, font=font, fill=fill)


def boxes_overlap(a, b) -> bool:
    """两个注记框是否相交（注记避让用）。"""
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
