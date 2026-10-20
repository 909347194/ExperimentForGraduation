# -*- coding: utf-8 -*-
"""绘图版面自检（figure audit）。

为什么需要它（2026-10-13 连续三次同类线上事故）：

1. ``allocation_anim`` 脚注单行不折行 → ``fig.text`` 不换行，超宽文本既溢出
   画布右缘，又把 ``bbox_inches="tight"`` 的包围盒撑到 **2.25× 画布宽**，
   出图右半张全白（P03/P04/P06 同此病灶）。
2. ``dmde_search`` / ``allocation_anim`` 的收敛曲线「先画整条序列、再
   ``set_xlim`` 截断」→ ``Line2D`` 的 extent **不受坐标轴裁剪**，照样计入
   tight bbox（实测撑到 4.77×）。
3. 状态文字按 **pt 定宽**、容器按 **英寸/轴宽比例定宽** → 文字比卡片宽
   1.48 in，溢出后压在任务标号上，视觉上就是「文字堆叠」。

前两类由 :func:`check_bbox_overflow` 守着；**第三类它守不住**——文字仍在
画布内、tight bbox 也正常，只是压住了别的 artist。本模块补上这一类：

- :func:`audit_figure`：静态图的版面体检（文本越容器 / 文本互压 / 数据越窗 /
  子图互压 / 画布越界）；
- :func:`audit_frame`：动画**逐帧**体检（默认关闭，由环境变量
  ``EXP01_AUDIT_FRAMES=1`` 开启，逐帧全检；``=sample:N`` 抽样每 N 帧）。

设计取向：**只诊断、不修图**。修图必须改绘图代码；护栏的职责是让问题在
出图那一刻就有告警，而不是等人眼看 PNG 发现。
"""
from __future__ import annotations

import os
from typing import Iterable

__all__ = [
    "AUDIT_ENABLED",
    "check_bbox_overflow",
    "audit_figure",
    "audit_frame",
    "iter_problems",
]

# 逐帧自检开关：默认关闭（动画帧数多，全检会增加出图耗时）。
#   EXP01_AUDIT_FRAMES=1         → 逐帧全检
#   EXP01_AUDIT_FRAMES=sample:5  → 每 5 帧抽一帧
_RAW_ENV = os.environ.get("EXP01_AUDIT_FRAMES", "0").strip().lower()
AUDIT_SAMPLE = 0
if _RAW_ENV.startswith("sample:"):
    try:
        AUDIT_SAMPLE = max(1, int(_RAW_ENV.split(":", 1)[1]))
    except ValueError:
        AUDIT_SAMPLE = 0
AUDIT_ENABLED = _RAW_ENV not in ("", "0", "false", "off", "no")

# ── 阈值（凭 exp01 实测标定，勿随意放大）───────────────────────────
_TOL_CANVAS = 1.02      # tight bbox / 画布英寸宽，超过即撑大出图幅面
_TOL_TEXTBOX = 0.06     # 文本允许溢出所属 axes 边界的比例（吸收 bbox padding）
_OVERLAP_MIN = 0.22     # 两文本重叠面积占较小者的比例，超过判定为「互压」
_LINE_BEYOND = 1.60     # Line2D 数据范围 / 视窗范围，超过判定为「数据越窗」


def _renderer(fig):
    try:
        fig.canvas.draw()
        return fig.canvas.get_renderer()
    except Exception:  # 无渲染器（极少）→ 自检整体放弃，绝不阻断出图
        return None


def _texts_of(fig) -> list[tuple]:
    """收集所有可见文本 artist（排除 tick label —— 它们由布局器排布）。"""
    out: list[tuple] = []
    for ax in list(fig.axes):
        tick_txt = set()
        try:  # 刻度文本由布局器排布且不属于「内容」，不计入内容构造
            tick_txt |= {id(t) for t in ax.get_xticklabels()}
            tick_txt |= {id(t) for t in ax.get_yticklabels()}
        except Exception:
            pass
        for art in (ax.title, ax._left_title, ax._right_title, *ax.texts):
            if art is None or not getattr(art, "get_visible", lambda: True)():
                continue
            if id(art) in tick_txt:
                continue
            try:
                s = str(art.get_text())
            except Exception:
                continue
            if not s.strip():
                continue
            out.append((art, ax, s))
    # 画布级文本（脚注 / suptitle 等）
    for art in fig.texts:
        try:
            s = str(art.get_text())
        except Exception:
            continue
        if s.strip():
            out.append((art, None, s))
    return out


def check_bbox_overflow(fig, *, label: str = "", tol: float = _TOL_CANVAS) -> bool:
    """画布级越界自检（保持向后兼容的调用签名，内部委托 :func:`audit_figure`）。

    历史原因：早期只有 tight bbox 一项检查，函数以此得名；现由 :func:`audit_figure`
    统一实现，这里保留入口以免改动全部调用点。
    """
    problems = audit_figure(fig, label=label, canvas_only=True)
    return any(p[0] == "canvas" for p in problems)


def iter_problems(fig, *, label: str = ""):
    """返回版面问题列表 ``[(kind, detail, severity), ...]``，不打印。"""
    return audit_figure(fig, label=label, verbose=False)


def _is_twin(a, b) -> bool:
    """是否为**有意同域叠加**（twinx/twiny 或透明叠加层），不算缺陷。

    两类叠加都是设计使然：

    1. 双轴：``_twinned_axes`` 是 ``matplotlib.cbook.Grouper``，**没有**
       ``.values()``（有 ``get_siblings``）——直接调 values() 会抛
       AttributeError 且被 try/except 吞掉，判定恒 False，twinx 全部误报；
    2. 动画叠加层：``fig.add_axes(ax.get_position(), frameon=False)`` 做的
       透明内容层（P06 的 ax_anim 叠在 ax_map 上）——位置重合但非 twin，
       靠「位置重合 + 其一无背景框」识别。
    """
    try:
        for host, other in ((a, b), (b, a)):
            grp = getattr(host, "_twinned_axes", None)
            if grp is not None and hasattr(grp, "get_siblings"):
                if other in grp.get_siblings(host):
                    return True
    except Exception:
        pass
    try:
        pa, pb = a.get_position(), b.get_position()
        same_box = (abs(pa.x0 - pb.x0) < 0.01 and abs(pa.width - pb.width) < 0.01
                    and abs(pa.y0 - pb.y0) < 0.01 and abs(pa.height - pb.height) < 0.01)
        if not same_box:
            return False
        overlay = (not a.get_frame_on()) or (not b.get_frame_on())
        return bool(overlay
                    or a.get_shared_x_axes().joined(a, b)
                    or a.get_shared_y_axes().joined(a, b))
    except Exception:
        return False


def _is_title_of(art, ax) -> bool:
    try:
        return art in (ax.title, ax._left_title, ax._right_title)
    except Exception:
        return False


def audit_figure(fig, *, label: str = "", verbose: bool = True,
                 canvas_only: bool = False, tol: float = _TOL_CANVAS):
    """静态图版面体检，返回 ``[(kind, detail, severity), ...]``。

    kind 取值：``canvas``（tight bbox 撑爆画布）/ ``textbox``（文本溢出所属
    axes）/ ``overlap``（文本互压）/ ``beyond-view``（数据越出视窗，会撑大
    bbox）/ ``axes-overlap``（子图区域相叠）。
    """
    renderer = _renderer(fig)
    if renderer is None:
        return []
    problems: list[tuple[str, str, str]] = []

    try:
        tb = fig.get_tightbbox(renderer)
        fw, fh = fig.get_size_inches()
        W_px = fig.canvas.get_width_height()[0]
    except Exception:
        return []

    # ── 1) 画布级：tight bbox 是否撑爆画布 ──────────────────────────
    canvas_bad = False
    if tb is not None and fw > 0 and fh > 0:
        sx, sy = tb.x1 / fw, tb.y1 / fh
        if (sx > tol or sy > tol or tb.x0 < -0.02 * fw or tb.y0 < -0.02 * fh):
            canvas_bad = True
            offenders: list[tuple[float, str]] = []

            def _walk(artist) -> None:
                try:
                    bb = artist.get_window_extent(renderer)
                except Exception:
                    bb = None
                if bb is not None and (bb.x1 > W_px * tol or bb.y1 > W_px * tol):
                    snippet = ""
                    try:
                        snippet = str(artist.get_text())[:32]
                    except Exception:
                        snippet = ""
                    offenders.append((max(bb.x1, bb.y1),
                                      f"{type(artist).__name__} {snippet!r}"))
                for ch in getattr(artist, "get_children", lambda: [])():
                    _walk(ch)

            _walk(fig)
            offenders.sort(reverse=True)
            shown = ", ".join(s for _, s in offenders[:3]) or "（无直接 artist）"
            problems.append((
                "canvas",
                f"内容越出画布 ×{sx:.2f}（高 ×{sy:.2f}）：{shown}",
                "error",
            ))
    if canvas_only:
        if verbose and canvas_bad:
            _print_all(label, problems)
        return problems

    # ── 2) 文本是否溢出所属容器 ─────────────────────────────────────
    boxes: list[tuple] = []
    for art, ax, s in _texts_of(fig):
        try:
            bb = art.get_window_extent(renderer)
        except Exception:
            continue
        boxes.append((art, ax, s, bb))
        if ax is not None:
            try:
                host = ax.get_window_extent(renderer)
            except Exception:
                continue
            if _is_title_of(art, ax):
                # 轴标题居于轴区**上方**，纵向超出是布局约定而非缺陷；只查横向
                # 是否宽到会越过自己的地盘、压到相邻子图（这才真会糊）。
                if bb.width > host.width * (1.0 + _TOL_TEXTBOX):
                    problems.append((
                        "textbox",
                        f"轴标题超出本子图宽度 {bb.width / host.width:.2f}×（会压到相邻"
                        f"子图）：{s[:44]!r}",
                        "error",
                    ))
                continue
            ov_w = max(0.0, bb.x1 - host.x1) + max(0.0, host.x0 - bb.x0)
            ov_h = max(0.0, bb.y1 - host.y1) + max(0.0, host.y0 - bb.y0)
            rel = max(ov_w / max(host.width, 1.0), ov_h / max(host.height, 1.0))
            if rel > _TOL_TEXTBOX:
                problems.append((
                    "textbox",
                    f"文本溢出所属子图 {rel * 100:.0f}%：{s[:38]!r}"
                    f"（子图「{_axes_name(ax)}」）",
                    "error",
                ))
        else:
            # 画布级文本：不能超出画布
            if bb.x1 > W_px * 1.005 or bb.x0 < -0.005 * W_px:
                problems.append((
                    "textbox",
                    f"画布级文本超出画布右缘（x1={bb.x1:.0f} / 画布 {W_px:.0f}px）："
                    f"{s[:38]!r}",
                    "error",
                ))

    # ── 3) 文本互压（同一子图内）───────────────────────────────────
    by_ax: dict = {}
    for art, ax, s, bb in boxes:
        by_ax.setdefault(id(ax), []).append((art, s, bb))
    for _, items in by_ax.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                _, si, bi = items[i]
                _, sj, bj = items[j]
                iw = min(bi.x1, bj.x1) - max(bi.x0, bj.x0)
                ih = min(bi.y1, bj.y1) - max(bi.y0, bj.y0)
                if iw <= 0 or ih <= 0:
                    continue
                inter = iw * ih
                small = min((bi.x1 - bi.x0) * (bi.y1 - bi.y0),
                            (bj.x1 - bj.x0) * (bj.y1 - bj.y0))
                if small <= 0:
                    continue
                rel = inter / small
                if rel > _OVERLAP_MIN:
                    problems.append((
                        "overlap",
                        f"文本互压 {rel * 100:.0f}%：{si[:26]!r} ↔ {sj[:26]!r}",
                        "error",
                    ))

    # ── 4) 数据越出视窗（Line2D extent 不被裁剪 → 撑大 bbox）────────
    for ax in fig.axes:
        try:
            x0, x1 = ax.get_xlim()
            y0, y1 = ax.get_ylim()
        except Exception:
            continue
        span_x, span_y = abs(x1 - x0), abs(y1 - y0)
        if span_x <= 0 or span_y <= 0:
            continue
        for line in ax.lines:
            try:
                xd, yd = line.get_xdata(), line.get_ydata()
            except Exception:
                continue
            if len(xd) == 0 or len(yd) == 0:
                continue
            try:
                lx = max(float(xd.max()), ) if hasattr(xd, "max") else max(map(float, xd))
                nx = min(map(float, xd))
                ly = max(map(float, yd))
                ny = min(map(float, yd))
            except Exception:
                continue
            overshoot = max((lx - nx) / span_x, (ly - ny) / span_y)
            if overshoot > _LINE_BEYOND:
                problems.append((
                    "beyond-view",
                    f"数据范围 ×{overshoot:.1f} 超出视窗（子图「{_axes_name(ax)}」）："
                    f"Line2D 的 extent 不受 xlim/ylim 裁剪，会计入 tight bbox",
                    "error",
                ))

    # ── 5) 子图区域相叠 ────────────────────────────────────────────
    axs = list(fig.axes)
    for i in range(len(axs)):
        for j in range(i + 1, len(axs)):
            ai, aj = axs[i], axs[j]
            try:  # inset_axes 与主图、twinx 双轴天然同域，跳过
                if _is_inset(ai) or _is_inset(aj) or _is_twin(ai, aj):
                    continue
                bi = ai.get_window_extent(renderer)
                bj = aj.get_window_extent(renderer)
            except Exception:
                continue
            iw = min(bi.x1, bj.x1) - max(bi.x0, bj.x0)
            ih = min(bi.y1, bj.y1) - max(bi.y0, bj.y0)
            if iw > 2 and ih > 2:
                inter = iw * ih
                small = min((bi.x1 - bi.x0) * (bi.y1 - bi.y0),
                            (bj.x1 - bj.x0) * (bj.y1 - bj.y0))
                if small > 0 and inter / small > 0.05:
                    problems.append((
                        "axes-overlap",
                        f"子图区域相叠 {inter / small * 100:.0f}%："
                        f"「{_axes_name(ai)}」↔「{_axes_name(aj)}」",
                        "warn",
                    ))

    if verbose:
        _print_all(label, problems)
    return problems


def audit_frame(fig, index: int) -> None:
    """动画逐帧自检：默认静默；``EXP01_AUDIT_FRAMES`` 开启后按抽样策略体检。

    为什么必须能查**每一帧**：静态图只出一张，出了问题一眼能看到；动画的
    中间帧一旦出问题（例如某帧状态文字变长后压住任务标号），poster 的末帧
    完全正常，只有播放时才发现。
    """
    if not AUDIT_ENABLED:
        return
    if AUDIT_SAMPLE and (index % AUDIT_SAMPLE != 0):
        return
    audit_figure(fig, label=f"frame#{index}", verbose=True)


def _is_inset(ax) -> bool:
    try:  # inset_axes 有此内部属性
        return getattr(ax, "_inset_parent", None) is not None
    except Exception:
        return False


def _axes_name(ax) -> str:
    try:
        t = ax.get_title() or ax.get_ylabel() or ""
        return str(t)[:22] or "(无标题)"
    except Exception:
        return "(无标题)"


def _print_all(label: str, problems: Iterable[tuple]) -> None:
    problems = list(problems)
    if not problems:
        return
    name = f"「{label}」" if label else ""
    for kind, detail, sev in problems[:8]:
        mark = "⚠" if sev == "warn" else "✗"
        print(f"[exp01] {mark} 版面自检{name} [{kind}] {detail}")
    if len(problems) > 8:
        print(f"[exp01]    …另有 {len(problems) - 8} 条同类问题")
