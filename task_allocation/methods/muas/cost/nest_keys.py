# -*- coding: utf-8 -*-
"""nest_keys.py — 机巢代键常量（叶子模块，不导入任何本地模块）。

职责：集中定义机巢代键偏移 DEFAULT_NEST_OFFSET，供各代价模块与滚动层共享，避免多份定义
导致键空间错位。
边界：只放常量，无导入、无逻辑，可被任意层安全引用。
相关：muas/cost/cost_matrix.py、lazy_pairwise.py、provider.py、rolling/horizon.py。"""
from __future__ import annotations

# 机巢虚拟节点id 基准：机巢在代价矩阵中的键 = DEFAULT_NEST_OFFSET + nest_id。
# 取值须足够大以避开任务/UAV 键空间；**历史依赖请勿随意改动**。
DEFAULT_NEST_OFFSET: int = 1_000_000

__all__ = ["DEFAULT_NEST_OFFSET"]
