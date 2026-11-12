# -*- coding: utf-8 -*-
"""异构机队 JSON 清单（fleet spec）：加载、校验、装配 ``UAV``。

定位（设计文档：``task_allocation/docs/design_fleet_spec_2026-11-11.md``）：

- ``config.yaml`` 的 ``problem:`` 段只描述**同构**机队（``n_uavs`` + 全局
  缺省航程/速度）；需要逐机差异（型号 / 初始机巢 / 航程 / 续航 / 速度）时，
  用 ``problem.fleet_file`` 指向 JSON 机队清单，装配层按清单逐机生成。
- 机队清单是「装备台账」型数据（谁、什么型号、驻哪个巢、满电航程多少），
  与机巢 CSV 同类：可跨实验共享、可被扫参脚本批量生成（异构域扫描 D3）、
  可严格校验；实验旋钮仍留在 config.yaml。

Schema（**严格**：未知键一律报错，防拼写错误静默失效；单位显式写在键名）::

    {
      "version": 1,
      "defaults": {"speed_mps": 15.0, "max_range_m": 10000.0, "max_time_s": null},
      "models":   {"long": {"max_range_m": 10000.0, "speed_mps": 20.0}},
      "uavs": [
        {"id": 0, "model": "long", "home_nest": 0},
        {"id": 1, "home_nest": 1, "max_range_m": 8000.0, "speed_mps": 12.0}
      ]
    }

（示例数值仅演示三层继承链，不是实验标定值；实际航程以各实验
``uav_config.json`` 的说明为准。）

字段解析优先级：``defaults`` ← ``models[model]`` ← 逐机显式键；
``home_nest``（初始机巢 id）必填、不参与继承——机巢归属是逐机事实。

语义与同构回退保持一致（:func:`build_uavs_from_spec`）：

- 初始 ``remaining_range = max_range_m``（满电出巢）；
- ``max_time_s`` 缺省自动派生 ``max_range_m / speed_mps``（与航程预算
  自洽，不新增淘汰），显式给更小值即成独立绑定约束。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from task_allocation.common.types import UAV, Nest

__all__ = [
    "UavSpec",
    "FleetSpec",
    "load_fleet_spec",
    "build_uavs_from_spec",
]

#: 机型性能键（defaults / models / uav 三层通用的可继承字段）
_PERF_KEYS = ("speed_mps", "max_range_m", "max_time_s")
#: 逐机条目允许的键 = 性能键 + 身份键
_UAV_KEYS = ("id", "model", "home_nest", "meta") + _PERF_KEYS
#: 顶层允许的键
_TOP_KEYS = ("version", "description", "defaults", "models", "uavs")

_FLEET_SPEC_VERSION = 1


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UavSpec:
    """单机解析结果（三层继承后的最终值）。

    Attributes:
        id:         机号（清单内唯一；未显式给则取条目序号）
        home_nest:  初始机巢 id（必填，对应 ``Nest.id``）
        max_range_m: 满电航程（m，> 0）
        speed_mps:  巡航速度（m/s，> 0）
        max_time_s: 最大飞行时间（s）；``None`` = 派生 ``max_range_m / speed_mps``
        model:      型号名（仅溯源用，可空）
        meta:       透传元数据（进 ``UAV.meta``）
    """

    id: int
    home_nest: int
    max_range_m: float
    speed_mps: float
    max_time_s: float | None
    model: str | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    def resolved_max_time(self) -> float:
        """最大飞行时间：显式值优先，否则派生航程/速度。"""
        if self.max_time_s is not None:
            return float(self.max_time_s)
        return float(self.max_range_m) / float(self.speed_mps)


@dataclass(frozen=True)
class FleetSpec:
    """机队清单（解析 + 校验后）。"""

    uavs: tuple[UavSpec, ...]
    source: str | None = None  # 来源文件（溯源/报错用）

    def __len__(self) -> int:
        return len(self.uavs)


# ---------------------------------------------------------------------------
# 加载与校验
# ---------------------------------------------------------------------------


def _err(msg: str, source: str | None) -> ValueError:
    where = f"{source}: " if source else ""
    return ValueError(f"机队清单非法（{where}{msg}）")


def _check_keys(obj: Mapping[str, Any], allowed: tuple[str, ...], where: str,
                source: str | None) -> None:
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise _err(f"{where} 含未知键 {unknown}（允许：{list(allowed)}）", source)


def _perf_value(raw: Mapping[str, Any], key: str, where: str,
                source: str | None) -> float | None:
    """取出性能键值并做数值校验（None 合法 = 继续向下继承/派生）。"""
    if key not in raw:
        return None
    val = raw[key]
    if val is None:
        return None
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        raise _err(f"{where}.{key} 必须是数值或 null，得到 {val!r}", source)
    if key == "max_time_s":
        if float(val) <= 0:
            raise _err(f"{where}.{key} 必须 > 0 或 null，得到 {val}", source)
    elif float(val) <= 0:
        raise _err(f"{where}.{key} 必须 > 0，得到 {val}", source)
    return float(val)


def load_fleet_spec(path: str | Path) -> FleetSpec:
    """读取并严格校验 JSON 机队清单。

    校验失败一律抛 :class:`ValueError`（含文件名与具体条目），不静默修正。
    """
    path = Path(path)
    source = str(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"机队清单不存在：{path}") from None
    except json.JSONDecodeError as e:
        raise _err(f"JSON 解析失败：{e}", source) from e
    if not isinstance(raw, dict):
        raise _err("顶层必须是对象", source)
    _check_keys(raw, _TOP_KEYS, "顶层", source)

    version = raw.get("version", _FLEET_SPEC_VERSION)
    if version != _FLEET_SPEC_VERSION:
        raise _err(f"version 只支持 {_FLEET_SPEC_VERSION}，得到 {version!r}", source)
    desc = raw.get("description")
    if desc is not None and not isinstance(desc, str):
        raise _err("description 必须是字符串（人读说明，不参与装配）", source)

    # ── defaults / models：只允许性能键 ──────────────────────────
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise _err("defaults 必须是对象", source)
    _check_keys(defaults, _PERF_KEYS, "defaults", source)

    models = raw.get("models") or {}
    if not isinstance(models, dict):
        raise _err("models 必须是对象", source)
    for name, m in models.items():
        if not isinstance(m, dict):
            raise _err(f"models.{name} 必须是对象", source)
        _check_keys(m, _PERF_KEYS, f"models.{name}", source)

    uavs_raw = raw.get("uavs")
    if not isinstance(uavs_raw, list) or not uavs_raw:
        raise _err("uavs 必须是非空列表", source)

    # ── 逐机解析：defaults ← model ← 逐机键 ──────────────────────
    uavs: list[UavSpec] = []
    for i, entry in enumerate(uavs_raw):
        where = f"uavs[{i}]"
        if not isinstance(entry, dict):
            raise _err(f"{where} 必须是对象", source)
        _check_keys(entry, _UAV_KEYS, where, source)

        model_name = entry.get("model")
        if model_name is not None and model_name not in models:
            raise _err(f"{where}.model={model_name!r} 未在 models 中定义", source)
        if "home_nest" not in entry:
            raise _err(f"{where} 缺少必填键 home_nest（初始机巢 id）", source)
        home_nest = entry["home_nest"]
        if isinstance(home_nest, bool) or not isinstance(home_nest, int):
            raise _err(f"{where}.home_nest 必须是整数机巢 id，得到 {home_nest!r}", source)

        merged: dict[str, Any] = dict(defaults)
        if model_name is not None:
            merged.update(models[model_name])
        merged.update({k: entry[k] for k in _PERF_KEYS if k in entry})

        speed = _perf_value(merged, "speed_mps", where, source)
        rng = _perf_value(merged, "max_range_m", where, source)
        max_time = _perf_value(merged, "max_time_s", where, source)
        if speed is None or rng is None:
            raise _err(
                f"{where} 解析后缺少 speed_mps / max_range_m"
                f"（defaults、models.{model_name}、条目三层均未给出）",
                source,
            )

        meta = entry.get("meta") or {}
        if not isinstance(meta, dict):
            raise _err(f"{where}.meta 必须是对象", source)

        uavs.append(UavSpec(
            id=i if "id" not in entry else _check_id(entry["id"], where, source),
            home_nest=home_nest,
            max_range_m=rng,
            speed_mps=speed,
            max_time_s=max_time,
            model=model_name,
            meta=dict(meta),
        ))

    ids = [u.id for u in uavs]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise _err(f"机号重复：{dup}", source)
    return FleetSpec(uavs=tuple(uavs), source=source)


def _check_id(val: Any, where: str, source: str | None) -> int:
    if isinstance(val, bool) or not isinstance(val, int):
        raise _err(f"{where}.id 必须是整数，得到 {val!r}", source)
    return int(val)


# ---------------------------------------------------------------------------
# 装配：FleetSpec + 机巢 → list[UAV]
# ---------------------------------------------------------------------------


def build_uavs_from_spec(
    spec: FleetSpec,
    nests: Sequence[Nest],
    *,
    check_capacity: bool = True,
) -> list[UAV]:
    """按清单逐机装配 :class:`UAV`（初始位置 = 所驻机巢，满电出巢）。

    Args:
        spec: 已校验的机队清单
        nests: 场景机巢（``build_nest_scenario`` 产物）
        check_capacity: True 时校验初始驻巢数 ≤ 机巢容量（超容是配置错误，
            与 ``problem`` 注释「不初始超容」同一口径；置 False 跳过，
            交给运行期 ``strict_capacity`` 校验）

    同构等价：清单全机同性能时，产物与 ``build_uavs`` 同构回退**逐字段一致**
    （回归锚点见 ``tests/test_fleet_spec.py``）。
    """
    by_id = {n.id: n for n in nests}
    if not by_id:
        raise ValueError("无机巢，无法按清单生成机队")

    occupancy: dict[int, int] = {}
    out: list[UAV] = []
    for u in spec.uavs:
        nest = by_id.get(u.home_nest)
        if nest is None:
            raise ValueError(
                f"机队清单：机 {u.id} 的 home_nest={u.home_nest} 不存在"
                f"（可用机巢 id：{sorted(by_id)}）"
            )
        occupancy[nest.id] = occupancy.get(nest.id, 0) + 1
        out.append(UAV(
            id=u.id,
            x=nest.x,
            y=nest.y,
            z=nest.z,
            remaining_range=u.max_range_m,
            speed=u.speed_mps,
            max_time=u.resolved_max_time(),
            max_range=u.max_range_m,
            nest_id=nest.id,
            meta=dict(u.meta),
        ))

    if check_capacity:
        over = {
            nid: (occ, by_id[nid].capacity)
            for nid, occ in occupancy.items()
            if occ > by_id[nid].capacity
        }
        if over:
            detail = ", ".join(
                f"巢 {nid}：{occ} 架 > 容量 {cap}" for nid, (occ, cap) in sorted(over.items())
            )
            raise ValueError(f"机队清单初始超容（{detail}）")

    out.sort(key=lambda u: u.id)
    return out
