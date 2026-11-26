# -*- coding: utf-8 -*-
"""exp01_smoke 事后诊断：只读已落盘结果，不产正式账、不改参数。

- ``failure.py``  失败航次重放：按账重建请求，摊开逐航段 / RRT 统计
- ``water.py``    DEM nodata（水域）填充诊断：只读 DEM 与任务点
- ``dt_floor.py`` 冻结的历史诊断：n × dt_min 离散退化扫描（坐标来自旧任务清单）
- ``reaudit.py``  按已落盘 ``loop_trace.json`` 重算 ``audit.json``（不重跑求解）
"""
