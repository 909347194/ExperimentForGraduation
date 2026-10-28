# -*- coding: utf-8 -*-
"""scenario_data.py — 场景数据注册与加载（``task_allocation/data/<scenario>/``）。

``task_allocation/data/`` 是**场景注册表**：每个场景一个目录（目录名即场景 id），
目录内放该场景的实测 / 共享数据（机巢 CSV、DEM 瓦片、区域门禁 geojson…）。
当前场景：

    lasa_chengguan/   拉萨市城关区（机巢 CSV + ASTER GDEM 瓦片 + 城关区多边形）

加载约定（2026-10-27 由"平铺 data/ 按场景分目录"）：

    * 实验 config 的 ``data.scenario`` 声明场景名，``data.path`` / ``data.dem`` /
      ``data.region_geojson`` 仍声明**文件名**（相对场景目录）；
    * 路径解析统一走本模块 :func:`resolve_file`，**禁止各实验自行拼
      data/ 路径**——目录组织再变只改这里；
    * ``resolve_file`` 支持绝对路径透传（如外部 geojson）。

新增场景步骤：

    1. ``data/<新场景_id>/`` 放入数据文件；
    2. 实验 config 的 ``data.scenario: <新场景_id>``；
    3. （可选）在 ``data/README.md`` 登记一行。
"""

from __future__ import annotations

from pathlib import Path

#: 场景注册表根（task_allocation/data/）
DATA_ROOT: Path = Path(__file__).resolve().parents[1] / "data"

#: 默认场景（未显式声明 data.scenario 时使用）
DEFAULT_SCENARIO: str = "lasa_chengguan"

__all__ = [
    "DATA_ROOT",
    "DEFAULT_SCENARIO",
    "list_scenarios",
    "scenario_dir",
    "resolve_file",
]


def list_scenarios() -> list[str]:
    """已注册的场景 id（data/ 下的目录名，按字典序）。"""
    if not DATA_ROOT.is_dir():
        return []
    return sorted(
        p.name
        for p in DATA_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith((".", "_"))
    )


def scenario_dir(scenario: str | None = None) -> Path:
    """场景数据目录 ``data/<scenario>/``（缺省 = DEFAULT_SCENARIO）。

    Raises:
        FileNotFoundError: 目录不存在（报错里列出已注册场景，便于排查）。
    """
    name = str(scenario or DEFAULT_SCENARIO).strip()
    d = DATA_ROOT / name
    if not d.is_dir():
        raise FileNotFoundError(
            f"场景数据目录不存在：{d}（已注册场景：{list_scenarios() or '无'}）"
        )
    return d


def resolve_file(filename: str | Path, scenario: str | None = None) -> Path:
    """解析场景数据文件：``data/<scenario>/<filename>``。

    * ``filename`` 为绝对路径时原样透传（外部数据不受场景目录约束）；
    * 相对路径时相对**场景目录**解析（不在此处校验文件存在——由调用方
      按需报错，保持"解析"与"校验"职责分离）。
    """
    p = Path(filename)
    if p.is_absolute():
        return p
    return scenario_dir(scenario) / p
