# -*- coding: utf-8 -*-
"""标签防重叠：出图前把互相压字的 ``Annotation`` 自动挪开。

为什么要它
----------
同一片地图上要同时打三类标号：任务序号 ``1…30``、机巢 ``巢0``、航次尾注
``机6 · 10 站``。它们各自按固定像素偏移摆放，彼此不知道对方在哪，于是十位数
标号一定压成一团——这正是旧 P01/P05 读不出信息的原因之一。

做法
----
对本 axes 内所有带文字的 ``Annotation`` 做一次贪心排布：

1. 面积大的先落位（面板标题/巢标号该优先，任务小标号让路）；
2. 与已落位包围盒算 IoA（相交面积 / 较小者面积），超过阈值即冲突；
3. 冲突时按「最小扰动优先」试候选偏移：先沿原方向加长，再绕锚点转 45°，
   最后才加半径；
4. 候选位置必须先落在本子图内（避免标号被顶到相邻面板）；
5. 仍摆不下就回原位——宁可少量重叠，也不让标号飞离锚点。

只改像素偏移与对齐方式，**不动语义、不动数据点**。
"""

from __future__ import annotations

__all__ = ["declutter"]

#: (缩放, 旋转角度) 搜索顺序：先原方向加长，再逐步绕锚点转。
_STEPS = (
    (1.45, 0), (1.45, 45), (1.45, -45), (1.0, 90), (1.0, -90),
    (2.05, 0), (2.05, 45), (2.05, -45), (2.05, 90), (2.05, -90),
    (2.75, 0), (2.75, 45), (2.75, -45), (2.75, 135), (2.75, -135),
)

#: 判为压字的相交比例阈值（相交面积 / 较小者面积）。
_IOA_BAD = 0.20


def _ioa(a, b) -> float:
    iw = min(a.x1, b.x1) - max(a.x0, b.x0)
    ih = min(a.y1, b.y1) - max(a.y0, b.y0)
    if iw <= 0 or ih <= 0:
        return 0.0
    sa = max((a.x1 - a.x0) * (a.y1 - a.y0), 1e-9)
    sb = max((b.x1 - b.x0) * (b.y1 - b.y0), 1e-9)
    return iw * ih / min(sa, sb)


def _is_label(art) -> bool:
    from matplotlib.text import Annotation

    return isinstance(art, Annotation) and bool(str(art.get_text()).strip())


def _get(art) -> tuple[float, float]:
    """读像素偏移；matplotlib 版本差异使属性名不同，按存在性依次探测。"""
    for name in ("xyann", "_xytext", "xytext"):
        v = getattr(art, name, None)
        if v is not None:
            try:
                return float(v[0]), float(v[1])
            except Exception:
                continue
    return 0.0, 0.0


def _set(art, dx: float, dy: float) -> bool:
    for name in ("xyann", "_xytext", "xytext"):
        if hasattr(art, name):
            try:
                setattr(art, name, (dx, dy))
                return True
            except Exception:
                continue
    return False


def _box(art, renderer):
    try:
        art.update_bbox_position_size(renderer)
    except Exception:
        pass
    try:
        return art.get_window_extent(renderer)
    except Exception:
        return None


def declutter(fig, *, ax_filter=None, min_labels: int = 2) -> int:
    """整理整张图的标注，返回被挪动的个数。``ax_filter(ax)`` 可限定只处理部分子图。"""
    import math

    try:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
    except Exception:
        return 0

    moved_total = 0
    for ax in list(fig.axes):
        if ax_filter is not None and not ax_filter(ax):
            continue
        anns = [a for a in getattr(ax, "texts", []) if _is_label(a)]
        if len(anns) < min_labels:
            continue
        anns.sort(key=lambda a: (lambda b: 0.0 if b is None else (b.x1 - b.x0) * (b.y1 - b.y0))(
            _box(a, renderer)), reverse=True)

        try:
            host = ax.get_window_extent(renderer)
        except Exception:
            host = None

        def inside(b) -> bool:
            if host is None or b is None:
                return True
            px, py = 0.02 * host.width, 0.02 * host.height
            return (b.x0 >= host.x0 - px and b.x1 <= host.x1 + px
                    and b.y0 >= host.y0 - py and b.y1 <= host.y1 + py)

        placed: list = []
        for ann in anns:
            dx0, dy0 = _get(ann)
            box = _box(ann, renderer)
            if box is None:
                placed.append(None)
                continue
            worst = max((_ioa(box, p) for p in placed if p is not None), default=0.0)
            if worst <= _IOA_BAD and inside(box):
                placed.append(box)
                continue

            solved = False
            best_box, best_worst = None, None
            for scale, turn in _STEPS:
                th = math.atan2(dy0, dx0) + math.radians(turn)
                r = (math.hypot(dx0, dy0) or 10.0) * scale
                dx, dy = r * math.cos(th), r * math.sin(th)
                if not _set(ann, dx, dy):
                    continue
                try:
                    ann.set_ha("left" if dx > 0.5 else "right" if dx < -0.5 else "center")
                    ann.set_va("bottom" if dy > 0.5 else "top" if dy < -0.5 else "center")
                except Exception:
                    continue
                nb = _box(ann, renderer)
                if nb is None:
                    continue
                wb = max((_ioa(nb, p) for p in placed if p is not None), default=0.0)
                if wb <= _IOA_BAD and inside(nb):
                    placed.append(nb)
                    moved_total += 1
                    solved = True
                    break
                if inside(nb) and (best_worst is None or wb < best_worst):
                    best_box, best_worst = nb, wb
            if solved:
                continue
            if best_box is not None:
                placed.append(best_box)
                moved_total += 1
            else:
                _set(ann, dx0, dy0)
                placed.append(_box(ann, renderer))
    if moved_total:
        try:
            fig.canvas.draw()
        except Exception:
            pass
    return moved_total
