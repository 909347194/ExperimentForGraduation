# -*- coding: utf-8 -*-
"""nest_keys.py — 机巢代键常量（**叶子模块，不得做任何本地导入**）。

历史问题：``nest_offset`` 的默认值 ``1_000_000`` 在 5 处独立重复
（``cost_matrix`` / ``lazy_pairwise`` / ``provider``×2 / ``rolling.horizon``），
改一处漏一处会让「机巢键 = nest_offset + nest_id」的键空间错位——代价矩阵、
懒加载 pair 项、滚动层三边口径一旦错开，属于难排查的静默不一致。

之所以单独成模块而不是放进 ``cost_matrix``：``cost_matrix`` 会导入
``provider`` 与 ``lazy_pairwise``，若常量定义在 ``cost_matrix`` 里，
下游反向导入即形成循环（见 ``provider.py`` 关于 cost 包 __init__ 回环的注释）。
本模块**不导入任何本地模块**，可安全被任一层引用。
"""
from __future__ import annotations

# 机巢虚拟节点id 基准：机巢在代价矩阵中的键 = DEFAULT_NEST_OFFSET + nest_id。
# 取值须足够大以避开任务/UAV 键空间；**历史依赖请勿随意改动**。
DEFAULT_NEST_OFFSET: int = 1_000_000

__all__ = ["DEFAULT_NEST_OFFSET"]
