# -*- coding: utf-8 -*-
"""共享件（不是图）：场景装配、四张台账、标注去重叠。

约定：图模块只允许 ``from ..base import scene as sc`` / ``ledger as iv`` /
``from .. import theme as th`` 三个入口；不要在 base 里塞任何"只给一张图用"的
绘制逻辑——那种代码留在那张图的模块里，否则 base 又会长成第二个 ``_shared``。
"""

__all__ = ["scene", "ledger", "declutter"]
