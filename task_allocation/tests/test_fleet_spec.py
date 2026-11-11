# -*- coding: utf-8 -*-
"""机队 JSON 清单（fleet spec）加载 / 校验 / 装配 + exp01 接线回归。

口径（docs/design_fleet_spec_2026-11-11.md）：

- 三层继承：defaults ← models[model] ← 逐机键；home_nest 必填不继承。
- 严格校验：未知键 / 非法数值 / 重复机号 / 未知巢 一律报错，不静默修正。
- 同构等价：清单全机同性能时，装配产物与 exp01 同构回退逐字段一致
  （本文件的回归锚点——异构配置不得改变同构行为）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.fleet_spec import (  # noqa: E402
    build_uavs_from_spec,
    load_fleet_spec,
)
from task_allocation.common.types import Nest  # noqa: E402

EXP01_DIR = REPO / "task_allocation" / "experiments" / "exp01_smoke"


def _nests(capacity: int = 2):
    return [
        Nest(id=0, x=0.0, y=0.0, z=10.0, capacity=capacity),
        Nest(id=1, x=1000.0, y=0.0, z=20.0, capacity=capacity),
    ]


def _write(tmp_path: Path, obj, name: str = "fleet.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 加载与校验
# ---------------------------------------------------------------------------


def test_three_layer_inheritance(tmp_path):
    """defaults ← model ← 逐机键；home_nest / id 不参与继承。"""
    p = _write(tmp_path, {
        "version": 1,
        "defaults": {"speed_mps": 15.0, "max_range_m": 10000.0},
        "models": {"long": {"max_range_m": 25000.0, "speed_mps": 20.0}},
        "uavs": [
            {"id": 0, "model": "long", "home_nest": 0},                  # 全取 model
            {"id": 1, "model": "long", "home_nest": 1, "max_range_m": 8000.0},
            {"home_nest": 1},                                            # 全取 defaults
        ],
    })
    spec = load_fleet_spec(p)
    assert [u.id for u in spec.uavs] == [0, 1, 2]          # 未显式给 id → 取序号
    u0, u1, u2 = spec.uavs
    assert (u0.max_range_m, u0.speed_mps) == (25000.0, 20.0)
    assert (u1.max_range_m, u1.speed_mps) == (8000.0, 20.0)   # 逐机键压过 model
    assert (u2.max_range_m, u2.speed_mps) == (10000.0, 15.0)  # 落到 defaults


def test_max_time_derivation(tmp_path):
    """max_time_s 缺省派生 = 航程/速度；显式值优先。"""
    q = _write(tmp_path, {
        "defaults": {"speed_mps": 10.0, "max_range_m": 10000.0, "max_time_s": None},
        "uavs": [
            {"id": 0, "home_nest": 0},
            {"id": 1, "home_nest": 0, "max_time_s": 500.0},
        ],
    })
    spec = load_fleet_spec(q)
    assert spec.uavs[0].resolved_max_time() == pytest.approx(1000.0)  # 10000/10
    assert spec.uavs[1].resolved_max_time() == pytest.approx(500.0)


@pytest.mark.parametrize("bad,frag", [
    ({"uavs": [{"home_nest": 0, "speeed_mps": 1}], "defaults": {"speed_mps": 1.0, "max_range_m": 1.0}}, "未知键"),
    ({"uavs": [{"model": "ghost", "home_nest": 0}], "models": {}, "defaults": {"speed_mps": 1.0, "max_range_m": 1.0}}, "未在 models"),
    ({"uavs": [{"id": 0}], "defaults": {"speed_mps": 1.0, "max_range_m": 1.0}}, "home_nest"),
    ({"uavs": [{"home_nest": 0}], "defaults": {"speed_mps": 1.0, "max_range_m": 1.0},
      "extra": 1}, "未知键"),
    ({"uavs": [{"home_nest": 0}], "defaults": {"speed_mps": -1.0, "max_range_m": 1.0}}, "必须 > 0"),
    ({"uavs": [{"home_nest": 0}], "defaults": {"speed_mps": 1.0}}, "缺少"),
    ({"uavs": [{"home_nest": 0, "max_range_m": 5.0}], "defaults": {"speed_mps": 1.0, "max_range_m": 1.0, "max_time_s": -3.0}}, "必须 > 0"),
    ({"version": 99, "uavs": [{"home_nest": 0}], "defaults": {"speed_mps": 1.0, "max_range_m": 1.0}}, "version"),
    ({"uavs": []}, "非空列表"),
    ({"uavs": [{"home_nest": True}], "defaults": {"speed_mps": 1.0, "max_range_m": 1.0}}, "home_nest"),
])
def test_validation_rejects(bad, frag, tmp_path):
    q = _write(tmp_path, bad)
    with pytest.raises(ValueError, match=frag):
        load_fleet_spec(q)


def test_duplicate_ids_rejected(tmp_path):
    p = _write(tmp_path, {
        "defaults": {"speed_mps": 1.0, "max_range_m": 1.0},
        "uavs": [{"id": 7, "home_nest": 0}, {"id": 7, "home_nest": 1}],
    })
    with pytest.raises(ValueError, match="重复"):
        load_fleet_spec(p)


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------


def test_build_places_uavs_at_home_nest_full_charge(tmp_path):
    p = _write(tmp_path, {
        "defaults": {"speed_mps": 15.0, "max_range_m": 10000.0},
        "uavs": [
            {"id": 0, "home_nest": 0, "max_range_m": 30000.0},
            {"id": 1, "home_nest": 1},
        ],
    })
    uavs = build_uavs_from_spec(load_fleet_spec(p), _nests(capacity=2))
    assert [(u.id, u.nest_id) for u in uavs] == [(0, 0), (1, 1)]
    assert uavs[0].max_range == 30000.0 and uavs[0].remaining_range == 30000.0
    assert uavs[1].max_range == 10000.0
    assert (uavs[0].x, uavs[0].y, uavs[0].z) == (0.0, 0.0, 10.0)   # 巢位起点
    assert uavs[1].max_time == pytest.approx(10000.0 / 15.0)


def test_build_unknown_nest_rejected(tmp_path):
    p = _write(tmp_path, {
        "defaults": {"speed_mps": 1.0, "max_range_m": 1.0},
        "uavs": [{"home_nest": 99}],
    })
    with pytest.raises(ValueError, match="home_nest=99 不存在"):
        build_uavs_from_spec(load_fleet_spec(p), _nests())


def test_build_initial_overcapacity_rejected(tmp_path):
    p = _write(tmp_path, {
        "defaults": {"speed_mps": 1.0, "max_range_m": 1.0},
        "uavs": [{"home_nest": 0}, {"home_nest": 0}, {"home_nest": 0}],
    })
    spec = load_fleet_spec(p)
    with pytest.raises(ValueError, match="初始超容"):
        build_uavs_from_spec(spec, _nests(capacity=2))
    # 显式关闭检查 → 交给运行期 strict_capacity
    assert len(build_uavs_from_spec(spec, _nests(capacity=2), check_capacity=False)) == 3


# ---------------------------------------------------------------------------
# exp01 接线 + 同构等价回归
# ---------------------------------------------------------------------------


def _load_exp01_build():
    """按路径加载 exp01 build.py（与 test_rolling_caliber 同法，防串模块）。"""
    preexisting = "paths" in sys.modules
    if str(EXP01_DIR) not in sys.path:
        sys.path.insert(0, str(EXP01_DIR))  # 供 build.py 的 `import paths`
    name = "exp01_build_module"
    spec = importlib.util.spec_from_file_location(name, EXP01_DIR / "build.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # 必须先注册：dataclass 需按 __module__ 反查模块
    try:
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod
    finally:
        if str(EXP01_DIR) in sys.path:
            sys.path.remove(str(EXP01_DIR))
        if not preexisting:
            sys.modules.pop("paths", None)


def test_exp01_build_uavs_fleet_file(tmp_path):
    """exp01 build_uavs：fleet_file 走逐机清单（绝对路径）。"""
    build = _load_exp01_build()
    p = _write(tmp_path, {
        "defaults": {"speed_mps": 15.0, "max_range_m": 10000.0},
        "models": {"long": {"max_range_m": 25000.0}},
        "uavs": [
            {"id": 0, "model": "long", "home_nest": 0},
            {"id": 1, "home_nest": 1},
        ],
    })
    cfg = {"problem": {"fleet_file": str(p)}}
    uavs = build.build_uavs(_nests(), cfg)
    assert [u.max_range for u in uavs] == [25000.0, 10000.0]
    assert [u.nest_id for u in uavs] == [0, 1]


def test_exp01_build_uavs_homogeneous_equivalence(tmp_path):
    """同构等价：清单全机同性能 ⇔ 历史 n_uavs 回退，逐字段一致。"""
    build = _load_exp01_build()
    nests = _nests(capacity=4)
    cfg_legacy = {"problem": {
        "n_uavs": 4, "uav_speed": 15.0, "uav_max_range": 10000.0, "uav_max_time": None,
    }}
    legacy = build.build_uavs(nests, cfg_legacy)

    p = _write(tmp_path, {
        "defaults": {"speed_mps": 15.0, "max_range_m": 10000.0},
        "uavs": [{"id": i, "home_nest": nests[i % len(nests)].id} for i in range(4)],
    })
    from_spec = build.build_uavs(nests, {"problem": {"fleet_file": str(p)}})

    for a, b in zip(legacy, from_spec):
        assert (a.id, a.x, a.y, a.z, a.speed, a.remaining_range,
                a.max_time, a.nest_id, a.status) == \
               (b.id, b.x, b.y, b.z, b.speed, b.remaining_range,
                b.max_time, b.nest_id, b.status)
        # max_range 字段口径不同（回退 = None 跟随全局缺省；清单 = 显式值），
        # 解析语义必须相等：
        assert a.max_range is None
        assert b.full_charge_range(10000.0) == a.full_charge_range(10000.0) == 10000.0


def test_exp01_build_uavs_legacy_unchanged():
    """无 fleet_file 时行为与历史口径一致（轮转驻巢）。"""
    build = _load_exp01_build()
    uavs = build.build_uavs(_nests(capacity=2), {"problem": {
        "n_uavs": 3, "uav_speed": 15.0, "uav_max_range": 10000.0,
    }})
    assert [u.nest_id for u in uavs] == [0, 1, 0]     # 轮转
    assert all(u.remaining_range == 10000.0 for u in uavs)
    # 同构回退不写逐机 max_range（None = 跟随全局缺省，历史口径不动）
    assert all(u.max_range is None for u in uavs)
    assert all(u.full_charge_range(10000.0) == 10000.0 for u in uavs)
    assert all(u.max_time == pytest.approx(10000.0 / 15.0) for u in uavs)
