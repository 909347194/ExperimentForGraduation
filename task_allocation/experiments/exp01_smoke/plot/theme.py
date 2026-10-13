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
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

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
#: 中文字形自动回退到 Microsoft YaHei。直接写 ``family="monospace"`` 会落到
#: DejaVu Sans Mono 一种字体上，中文全部缺字（豆腐块）。
MONO_ZH = ["DejaVu Sans Mono", "Microsoft YaHei"]

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
    for fmt in formats:
        ext = str(fmt).lstrip(".").lower()
        if not ext:
            continue
        out = stem.with_suffix(f".{ext}")
        robust_savefig(fig, out, dpi=dpi, bbox_inches="tight", facecolor="white")
        written.append(out)
    plt.close(fig)
    return written


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


def style_axes(ax, *, title: str | None = None, xlabel: str | None = None,
               ylabel: str | None = None) -> None:
    """统一的坐标轴标注小工具（标题 / 轴标签一次设齐）。"""
    if title:
        ax.set_title(title, fontsize=FS["title"])
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=FS["base"])
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=FS["base"])


def footnote(fig, text: str, x: float = 0.01, y: float = 0.004) -> None:
    """画布级脚注（口径说明 / 样本量 / 数据来源），学术图必备。"""
    fig.text(x, y, text, ha="left", va="bottom",
             fontsize=FS["footnote"], color="0.45")
