# -*- coding: utf-8 -*-
"""exp01_smoke 绘图底座：**被图依赖、但自己不是图**的模块。

放这里的判断标准——「有至少两张图会用到它，且它不产出任何图文件」：

    ``scene.py``      场景数据定位：data.<scenario> 目录下的 DEM 瓦片 / 建筑
                      shapefile、局部坐标系、项目根 sys.path 兜底（**只依赖
                      标准库**，故可被其余底座在顶层安全导入）
    ``dem.py``        DEM 背景：取数（真实 ASTER 瓦片 / solution 降采样栅格）、
                      越界补洞、山体阴影晕渲、分层设色铺底
    ``buildings.py``  建筑背景：BuildingLayer 载入 → 视口裁剪 → 按高度着色铺底
    ``backdrop.py``   背景调度：**有建筑优先建筑，只有地形才画地形**（各图唯一
                      入口，判断只做一次）
    ``geo.py``        矢量图标与图例、UAV 配色与航段、图层绘制与多周期行式排版

依赖方向单向向下，禁止反向::

    plot/__init__.py ──► registry.py ──► initial|process|report/*.py ──► base/*.py ──► theme.py
                                                                            │
                                                            scene.py ◄── dem.py / buildings.py

导入顺序说明（勿随意调整）：``dem.py`` 在顶层 ``from ..theme import plt``，而
``theme.py`` 又反过来 ``from .base import audit``；这套环之所以成立，是因为
``theme`` 在 import ``base`` 之前已定义完 ``plt``。新增底座模块请**不要在顶层
触碰 ``theme.PALETTE``**（那是在 base 之后才定义的），需要配色就在本模块内自持
（``buildings.py`` 的 ``BUILDING_CMAP`` 即如此）。
"""

# 同 plot/__init__.py：包内导入用单点相对（2026-10-23 修正误改的四点导入）。
from . import scene as _scene  # noqa: F401  （无 matplotlib 依赖，必须最先）
from . import dem as _dem, geo as _geo
from . import buildings as _buildings
from . import backdrop as _backdrop
from .backdrop import __all__ as _BACKDROP_ALL
from .buildings import __all__ as _BUILDING_ALL
from .dem import __all__ as _DEM_ALL
from .geo import __all__ as _GEO_ALL
from .scene import __all__ as _SCENE_ALL

__all__ = [*_SCENE_ALL, *_DEM_ALL, *_BUILDING_ALL, *_BACKDROP_ALL, *_GEO_ALL]
