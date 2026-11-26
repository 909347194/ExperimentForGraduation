# -*- coding: utf-8 -*-
"""exp01_smoke 出图样式、保存约定与单图 CLI——**唯一** import ``pyplot`` 的地方。

先切到无后端 ``Agg`` 再导入 pyplot，保证无显示环境下不会误开窗口。
各图模块统一 ``from .. import theme as th``，不再各自 import pyplot、各自
设字体/配色/保存参数——否则同一批图的字号、灰色、线宽会各写一套，论文里
并排放两张就看得出不齐。

## 口径（本实验定稿）

* 画布：宽图 12–15 in（这些图是「一屏读一张」的工程图，不是版心单栏图）；
* 字号：标题 15–16.5 pt、轴 12 pt、图例 9–9.6 pt、明细 7.6–8.6 pt；
* 配色：机号色 / 周期色 / 语义色全实验唯一（见下方常量），任何图不得另立；
* 保存：PNG **300 dpi** 为默认；``--pdf`` 才追加矢量 PDF（TrueType 可搜索）；
* 落盘前统一走 :func:`save`：先做标注去重叠、再做越界自检，最后带重试写盘。
  三件事都在出口处做一次，各图不必各自实现。
"""

from __future__ import annotations

import argparse
import time
import unicodedata
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from ..support.config import EXP_DIR  # noqa: E402

__all__ = [
    "plt",
    "UAV_COLOR",
    "UAV_STYLE",
    "CYCLE_COLOR",
    "BUILDING_FACE",
    "BUILDING_EDGE",
    "INK",
    "MUTED",
    "FAIL",
    "WATER",
    "OK_GREEN",
    "EMIT_PDF",
    "DEFAULT_DPI",
    "style",
    "save",
    "fingerprint",
    "footnote",
    "formats",
    "add_common",
    "run_script",
]

#: 中文字体回退链（Windows/Linux 常见字体都列上，缺哪个就顺延）
_CJK_CANDIDATES = (
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "SimHei",
    "PingFang SC",
)

#: 机号 → 颜色（全实验唯一：同一架机在任何图上都是这个色；机号 1…6，按巢成对）
UAV_COLOR = {1: "#5B8C5A", 2: "#2E5C8A", 3: "#8A6FB0",
             4: "#C2582B", 5: "#A8823B", 6: "#4A8A86"}
#: 机号 → 线型（同栏多机靠线型分辨，不只靠颜色）
UAV_STYLE = {1: (0, (5, 2)), 2: "-", 3: (0, (1, 1)),
             4: "-", 5: "-", 6: (0, (3, 3))}
#: 周期 → 颜色（任务点、周期标题、周期分隔线统一用它）。周期数不预设，
#: 色板给到 6 个；超出取 MUTED，别让新周期把出图打崩。
CYCLE_COLOR = {1: "#154E7D", 2: "#2F8F73", 3: "#B5651D",
               4: "#6C4E9B", 5: "#A03A5B", 6: "#4E6E2F"}

BUILDING_FACE = "#DCDCDC"
BUILDING_EDGE = "#BFBFBF"
INK = "#222222"
MUTED = "#6B6B6B"
FAIL = "#C0392B"
WATER = "#1F6FA8"
OK_GREEN = "#2E7D4F"

#: 是否同时出 PDF。默认关：日常看图只要 PNG，投稿/排版时才显式加 ``--pdf``。
EMIT_PDF = False
#: 默认位图 dpi：300 = 印刷级。15 in 幅面下约 4500 px 宽，
#: 论文插图缩到单栏后仍有足够像素；190 只在屏幕预览时够用，贴进 Word 会发糊。
DEFAULT_DPI = 300

CONFIG_PATH = EXP_DIR / "config.yaml"


def style() -> str:
    """应用全局样式，返回实际选中的中文字体名（便于日志确认没落到豆腐块）。"""
    available = {f.name for f in fm.fontManager.ttflist}
    chain = [n for n in _CJK_CANDIDATES if n in available] or ["DejaVu Sans"]
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [*chain, "DejaVu Sans"],
            "font.size": 12,
            "axes.titlesize": 15,
            "axes.titleweight": "bold",
            "axes.labelsize": 12,
            "axes.edgecolor": "#8C8C8C",
            "axes.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.unicode_minus": False,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "grid.color": "#E6E6E6",
            "grid.linewidth": 0.6,
            "legend.frameon": False,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.08,
            # PDF 嵌 TrueType：投稿系统要文本可搜索，Type3 位图字会被退稿
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    return chain[0]


def formats() -> tuple[str, ...]:
    """当前该写的格式：PNG 恒有，``--pdf`` 时追加矢量。"""
    return ("png", "pdf") if EMIT_PDF else ("png",)


def save(fig, stem: Path | str, *, dpi: int | None = None,
         fmt: Sequence[str] | None = None) -> list[Path]:
    """按 ``stem``（不含扩展名）落盘并关闭画布。

    出口处统一做三件事，各图不再各自实现：

    1. **标注去重叠**：地图上的任务序号/巢标号/文字块互压；
    2. **越界自检**：内容越出画布（tight bbox 被看不见的 artist 撑大）；
    3. **带重试写盘**：Windows 上预览器/同步进程短暂占用文件会报 EINVAL。
    """
    from .base.declutter import declutter

    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    dpi = int(dpi or DEFAULT_DPI)
    try:
        declutter(fig)
    except Exception:  # 排版修正失败绝不阻断出图
        pass
    check_overflow(fig, stem.name)

    old_dpi = fig.dpi
    fig.set_dpi(dpi)
    written: list[Path] = []
    for ext in (fmt if fmt is not None else formats()):
        ext = str(ext).lstrip(".").lower()
        if not ext:
            continue
        out = stem.with_suffix(f".{ext}")
        _save_with_retry(fig, out, dpi=None if ext == "pdf" else dpi)
        written.append(out)
    fig.set_dpi(old_dpi)
    plt.close(fig)
    return written


def _save_with_retry(fig, path: Path, *, dpi: int | None, tries: int = 4) -> None:
    """存图带重试：Windows 上预览器/同步进程短暂占用文件会让 open 报 EINVAL。"""
    for k in range(tries):
        try:
            if dpi is None:
                fig.savefig(path)
            else:
                fig.savefig(path, dpi=dpi)
            return
        except OSError:
            if k == tries - 1:
                raise
            time.sleep(0.8 * (k + 1))


def check_overflow(fig, label: str = "", *, tol: float = 1.03) -> bool:
    """出图自检：内容是否越出画布；越界只告警不阻断（返回 True 表示溢出）。

    ``bbox_inches="tight"`` 按所有 artist 的包围盒裁图，而画布级 ``Text`` 与
    画到 xlim 之外的 ``Line2D`` 都会计入——「画了但看不见」的内容会把裁切框
    撑成异常宽/高。这里在写盘前量一次，把真实幅面打进日志。
    """
    try:
        fig.canvas.draw()
        tb = fig.get_tightbbox(fig.canvas.get_renderer())
        w, h = fig.get_size_inches()
        over = (tb.x1 - tb.x0) > w * tol or (tb.y1 - tb.y0) > h * tol
        if over:
            print(f"[plot] 版面警告：{label} 内容 {tb.x1 - tb.x0:.2f}×"
                  f"{tb.y1 - tb.y0:.2f} in 超出画布 {w:.2f}×{h:.2f} in")
        return bool(over)
    except Exception:
        return False


def _wrap(text: str, width_in: float, fontsize: float) -> str:
    """按可用宽度贪心折行（画布级 ``fig.text`` 不会自动换行）。"""
    em = fontsize / 72.0
    budget = max(width_in * 0.88, 1.0)
    out: list[str] = []
    for seg in str(text).split("\n"):
        if not seg:
            out.append("")
            continue
        line: list[str] = []
        used = 0.0
        for ch in seg:
            cw = em if unicodedata.east_asian_width(ch) in ("W", "F") else em * 0.52
            if line and used + cw > budget:
                out.append("".join(line))
                line, used = [ch], cw
            else:
                line.append(ch)
                used += cw
        if line:
            out.append("".join(line))
    return "\n".join(out)


def fingerprint(title: str, subtitle: str = "", *, y: float = 0.985,
                gap_in: float = 0.42) -> None:
    """图题两行式：主标题 + 一句口径。

    ``gap_in`` 是两行之间的**英寸**距离（不是 figure 比例）：同样的 0.05 在
    4.9 in 高的图上会压字、在 9 in 高的图上又太空。按英寸算，换画布高度不用重调。
    """
    fig = plt.gcf()
    fig.suptitle(title, y=y, va="top", fontsize=16.5, fontweight="bold", x=0.5)
    if subtitle:
        dy = gap_in / max(float(fig.get_size_inches()[1]), 1.0)
        fig.text(0.5, y - dy, subtitle, ha="center", va="top", fontsize=11, color=MUTED)


def footnote(fig, text: str, *, y: float = 0.012) -> None:
    """底部小字脚注（口径/读图说明），超宽自动折行。"""
    fig_w = float(fig.get_size_inches()[0])
    fig.text(0.5, y, _wrap(text, fig_w * 0.94, 9.4), ha="center", va="bottom",
             fontsize=9.4, color=MUTED, linespacing=1.35)


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--run", default=None,
                        help="结果目录（缺省 = 配置 seed 对应的 results/seed_N）")
    parser.add_argument("--pdf", action="store_true", help="同时输出矢量 PDF（默认只出 PNG）")


def _read_dpi(exp_root: Path) -> int | None:
    """从实验根 ``config.yaml`` 读 ``plot.dpi``（读不到返回 None）。"""
    try:
        import yaml

        cfg_path = Path(exp_root) / "config.yaml"
        if cfg_path.exists():
            with cfg_path.open(encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            dpi = int((cfg.get("plot") or {}).get("dpi", 0) or 0)
            return dpi or None
    except Exception:
        return None
    return None


def run_script(figure_id: str, argv: Sequence[str] | None = None) -> list[Path]:
    """单图独立绘制入口（``python -m …plot.ta.ta01_task_assignment``）。

    只读已落盘 ``results/``（**不重跑规划器**），输出到 ``results/<run>/figures``。
    可选 ``--pdf`` / ``--dpi N`` / ``--run DIR`` / ``--out DIR``。
    """
    global EMIT_PDF
    from . import registry as reg
    from .base import scene as sc

    fig = reg.by_id(figure_id)

    p = argparse.ArgumentParser(prog=f"plot:{figure_id}", description=fig.title)
    add_common(p)
    p.add_argument("--dpi", type=int, default=None, help="指定 dpi（缺省读 config.yaml）")
    p.add_argument("--out", default=None, help="输出目录（缺省 <run>/figures）")
    ns = p.parse_args(list(argv) if argv is not None else [])

    EMIT_PDF = bool(ns.pdf)
    font = style()
    ctx = sc.prepare(ns.config, ns.run, need_scene=fig.needs_scene)
    if ns.out:
        import dataclasses

        ctx = dataclasses.replace(ctx, fig_dir=Path(ns.out))
    dpi = ns.dpi if ns.dpi is not None else (_read_dpi(sc.EXP_ROOT) or DEFAULT_DPI)

    t0 = time.perf_counter()
    paths = fig.build(ctx, dpi=dpi)
    print(f"[plot] {figure_id} → {', '.join(p.name for p in paths)}"
          f"（{time.perf_counter() - t0:.1f} s，字体 {font}）")
    return paths
