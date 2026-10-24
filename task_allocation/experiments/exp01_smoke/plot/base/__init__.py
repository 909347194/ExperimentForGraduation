# -*- coding: utf-8 -*-
"""exp01_smoke 绘图底座：**被图依赖、但自己不是图**的模块。

放这里的判断标准——「有至少两张图会用到它，且它不产出任何图文件」：

    ``dem.py``   DEM 高程底图：取数（真实 ASTER 瓦片 / solution 降采样栅格）、
                 越界补洞、山体阴影晕渲、分层设色铺底
    ``geo.py``   矢量图标与图例、UAV 配色与航段、图层绘制与多周期行式排版

依赖方向单向向下，禁止反向::

    plot/__init__.py ──► registry.py ──► scene|process|report/*.py ──► base/*.py ──► theme.py
"""

# 同 plot/__init__.py：包内导入用单点相对（2026-10-23 修正误改的四点导入）。
from . import dem as _dem, geo as _geo
from .dem import __all__ as _DEM_ALL
from .geo import __all__ as _GEO_ALL

__all__ = [*_DEM_ALL, *_GEO_ALL]
