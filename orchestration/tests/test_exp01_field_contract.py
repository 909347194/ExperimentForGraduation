# -*- coding: utf-8 -*-
"""exp01_smoke 建场契约：pad 由 OD 上限派生，且随上限进缓存键。"""

from __future__ import annotations

import math
from types import SimpleNamespace

from orchestration.experiments.exp01_smoke.support import config as cfgmod
from orchestration.experiments.exp01_smoke.support import field as fld
from path_planning.methods.spatial.corridor_window import required_pad_m

BOUNDS = (-2505.6, -2798.8, 2304.4, 1921.2)


def _scenario():
    return SimpleNamespace(
        bounds_xy=BOUNDS,
        data_bounds_xy=(BOUNDS[0] + 5.0, BOUNDS[1] + 5.0,
                        BOUNDS[2] - 5.0, BOUNDS[3] - 5.0),
        buildings=None,
        nofly=(),
    )


def _cfg():
    return cfgmod.load_config()


def test_city_diagonal_derived_pad():
    cfg = _cfg()
    c = fld.field_contract(cfg, _scenario())
    diagonal = math.hypot(BOUNDS[2] - BOUNDS[0], BOUNDS[3] - BOUNDS[1])
    assert abs(c["max_od_length_m"] - diagonal) < 1e-6
    spacing = tuple(float(v) for v in cfg["field"]["spacing_m"])
    expected = required_pad_m(
        fld.corridor_rule(cfg), spacing, diagonal,
        extra_m=float(cfg["field"]["world"]["pad_extra_m"]),
        min_pad_m=float(cfg["field"]["world"]["pad_min_m"]),
    )
    assert c["pad_m"] == expected
    # pad 必须是两条水平格距的公倍数，否则网格整体平移、历史结果不可复现
    assert c["pad_m"] % spacing[0] == 0.0
    assert c["pad_m"] % spacing[1] == 0.0


def test_explicit_od_limit_respected():
    cfg = _cfg()
    cfg["field"]["world"]["max_od_length_m"] = 1000.0
    c = fld.field_contract(cfg, _scenario())
    assert c["max_od_length_m"] == 1000.0
    assert c["pad_m"] < fld.field_contract(_cfg(), _scenario())["pad_m"]


def test_cache_key_follows_contract():
    cfg = _cfg()
    sc = _scenario()
    base = fld._cache_path(cfg, sc)
    other = fld._cache_path(cfg, sc)
    assert base == other, "同一契约必须命中同一个缓存文件"
    cfg2 = _cfg()
    cfg2["field"]["world"]["max_od_length_m"] = 3000.0
    assert fld._cache_path(cfg2, sc) != base, "改 OD 上限必须让旧场缓存失效"
