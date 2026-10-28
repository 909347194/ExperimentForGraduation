# -*- coding: utf-8 -*-
"""离线预处理线（不依赖具体飞行任务，只执行一次）：

    control_points → link_builder → virtual_network → precompute → network_io

编排见 pipeline.py。本线不依赖 online/。
"""
