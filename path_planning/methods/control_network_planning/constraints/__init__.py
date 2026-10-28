# -*- coding: utf-8 -*-
"""约束模型层：buildings / nofly_zones / query。

设计要点：离线剪枝与在线剪枝必须走同一 CollisionQuery 判定口径，
避免"离线说可行、在线说不可行"的两套标准。
"""
