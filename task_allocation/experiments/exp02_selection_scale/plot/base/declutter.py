# -*- coding: utf-8 -*-
"""标签去重叠（declutter）——把互相压字的 ``annotate`` 自动挪开。

为什么要有它
------------
地图类图（P03 / P05 / P06 动画 / P08）在同一片地理区域里同时打三类标号：
UAV 起飞点 ``U#``、机巢 ``巢#``、任务 ``T#·Ck``（外加甘特里的收益数字）。它们
按**各自的固定像素偏移**摆放，彼此并不知道对方在哪，于是：

* 同一机巢起飞的两架 UAV（U0 / U8 都在巢 0）标号坐标完全相同 → **100% 相叠**，
  看上去只有一个标号；
* UAV 标号落在右上、部分机巢标号也在右上 → 压字 34%；
* 相邻任务点的任务标号与邻点收益数字 → 压字 56%。

这些都是 2026-10-13 版面自检（``plot.base.audit``）报出来的真实缺陷。靠人手
逐个微调偏移治不了根——换一份数据（更多 UAV、更密的巢）又会错开。

做法
----
在**出图前**（帧已 draw）对本 axes 内所有 ``Annotation`` 做一次贪心排布：

1. 按当前 ``xytext`` 计算窗口包围盒；
2. 与已放置的集合做 IoA（相交面积占较小者的比例）检测；
3. 冲突时按「**最小扰动优先**」依次尝试候选偏移——先沿原偏转方向加长，
   再绕锚点旋转到相邻象限，最后才加大半径；
4. 仍摆不下就保留原位（宁可少量重叠，也不让标号飘到离锚点很远的地方）。

只动 ``xytext``（像素偏移）与对齐方式，**不改语义、不改数据点**。
"""
from __future__ import annotations

from itertools import combinations

__all__ = [
    "CANDIDATE_STEPS",
    "declutter_axes",
    "declutter_figure",
    "fit_axis_titles",
]

#: 候选搜索顺序：``(scale, turn_deg)`` —— 先尝试原方向加长，再逐步绕锚点旋转。
#: 角度取 45° 的倍数；这样 8 个象限都覆盖，且第一步总是「最小扰动」。
CANDIDATE_STEPS = (
    (1.45, 0), (1.45, 45), (1.45, -45), (1.00, 90), (1.00, -90),
    (2.05, 0), (2.05, 45), (2.05, -45), (2.05, 90), (2.05, -90),
    (2.75, 0), (2.75, 45), (2.75, -45), (2.75, 135), (2.75, -135),
)

#: 判定为「压字」的相交比例阈值（相交面积 / 较小者面积）。
#: 取值略低于 ``audit._OVERLAP_MIN``(0.22)——保证「自检不再报」而不是「刚好压线」。
_IOA_BAD = 0.20


def _ioa(a, b) -> float:
    """相交面积占较小包围盒的比例（boxes 为 matplotlib Bbox，窗口坐标）。"""
    iw = min(a.x1, b.x1) - max(a.x0, b.x0)
    ih = min(a.y1, b.y1) - max(a.y0, b.y0)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    sa = max((a.x1 - a.x0) * (a.y1 - a.y0), 1e-9)
    sb = max((b.x1 - b.x0) * (b.y1 - b.y0), 1e-9)
    return inter / min(sa, sb)


def _ann_of(art) -> bool:
    """是否是可挪动的标注（带 offset-points 的 Annotation，且有文字）。"""
    try:
        from matplotlib.text import Annotation
    except Exception:  # pragma: no cover
        return False
    if not isinstance(art, Annotation):
        return False
    try:
        return bool(str(art.get_text()).strip())
    except Exception:
        return False


def _get_offset(art) -> tuple[float, float]:
    """读标注的像素偏移。

    注意 matplotlib 版本差异：``Annotation`` 的偏移属性在部分版本叫 ``xyann``
    （3.11 实测），另有版本走 ``_xytext``/``xytext``。这里按存在性依次探测，
    取不到就当作无偏移——**探测失败不能让去重叠整体崩溃**。
    """
    for name in ("xyann", "_xytext", "xytext"):
        v = getattr(art, name, None)
        if v is not None:
            try:
                return float(v[0]), float(v[1])
            except Exception:
                continue
    return 0.0, 0.0


def _set_offset(art, dx: float, dy: float) -> bool:
    """写回像素偏移，成功返回 True。"""
    for name in ("xyann", "_xytext", "xytext"):
        if hasattr(art, name):
            try:
                setattr(art, name, (dx, dy))
                return True
            except Exception:
                continue
    return False


def _polish_box(art, renderer):
    """取该 artist 当前的窗口包围盒（含 bbox 衬底 padding）。"""
    try:
        art.update_bbox_position_size(renderer)
        return art.get_window_extent(renderer)
    except Exception:
        try:
            return art.get_window_extent(renderer)
        except Exception:
            return None


def declutter_axes(ax, renderer) -> int:
    """整理单个 axes 的标注，返回被挪动的标注个数。"""
    anns = [a for a in getattr(ax, "texts", []) if _ann_of(a)]
    if len(anns) < 2:
        return 0
    # 固定件优先：面积越大越先落位（面板标题/巢标号比 UAV 标号更该让路）
    def _area(a):
        bb = _polish_box(a, renderer)
        return 0.0 if bb is None else (bb.x1 - bb.x0) * (bb.y1 - bb.y0)

    anns.sort(key=_area, reverse=True)

    placed: list = []
    moved = 0
    # 「不许把标号推出子图」—— 只看冲突会把边缘标号顶到相邻面板上去，
    # 于是旧的地图图（P03/P05）出现了「巢1 / T14·C2 溢出所属子图 7~9%」。
    # 候选位置必须先通过「仍在 host 内」的准入，再谈互不重叠。
    try:
        host = ax.get_window_extent(renderer)
    except Exception:
        host = None

    def _inside(box) -> bool:
        if host is None or box is None:
            return True
        pad_x, pad_y = 0.02 * host.width, 0.02 * host.height
        return (box.x0 >= host.x0 - pad_x and box.x1 <= host.x1 + pad_x
                and box.y0 >= host.y0 - pad_y and box.y1 <= host.y1 + pad_y)

    for ann in anns:
        dx0, dy0 = _get_offset(ann)
        box = _polish_box(ann, renderer)
        if box is None:
            placed.append(None)
            continue
        worst = max((_ioa(box, p) for p in placed if p is not None), default=0.0)
        # 出界也触发重排：地图边缘的标号即便不压别人，也会被自己的边界裁掉
        # 或压到相邻地图（P03 的「巢1 / T14·C2」即属此类）。
        need_move = worst > _IOA_BAD or not _inside(box)
        if not need_move:
            placed.append(box)
            continue

        solved = False
        fallback_nb, fallback_worst = None, None
        import math

        for scale, turn in CANDIDATE_STEPS:
            th = math.atan2(dy0, dx0) + math.radians(turn)
            r0 = math.hypot(dx0, dy0) or 10.0
            r = r0 * scale
            dx, dy = r * math.cos(th), r * math.sin(th)
            try:
                if not _set_offset(ann, dx, dy):
                    continue
                ann.set_ha("left" if dx > 0.5 else "right" if dx < -0.5 else "center")
                ann.set_va("bottom" if dy > 0.5 else "top" if dy < -0.5 else "center")
            except Exception:
                continue
            nb = _polish_box(ann, renderer)
            if nb is None:
                continue
            wb = max((_ioa(nb, p) for p in placed if p is not None), default=0.0)
            if wb <= _IOA_BAD and _inside(nb):
                placed.append(nb)
                moved += 1
                solved = True
                break
            if _inside(nb) and (fallback_worst is None or wb < fallback_worst):
                fallback_nb, fallback_worst = nb, wb
        if solved:
            continue
        if fallback_nb is not None:  # 摆在子图内、冲突最小的位置
            placed.append(fallback_nb)
            moved += 1
            continue
        _set_offset(ann, dx0, dy0)   # 摆不下 → 回原位，宁可少量重叠也不推出子图
        placed.append(_polish_box(ann, renderer))
    return moved


def _text_width_in(text: str, fontsize_pt: float) -> float:
    """估算文本物理宽度（inch）——与 ``theme._wrap_to_width`` 同一套口径。

    不直接引用 theme 是为了避开循环导入（theme → base.declutter → theme）。
    """
    import unicodedata

    em = fontsize_pt / 72.0
    return sum(
        em if unicodedata.east_asian_width(ch) in ("W", "F") else em * 0.52
        for ch in str(text)
    )


def _wrap(text: str, avail_in: float, fontsize_pt: float) -> str:
    """按可用宽度贪心折行。

    断点优先级：**空格 > 逐字符**。公式/守恒式标题里含 ``n_selected`` 这类
    英文标识符，逐字符断行会把单词拦腰切成 ``n_selecte`` / ``d``（2026-10-13
    P01(d) 实测），比不折行更难看；先尝试在最后一个空格处断，整段无空格
    （如纯中文标题）才退回逐字符。
    """
    budget = avail_in * 0.88          # 安全余量（估宽偏松，实测约 1.13×）
    out: list[str] = []
    for seg in str(text).split("\n"):
        if not seg:
            out.append("")
            continue
        while _text_width_in(seg, fontsize_pt) > budget and (" " in seg.strip()):
            # 找最后一个使前缀不超预算的空格
            cut = -1
            acc = 0.0
            for i, ch in enumerate(seg):
                w = _text_width_in(ch, fontsize_pt)
                if acc + w > budget:
                    break
                acc += w
                if ch == " ":
                    cut = i
            if cut <= 0:
                break
            out.append(seg[:cut])
            seg = seg[cut + 1:]
        # 剩余段（或无空格段）逐字符断
        line: list[str] = []
        width = 0.0
        for ch in seg:
            ch_w = _text_width_in(ch, fontsize_pt)
            if line and width + ch_w > budget:
                out.append("".join(line))
                line, width = [ch], ch_w
            else:
                line.append(ch)
                width += ch_w
        if line:
            out.append("".join(line))
    return "\n".join(out)


def fit_axis_titles(fig) -> int:
    """把超出子图宽度的**轴标题**就地折行，返回被折行的标题数。

    为什么不用 matplotlib 自带的 ``set_title(wrap=True)``：它对中英混排只在
    空白与 ASCII 词边界断行，长英文 token（``n_selected_and_rejected``）整词
    不断，实测一条 2.52× 的标题只折到 **1.35×**，仍然压到相邻子图。这里改用
    逐字符估宽折行（与脚注同口径），可保证不超过可用宽度。

    放在出图管线统一执行而非依赖各图自己调用 ``style_axes``——这样
    ``ax.set_title`` 直接设置标题的图（如 P03/P05 的左定位标题）也一并受益。
    """
    changed = 0
    for ax in list(getattr(fig, "axes", [])):
        # 三个标题槽都要查：``setup_style`` 设了 ``axes.titlelocation="left"``，
        # 于是 ``set_title`` 写进 ``_left_title`` 而不是 ``title``——只查
        # ``ax.title`` 会全部漏掉（2026-10-13 调试发现）。
        slots = [getattr(ax, "title", None), getattr(ax, "_left_title", None),
                 getattr(ax, "_right_title", None)]
        for title in slots:
            if title is None:
                continue
            try:
                text = str(title.get_text())
                fs = float(title.get_fontsize())
                avail = float(fig.get_size_inches()[0]) * float(ax.get_position().width)
            except Exception:
                continue
            if not text.strip() or avail <= 0:
                continue
            widest = max((_text_width_in(seg, fs) for seg in text.split("\n")),
                         default=0.0)
            if widest <= avail * 0.98:
                continue
            try:
                title.set_text(_wrap(text, avail, fs))
                changed += 1
            except Exception:  # 折行失败绝不阻断出图
                continue
    return changed


def declutter_figure(fig, *, close_draw: bool = False) -> int:
    """整理整张图（含动画每一帧），返回被挪动的标注总数。

    ``close_draw`` 为 True 时会在整理后重新 ``draw`` 一次，使画布缓冲与调整后
    的位置一致（静态图出图前需要；动画抓帧前本身就是 draw，无需重复）。
    """
    try:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
    except Exception:
        return 0
    total = 0
    for ax in list(fig.axes):
        try:
            total += declutter_axes(ax, renderer)
        except Exception:  # 去重叠失败绝不阻断出图
            continue
    if close_draw and total:
        try:
            fig.canvas.draw()
        except Exception:
            pass
    return total
