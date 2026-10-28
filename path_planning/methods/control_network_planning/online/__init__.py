# -*- coding: utf-8 -*-
"""在线规划线（接收上层 OD，实时求解）：

    dynamic_prune → route_search → dubins_smoother → post_check → time_optimal

编排见 pipeline.py。本线不依赖 offline/（只消费其产物）。
"""
