# -*- coding: utf-8 -*-
"""exp02_selection_scale 绘图底座：**被图依赖、但自己不是图**的模块。

放这里的判断标准——「有至少两张图会用到它，且它不产出任何图文件」：

    ``audit.py``      版面自检（越界 / 文本互压 / 数据越窗），经 theme.save_figure
                      兜底执行——「出图空白/大幅面」类事故的防线
    ``declutter.py``  轴标题折行与标注去重叠，同样经 save_figure 兜底

与 exp01_smoke 的差异：本实验是**单周期变体扫描**（无逐周期 solution.json、
无 DEM 地图图件），故未复制 exp01 的 ``dem.py`` / ``geo.py``。若将来需要
地图类图件，从 exp01_smoke/plot/base/ 复制对应模块即可（theme 的接口不变）。

依赖方向单向向下，禁止反向::

    plot/__init__.py ──► registry.py ──► setup|report/*.py ──► base/*.py ──► theme.py
"""

from . import audit as _audit, declutter as _declutter
from .audit import __all__ as _AUDIT_ALL
from .declutter import __all__ as _DECLUTTER_ALL

__all__ = [*_AUDIT_ALL, *_DECLUTTER_ALL]
