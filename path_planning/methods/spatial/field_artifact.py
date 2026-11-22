# -*- coding: utf-8 -*-
"""场身份指纹：把"用的是哪张 SDF"写进结果缓存键。

职责：从 ``WorldField`` 抽取参与数值结果的几何参数，产出可直接并入参数哈希的字典。
边界：只读场属性，不建场、不改场。
相关：``methods/spatial/world_field.py``（场对象）。

为什么必须进指纹：全域场比"按窗口现算"多一层三线性取样，同一 OD 在两套 SDF 基准下
可以给出不同解；场参数（origin / spacing / shape / bias）变了更是换了一整套数据。
若两者共用同一个 hash，缓存会静默命中另一口径的结果——**最坏情况是复用了别的城市、
别的间距的解，而它看起来完全正常**。

``sample_bias_m`` 单列出来：它只改数值、不改格网形状，光靠 origin/spacing/shape
抓不到。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from path_planning.methods.spatial.world_field import WorldField

__all__ = ["field_signature"]


def field_signature(
    field: "WorldField | None", sample_bias_m: float = 0.0
) -> dict[str, Any]:
    """场身份 → 结果缓存的额外键。``None`` 返回空字典（不使用全域场）。"""
    if field is None:
        return {}
    g = field.grid
    return {
        "world_field": {
            "origin": [round(float(v), 6) for v in g.origin],
            "spacing": [float(v) for v in g.spacing],
            "shape": [int(v) for v in g.shape],
            "sample_bias_m": round(float(sample_bias_m), 6),
        }
    }
