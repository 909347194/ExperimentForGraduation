# -*- coding: utf-8 -*-
"""exp01_smoke 绘图样式与保存约定（学术论文口径）。

本模块是**唯一** import ``pyplot`` 的地方：先切到无后端 ``Agg``，
再导入 pyplot，保证不会在有显示环境时误开窗口、也不会因后端切换告警。

其余绘图模块统一 ``from .theme import plt``，不直接 import pyplot。

## 学术绘图口径

论文版式：A4、左右边距各 3 cm → 版心 15 cm ≈ 5.9 in：

    FIG_SINGLE  (5.9, h)   单栏图——进论文的默认宽度
    FIG_WIDE    (11.8, h)  通栏大图——区域总览等需要横向展开的图
    FIG_SQUARE  (5.9, 5.9) 单栏方图——俯视/剖面等方图

字号（图按 5.9 in 宽入版心时为 1:1）：

    base 8.5 / tick 8 / legend 8 / title 9.5 / footnote 7.5

中文：按字形回退的 CJK 字体链（Linux 沙箱 Noto Sans CJK、Windows 微软雅黑），
缺字形时 matplotlib 会整段出"豆腐块"，故链要覆盖两个平台。

输出：`results/figures/fig_<id>.png`（dpi=160），文件名由 registry 统一定。
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

__all__ = [
    "plt",
    "CJK_FONTS",
    "PALETTE",
    "MONO_ZH",
    "FIG_SINGLE",
    "FIG_WIDE",
    "FIG_SQUARE",
    "FS",
    "setup_style",
    "save_figure",
]

#: 中文字体链（按序回退；论文机器常见字体都列上）
CJK_FONTS = (
    "Noto Sans CJK SC",
    "Noto Sans CJK JP",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Microsoft YaHei",
    "SimHei",
)

#: 中文等宽字体链（参数面板/明细表用；按字形回退）
MONO_ZH = (
    "Noto Sans Mono CJK SC",
    "WenQuanYi Micro Hei Mono",
    "Microsoft YaHei",
    "DejaVu Sans Mono",
)

#: 语义色（Tableau 10 派生，色盲友好、灰度打印可辨）
PALETTE = {
    "building": "#d0d0d0",     # 建筑轮廓填充
    "building_edge": "#8a8a8a",
    "control": "#2b7bba",      # 控制点
    "route": "#9a9a9a",        # route 走廊（虚线）
    "path": "#1f77b4",         # path 平滑曲线（实线）
    "origin": "#2ca02c",       # 起点
    "dest": "#d1495b",         # 终点
    "nofly_dyn": "#ff0000",    # 动态禁飞区
    "nofly_stat": "#b30000",   # 静态禁飞区
    "crop": "#0050ff",         # 裁剪活跃窗口
}

#: 版面尺寸（in）
FIG_SINGLE = (5.9, 5.9)
FIG_WIDE = (11.8, 9.0)
FIG_SQUARE = (5.9, 5.9)

#: 字号
FS = {
    "base": 8.5,
    "tick": 8.0,
    "legend": 8.0,
    "title": 9.5,
    "suptitle": 11.0,
    "footnote": 7.5,
}

_DPI = 160


def setup_style() -> None:
    """全局 rcParams：CJK 字体链 + 负号 + 细网格。幂等，可重复调用。"""
    plt.rcParams["font.sans-serif"] = list(CJK_FONTS) + ["DejaVu Sans"]
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = FS["base"]
    plt.rcParams["axes.titlesize"] = FS["title"]
    plt.rcParams["axes.labelsize"] = FS["base"]
    plt.rcParams["xtick.labelsize"] = FS["tick"]
    plt.rcParams["ytick.labelsize"] = FS["tick"]
    plt.rcParams["legend.fontsize"] = FS["legend"]
    plt.rcParams["figure.dpi"] = 100
    plt.rcParams["savefig.dpi"] = _DPI
    plt.rcParams["pdf.fonttype"] = 42  # TrueType 嵌入，PDF 文本可搜索


def save_figure(fig, out_dir: str, stem: str, *, dpi: int = _DPI,
                close: bool = True) -> str:
    """统一保存约定：``<out_dir>/fig_<stem>.png`` + tight_layout 收尾。"""
    os.makedirs(out_dir, exist_ok=True)
    fig.tight_layout()
    path = os.path.join(out_dir, f"fig_{stem}.png")
    fig.savefig(path, dpi=dpi)
    if close:
        plt.close(fig)
    return path
