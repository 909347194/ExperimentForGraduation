# -*- coding: utf-8 -*-
"""建场契约异常：部署事故，不是调用方参数错误。

职责：定义全域场相关的两类契约错误。
边界：不含任何建场/取样逻辑。
相关：``methods/spatial/world_field.py``（抛错方）、``methods/spatial/world_field_builder.py``。

为什么单独成文件而不是塞进 ``world_field.py``：这两类异常会被**启动自检、请求准入、
离线校验**三处同时捕获，任何一处 import ``world_field`` 都会把 ``scipy.ndimage``
拉进依赖链。异常类本身零依赖，分开之后，只做准入检查的模块不必装载建场依赖。
"""

from __future__ import annotations

__all__ = ["FieldContractError", "WindowOutsideField"]


class FieldContractError(RuntimeError):
    """建场契约被破坏（部署事故）。

    故意不继承 ``ValueError``：上层通常把 ``ValueError`` 归为"调用方 bug / 4xx"，
    而这里该做的是**告警 + 拒绝就绪 + 重新建场**。重试同一条请求没有任何意义，
    因为场还是那张场。
    """


class WindowOutsideField(FieldContractError):
    """窗口超出全域场覆盖范围：应在启动自检暴露，不在线兜底。"""
