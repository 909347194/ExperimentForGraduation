# -*- coding: utf-8 -*-
"""exp02_selection_scale 图组：**setup 层——实验设置**。

回答「实验设了多大问题、扫了哪些变体、求解预算多少」：参数即图，
让读者不翻 config.yaml 就能判断实验设定，也是全部报告图的口径基准。

现有图：``setup_params``（实验设定总览：场景 / 变体矩阵 / 求解预算）。
"""

from .setup_params import plot_setup_params

__all__ = ["plot_setup_params"]
