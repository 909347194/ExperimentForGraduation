# -*- coding: utf-8 -*-
"""扩展控制网络序列化 / 加载。

职责：data/processed 产物的读写与版本管理。关键约束：

    文件内嵌参数指纹（OfflineParams + UAVParams + RiskParams 的 hash），
    加载时指纹不匹配必须报错 —— 防止"参数改了、网络没重建"的隐性不一致。

不负责：网络构建（offline 其余模块）。
"""
