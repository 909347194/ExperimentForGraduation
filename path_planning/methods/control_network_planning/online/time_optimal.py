# -*- coding: utf-8 -*-
"""时间最优轨迹生成（Lin2021 §2.4）。

职责：path → trajectory。逐节点估计曲率半径 → 向心加速度占用后剩余切向加/减速能力
（Lin2021 Fig.15）→ 前向贪婪推进速度剖面，配**反向回溯**（backward tracing，Fig.15(d)）
保证限速可行；输出含位置/速度/加速度的时空轨迹与总飞行时间。

不负责：路径几何（dubins_smoother/post_check）、TPR 汇总（online/pipeline 汇总）。
"""
