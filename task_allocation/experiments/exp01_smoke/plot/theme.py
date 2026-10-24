# -*- coding: utf-8 -*-
"""exp01_smoke 绘图样式与保存约定（学术论文口径）。

本模块是**唯一** import ``pyplot`` 的地方：先切到无后端 ``Agg``，
再导入 pyplot，保证不会在有显示环境时误开窗口、也不会因后端切换告警。

其余绘图模块统一 ``from ..theme import plt, ...``，不直接 import pyplot。

## 学术绘图口径（2026-10-12 定稿）

论文版式：A4、左右边距各 3cm → **版心 15 cm ≈ 5.9 in**。因此：

    FIG_SINGLE  (5.9, h)   单栏图——进论文的默认宽度，figsize 字号即论文实际字号
    FIG_WIDE    (11.8, h)  通栏大图——甘特/矩阵/多周期横排等需要横向展开的图
    FIG_SQUARE  (5.9, 5.9) 单栏方图——地理小图、示意图

字号（图按 5.9 in 宽入版心时为 1:1，勿再放大）：

    base 8.5 / tick 8 / legend 8 / title 9.5 / suptitle 11 / footnote 7.5

配色：Tableau 10 派生（色盲友好、灰度打印可辨），语义色见 ``PALETTE``。
线宽 1.2~1.8、marker 3~5 pt；网格只留 y 轴浅色点线（x 轴留给分类刻度）。
PDF 输出用 TrueType（fonttype 42），保证嵌入与文本可搜索。
"""

from __future__ import annotations

import time
import unicodedata
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from .base import audit as _audit  # noqa: E402  （版面自检，见 check_bbox_overflow）
from .base import declutter as _declutter  # noqa: E402  （标签去重叠，见 save_figure）

__all__ = [
    "plt",
    "PALETTE",
    "MONO_ZH",
    "FIG_SINGLE",
    "FIG_WIDE",
    "FIG_SQUARE",
    "FS",
    "setup_style",
    "save_figure",
    "robust_savefig",
    "robust_pil_save",
    "check_bbox_overflow",
    "declutter_figure",
    "save_poster",
    "build_gif_palette",
    "cycle_colors",
    "style_axes",
    "footnote",
]

#: 优先使用的中文字体（按序回退；论文机器常见字体都列上）
_CJK_CANDIDATES = (
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Microsoft YaHei",
    "PingFang SC",
    "SimHei",
)

#: 中文等宽字体链——用于「参数/明细表」类文本面板。
#: matplotlib ≥3.6 支持按字形回退：ASCII（数字、列对齐）走 DejaVu Sans Mono，
#: 中文字形自动回退到链上下一个可用中文字体。直接写 ``family="monospace"``
#: 会落到 DejaVu Sans Mono 一种字体上，中文全部缺字（豆腐块）。
#: 2026-10-24 指标口径修复重跑时补 "Noto Sans Mono CJK SC"：Linux 沙箱无
#: Microsoft YaHei，等宽面板中文会整段缺字（按字形回退只在本列表内逐字体找，
#: 不会跨到 font.sans-serif 链）；从系统 NotoSansCJK-Regular.ttc 提出 SC 面
#: 装到 ~/.fonts 后该名可被 matplotlib 解析（ttc 集合默认只注册 JP 面）。
MONO_ZH = ["DejaVu Sans Mono", "Microsoft YaHei", "Noto Sans Mono CJK SC", "PingFang SC", "SimHei"]

#: 三档图幅（英寸）。论文版心 15 cm ≈ 5.9 in；通栏用于横向大面板。
FIG_SINGLE = 5.9
FIG_WIDE = 11.8
FIG_SQUARE = 5.9

#: 学术字号（pt）。图按 5.9 in 宽入版心时 1:1，即论文实际字号。
FS = {
    "base": 8.5,
    "tick": 8.0,
    "legend": 8.0,
    "title": 9.5,
    "suptitle": 11.0,
    "footnote": 7.5,
    "annotation": 8.0,
}

#: 统一配色：数据系列与语义色（Tableau 10 派生，色盲友好）
PALETTE = {
    "remain": "#9aa5b1",
    "new": "#4c78a8",
    "release": "#e45756",
    "selected": "#72b7b2",
    "executed": "#54a24b",
    "deferred": "#f58518",
    "reward": "#4c78a8",
    "cost": "#e45756",
    "solve": "#72b7b2",
    "evals": "#b279a2",
    "nest": "#4c78a8",
    "grid": "#d0d5da",
}

#: 多周期序列配色（tab10 前几色，足够 8 个周期）
_CYCLE_COLORS = (
    "#4c78a8",
    "#f58518",
    "#54a24b",
    "#e45756",
    "#72b7b2",
    "#b279a2",
    "#ff9da6",
    "#9d755d",
)


def setup_style() -> str:
    """应用全局样式，返回实际选中的字体名（便于日志确认中文可用）。"""
    available = {f.name for f in fm.fontManager.ttflist}
    chain = [n for n in _CJK_CANDIDATES if n in available]
    resolved = chain[0] if chain else "DejaVu Sans"

    plt.rcParams.update(
        {
            # ── 字体与负号 ──
            "font.family": "sans-serif",
            "font.sans-serif": [*chain, "DejaVu Sans"],
            "axes.unicode_minus": False,
            # ── 学术字号 ──
            "font.size": FS["base"],
            "axes.titlesize": FS["title"],
            "axes.titlelocation": "left",
            "axes.titlepad": 6.0,
            "axes.labelsize": FS["base"],
            "xtick.labelsize": FS["tick"],
            "ytick.labelsize": FS["tick"],
            "legend.fontsize": FS["legend"],
            # ── 网格：仅 y 轴浅点线（x 轴留给分类刻度） ──
            "axes.grid": True,
            "axes.grid.axis": "y",
            "grid.alpha": 0.25,
            "grid.linestyle": ":",
            "axes.axisbelow": True,
            # ── 脊线：去顶右，留左下（学术默认） ──
            "axes.spines.top": False,
            "axes.spines.right": False,
            # ── 图例：无框 ──
            "legend.frameon": False,
            # ── 线宽与标记 ──
            "lines.linewidth": 1.4,
            "lines.markersize": 4.0,
            "xtick.direction": "out",
            "ytick.direction": "out",
            # ── 保存：位图 300 dpi（印刷），PDF TrueType 可搜索 ──
            "figure.dpi": 110,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "savefig.facecolor": "white",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    return resolved


def cycle_colors(n: int) -> list[str]:
    """按周期数给出稳定配色（超过预置色数则循环）。"""
    return [_CYCLE_COLORS[i % len(_CYCLE_COLORS)] for i in range(max(int(n), 1))]


# 动画模块经 theme 统一取用排版工具（避免各自直接依赖 base 子模块的写法分叉）
declutter_figure = _declutter.declutter_figure


def check_bbox_overflow(fig, label: str = "", *, tol: float = 1.02) -> bool:
    """出图前自检：内容是否越出画布（左右/上下）——返回 True 表示有溢出。

    背景：``bbox_inches="tight"`` 按**所有 artist 的包围盒**裁图，而
    ``Line2D`` 的 extent 不受坐标轴裁剪影响（数据画到 xlim 之外照样计入），
    画布级 ``Text``（脚注）也一样。于是「画了但看不见」的内容会把裁切框撑大，
    出图成为异常宽/高的大幅面、四周大片空白（2026-10-13 实测 P03/P04/P06）。

    实现已下沉到 :mod:`plot.base.audit`（那里同时提供「文本越容器 / 文本互压」
    等更细的检查），此处仅保留向后兼容的入口。
    """
    return _audit.check_bbox_overflow(fig, label=label, tol=tol)


def audit_figure(fig, *, label: str = "", verbose: bool = True):
    """完整版面体检：越界 + 文本越容器 + 文本互压 + 数据越窗 + 子图相叠。

    由 :mod:`plot.base.audit` 实现；比 :func:`check_bbox_overflow` 多覆盖
    「文字虽然还在画布内、却压住了别的文字/容器」这一类（2026-10-13 的
    「文字堆叠」事故即属此类）。
    """
    return _audit.audit_figure(fig, label=label, verbose=verbose)


def audit_frame(fig, index: int) -> None:
    """动画逐帧版面体检（默认静默，``EXP01_AUDIT_FRAMES=1`` 开启）。

    见 :func:`plot.base.audit.audit_frame`：中间帧出问题时 poster 末帧往往
    完全正常，只有逐帧自检才能发现。
    """
    _audit.audit_frame(fig, index)


def save_figure(
    fig,
    stem: Path | str,
    formats: Iterable[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    """按 ``stem``（不含扩展名）写出所有格式，返回落盘路径并关闭画布。

    默认给 PNG（300 dpi 印刷级）；调用方传入 ``("png", "pdf")`` 时才额外生成
    论文用 PDF 矢量图（TrueType 嵌入）。
    """
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    # 出图管线末端统一「排版体检 + 自动修”：两步都做成兜底，各图不用各自 invoked
    # ① 标题折行：防止标题比子图宽、压到相邻面板（见 base.declutter.fit_axis_titles）
    # ② 标注去重叠：防止地图 U#/巢#/T# 互压（同点起飞的两架 UAV 曾 100% 相叠）
    # ③ 越界自检：tight bbox 被「看不见的 artist」撑大（2026-10-13 P03/P04/P06）
    try:
        _declutter.fit_axis_titles(fig)
        _declutter.declutter_figure(fig, close_draw=True)
    except Exception:  # 排版修正失败绝不阻断出图
        pass
    check_bbox_overflow(fig, label=stem.name)
    for fmt in formats:
        ext = str(fmt).lstrip(".").lower()
        if not ext:
            continue
        out = stem.with_suffix(f".{ext}")
        robust_savefig(fig, out, dpi=dpi, bbox_inches="tight", facecolor="white")
        written.append(out)
    plt.close(fig)
    return written


def save_poster(fig, path: Path | str, *, dpi: int = 300) -> None:
    """动画图的静态海报保存：先做越界自检、再走带重试的 tight 裁切。

    动画图不走 ``save_figure``（它会在循环外最后单独落 poster），故单独给出
    带自检的入口，保证与静态图同一套防护（见 :func:`check_bbox_overflow`）。
    """
    check_bbox_overflow(fig, label=str(Path(path).name))
    robust_savefig(fig, path, dpi=dpi, bbox_inches="tight", facecolor="white")


def robust_savefig(fig, path: Path | str, *, dpi: int = 300,
                   max_retries: int = 8, **kwargs) -> None:
    """``fig.savefig`` 的**带重试**封装。

    覆盖 Windows 下「预览/杀毒/索引器短暂锁定正在覆盖的 PNG」导致的
    ``OSError: [Errno 22] Invalid argument``——退避重试通常几百毫秒内解锁。
    """
    last: Exception | None = None
    for attempt in range(max_retries):
        try:
            fig.savefig(str(path), dpi=dpi, **kwargs)
            return
        except OSError as exc:  # 文件被外部进程短暂锁定时重试
            last = exc
            time.sleep(0.25 * (attempt + 1))
    raise last if last is not None else RuntimeError("robust_savefig: 未知失败")


def robust_pil_save(img, path: Path | str, *,
                    max_retries: int = 8, **kwargs) -> None:
    """Pillow ``Image.save`` 的**带重试**封装（同 ``robust_savefig`` 的锁容忍）。"""
    last: Exception | None = None
    for attempt in range(max_retries):
        try:
            img.save(str(path), **kwargs)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.25 * (attempt + 1))
    raise last if last is not None else RuntimeError("robust_pil_save: 未知失败")


def build_gif_palette(frames_rgb: list, extra_colors: Iterable[str] = ()):
    """为动图构建**全局调色板**（末帧 MEDIANCUT + 显式追加关键语义色）。

    为什么不能只按末帧 MEDIANCUT：该算法按像素数量分箱，DEM 地形的大面积
    暖色会吞掉小而饱和的语义色——待执行红点、UAV 珊瑚色等整幅仅数百像素，
    实测全部被压成同一个棕橙 ``(165,93,48)``，图例里再亮的红在 GIF 里也是
    土棕。把关键色**显式追加**进调色板后，逐帧最近邻映射即保真。
    """
    from PIL import Image

    base = frames_rgb[-1].quantize(colors=216, method=Image.MEDIANCUT)
    pal = base.getpalette() or []
    cols: list[tuple[int, int, int]] = []
    for i in range(min(216, len(pal) // 3)):
        cols.append((int(pal[i * 3]), int(pal[i * 3 + 1]), int(pal[i * 3 + 2])))
    for c in extra_colors:
        s = str(c).lstrip("#")
        c8 = (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        if c8 not in cols:
            cols.append(c8)
    cols = cols[:255]
    pal_img = Image.new("P", (16, 16))
    data = [v for c in cols for v in c]
    data += [0] * (256 * 3 - len(data))
    pal_img.putpalette(data)
    pal_img.putdata(list(range(256)))
    return pal_img


def shrink_text_to_axes(ax, txt, *, pad_frac: float = 0.02,
                        min_fs: float = 5.5, max_iter: int = 5) -> bool:
    """把一个文本块**实测**缩到所属子图宽度以内，返回是否调整过。

    为什么不用估宽：``MONO_ZH`` 混排（ASCII 走 DejaVu Sans Mono、中文回退
    Microsoft YaHei）的实际行宽比「按 mono advance 的理论估算」宽最多 1.5×
    （2026-10-13 在 P01 参数面板实测：估 2.9 in、渲染 4.4 in），任何估算都会
    再踩「估宽偏松」的坑。这里直接 draw 后量包围盒，按比例缩字号，最多
    ``max_iter`` 轮、不低于 ``min_fs``——**收敛由实测保证，不由估算保证**。

    适用：参数表 / 公式面板这类「一整块等宽文本 + 底色框」。文本块位置用
    ``transAxes`` 锚定，缩字号不会跑位。
    """
    if txt is None:
        return False
    changed = False
    for _ in range(max_iter):
        try:
            fig = ax.get_figure()
            fig.canvas.draw()
            r = fig.canvas.get_renderer()
            bb = txt.get_window_extent(r)
            hb = ax.get_window_extent(r)
        except Exception:
            return changed
        if bb is None or hb is None or hb.width <= 0:
            return changed
        avail = hb.width * (1.0 - 2.0 * pad_frac)
        if bb.width <= avail:
            return changed
        fs = float(txt.get_fontsize())
        if fs <= min_fs:
            return changed
        new_fs = max(min_fs, fs * (avail / bb.width))
        if new_fs >= fs - 0.05:      # 无实质缩小（已到下限）则停
            return changed
        txt.set_fontsize(new_fs)
        changed = True
    return changed


def style_axes(ax, *, title: str | None = None, xlabel: str | None = None,
               ylabel: str | None = None) -> None:
    """统一的坐标轴标注小工具（标题 / 轴标签一次设齐）。

    轴标题总是带 ``wrap=True``：子图标题常常比子图本身还宽（2026-10-13 版面
    自检实测 P09(a) 达 1.16×、P09(e) 1.19×、P01(d) 1.20×），超出的部分会压到
    相邻子图上。交给 matplotlib 按**实际渲染宽度**自动折行最可靠——自己估算
    字宽会重复踩「估宽偏松」的坑（见 :func:`_wrap_to_width` 的余量说明）。
    """
    if title:
        ax.set_title(title, fontsize=FS["title"], wrap=True)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=FS["base"])
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=FS["base"])


def _wrap_to_width(text: str, avail_in: float, fontsize_pt: float) -> str:
    """按可用宽度（inch）贪心折行，返回换行后的文本。

    <｜hy_place▁holder▁no▁813｜>行宽估算：全角/宽字符（CJK 等）按 1 em，半角按 ~0.52 em。无需渲染器，
    因此在 draw 之前调用也成立。已存在的 ``\\n`` 视为作者指定断行，只在**超出
    可用宽度**时才二次折行（作者分段优先）。

    存在的理由：脚注是画布级 ``fig.text``，**不会自动换行**——单行过长会溢出
    画布右缘，同时把 ``bbox_inches="tight"`` 的包围盒按超宽文本计算，导致
    出图被撑成异常宽幅（2026-10-13 实测 P03/P04 宽 1.65×/1.27×、P06 poster
    宽近 2×，右半张全白）。
    宽度估算取保守值（全角 1 em、半角 0.52 em）后仍须留 ~12% 安全余量：
    实测（2026-10-13）折到满行时真实宽度约为估算的 **1.13 倍**——字形 advance
    与标点回退都会让估算偏松，不留余量就会再次溢出画布右缘。
    """
    em = fontsize_pt / 72.0
    budget = avail_in * 0.88          # 安全余量，吸收估宽误差
    out: list[str] = []
    for seg in str(text).split("\n"):
        if not seg:
            out.append("")
            continue
        line: list[str] = []
        width = 0.0
        for ch in seg:
            ch_w = em if unicodedata.east_asian_width(ch) in ("W", "F") else em * 0.52
            if line and width + ch_w > budget:
                out.append("".join(line))
                line, width = [ch], ch_w
            else:
                line.append(ch)
                width += ch_w
        if line:
            out.append("".join(line))
    return "\n".join(out)


def footnote(fig, text: str, x: float = 0.01, y: float = 0.004) -> None:
    """画布级脚注（口径说明 / 样本量 / 数据来源），学术图必备。

    超宽的段会按画布可用宽度自动折行（见 :func:`_wrap_to_width`），避免单行
    溢出画布并把 tight bbox 撑宽。
    """
    fs = FS["footnote"]
    fig_w = float(fig.get_size_inches()[0])
    avail_in = max(1.0, fig_w * (0.995 - x))
    fig.text(x, y, _wrap_to_width(text, avail_in, fs), ha="left", va="bottom",
             fontsize=fs, color="0.45", linespacing=1.28)
