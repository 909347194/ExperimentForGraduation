# -*- coding: utf-8 -*-
"""exp01_smoke 绘图底座：**场景数据定位**（路径解析 + 兼容判断的唯一入口）。

为什么单独拆出来（"拆分组织"）：底图有两条来源——DEM 瓦片与建筑 shapefile，
二者都需要回答同一组问题「**场景目录在哪 / 该目录下的 dem、building 叫什么 /
局部坐标系原点是多少**」。此前这套查找写在 ``dem.py`` 里且只认
``Chengguan_DEM.tif`` 一种文件名，切到城市场景（guangzhou，文件叫 ``dem.tif``）
就找不到瓦片、静默退回 45x45 降采样栅格。把定位逻辑集中到本模块后：

    * 文件名以 ``config.yaml`` 的 ``data.{scenario,dem,buildings}`` 为准，
      不再靠文件名猜；
    * 「这个场景有没有建筑」由本模块一处判定（``building_paths`` 为空即无），
      建筑背景与 DEM 背景才可能做统一的**优先/回退**裁决；
    * 找不到时**回退到旧启发式**（``Chengguan_DEM.tif`` / ``*_dem.tif``），
      保证老场景（lasa_chengguan）行为不变。

本模块**只依赖标准库**（不 import matplotlib / task_allocation），因此可以被
``dem.py`` / ``buildings.py`` 在模块顶层安全导入，不参与 theme 的循环时序。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = [
    "ensure_project_root",
    "scene_dirs",
    "local_frame",
    "warn_once",
]


# ---------------------------------------------------------------------------
# 目录与导入兜底
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()          # .../exp01_smoke/plot/base/scene.py
_BASE_DIR = _HERE.parent                  # plot/base
_EXP_ROOT = _HERE.parents[2]              # 实验根（plot 的上一级）

#: 建筑 shapefile 目录的候选名（config 未声明时的旧布局兜底）
_BUILDING_DIR_NAMES = ("building", "buildings", "Building", "Buildings")


def ensure_project_root() -> bool:
    """把**项目根**（含 ``task_allocation`` 包的目录）加入 ``sys.path``。

    这是「DEM 突然变糊 / 建筑突然不画」的共同根因防线。底图层要
    ``from task_allocation.methods.utils import ...``，而这**只在从项目根启动时
    才成立**（``python run.py``、``python -m pytest``）。一旦换成

        python -c "from plot import render_from_results; ..."   # cwd=实验目录
        cd 实验目录 && python 其它脚本

    之类入口，``sys.path[0]`` 是实验目录而非项目根，import 抛
    ``ModuleNotFoundError``，被各处的 ``except Exception`` 静默吞掉 ——> 底图
    静默降级。与其要求每个入口都记得加路径，**在真正需要 import 的这里兜底**：
    向上找含 ``task_allocation/__init__.py`` 的目录并插入。返回是否成功。
    """
    import sys as _sys

    try:
        import task_allocation  # noqa: F401  已可导入就不动 sys.path
        return True
    except Exception:
        pass
    proj = _HERE
    while proj.parent != proj:
        if (proj / "task_allocation" / "__init__.py").is_file():
            if str(proj) not in _sys.path:
                _sys.path.insert(0, str(proj))
            return True
        proj = proj.parent
    return False


# ---------------------------------------------------------------------------
# 场景目录解析（带缓存：一次出图会被多张图、多次调用反复问）
# ---------------------------------------------------------------------------

_SCENE_CACHE: dict[str, Any] | None = None
_WARNED: set[str] = set()


def warn_once(tag: str, msg: str) -> None:
    """同一原因每进程只报一次（底图降级必须留痕，但不能刷屏）。"""
    if tag in _WARNED:
        return
    _WARNED.add(tag)
    print(f"[exp01][scene][warn] {msg}", flush=True)


def _load_data_cfg() -> dict[str, Any]:
    """读实验配置里的 ``data`` 段。

    优先 ``<实验根>/config.yaml``（作者写的场景声明）；读不到再退回
    ``results/effective_config.json``（run.py 落盘的已解析配置，与 solution.json
    同源，避免因改图目录而找不到配置）。
    """
    try:
        import yaml

        for name in ("config.yaml", "config.yml"):
            path = _EXP_ROOT / name
            if not path.is_file():
                continue
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data = (cfg or {}).get("data") or {}
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    try:
        import json

        path = _EXP_ROOT / "results" / "effective_config.json"
        if path.is_file():
            cfg = json.loads(path.read_text(encoding="utf-8"))
            data = (cfg or {}).get("data") or {}
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _find_data_root() -> Path | None:
    """向上查找 ``data/`` 目录（场景数据根目录，其下按场景分子目录）。"""
    for p in [_BASE_DIR, *_BASE_DIR.parents]:
        data_dir = p / "data"
        if data_dir.is_dir():
            return data_dir
    return None


def _legacy_dem_tif(data_root: Path | None, data_dir: Path | None) -> Path | None:
    """旧启发式（config 未声明 ``data.dem`` 时用）：场景子目录优先，兼容平铺。"""
    search = [d for d in (data_dir, data_root) if d is not None]
    for root in search:
        if not root.is_dir():
            continue
        direct = root / "Chengguan_DEM.tif"
        if direct.is_file():
            return direct
        hits = sorted(root.glob("*/*_dem.tif")) or sorted(root.glob("*_dem.tif"))
        if hits:
            return hits[0]
    return None


def _resolve_dem_tif(data_cfg: dict[str, Any], data_root: Path | None,
                     data_dir: Path | None) -> Path | None:
    """DEM 瓦片：优先 ``data.dem`` 声明的文件名，缺省走旧启发式 + ``dem.tif``。"""
    name = data_cfg.get("dem")
    for root in (data_dir, data_root):
        if name and root is not None and root.is_dir():
            direct = root / str(name)
            if direct.is_file():
                return direct
    legacy = _legacy_dem_tif(data_root, data_dir)
    if legacy is not None:
        return legacy
    # 城市场景的常见命名：data/<scenario>/dem.tif（旧逻辑的 ``*_dem.tif`` 不认）
    for root in (data_dir, data_root):
        if root is None or not root.is_dir():
            continue
        direct = root / "dem.tif"
        if direct.is_file():
            return direct
        hits = sorted(root.glob("*/*.tif"))
        if hits:
            return hits[0]
    return None


def _resolve_building_paths(data_cfg: dict[str, Any], data_root: Path | None,
                            data_dir: Path | None) -> list[Path]:
    """建筑 shapefile 列表：**无建筑场景返回空列表**（这是合法的，不报警）。

    ``data.buildings`` 可以是目录（取目录下全部 ``*.shp``）或单个 ``.shp``。
    未声明时退回目录名候选（``building/`` 等）。
    """
    name = data_cfg.get("buildings")
    candidates: list[Path] = []
    if name:
        for root in (data_dir, data_root):
            if root is None or not root.is_dir():
                continue
            base = root / str(name)
            if base.is_dir():
                candidates = sorted(base.glob("*.shp"))
                break
            if base.is_file() and base.suffix.lower() == ".shp":
                candidates = [base]
                break
    if not candidates and data_dir is not None and data_dir.is_dir():
        for sub in sorted(d for d in data_dir.iterdir() if d.is_dir()):
            if sub.name.lower() in {n.lower().strip() for n in _BUILDING_DIR_NAMES}:
                candidates = sorted(sub.glob("*.shp"))
                if candidates:
                    break
    return candidates


def scene_dirs() -> dict[str, Any]:
    """返回场景数据定位结果（进程内缓存一次）。

    键：
        ``scenario``        场景名（``data.scenario``，可能为 None）
        ``data_root``       ``data/`` 目录
        ``data_dir``        ``data/<scenario>/``（找不到场景子目录时退回 ``data/``）
        ``dem_tif``         DEM 瓦片路径（可能 None）
        ``building_paths``  建筑 shapefile 列表（**空列表 = 该场景无建筑**）
    """
    global _SCENE_CACHE
    if _SCENE_CACHE is not None:
        return _SCENE_CACHE

    data_cfg = _load_data_cfg()
    data_root = _find_data_root()
    scenario = data_cfg.get("scenario") or None
    data_dir = None
    if data_root is not None:
        if scenario:
            cand = data_root / str(scenario)
            data_dir = cand if cand.is_dir() else data_root
        else:
            data_dir = data_root
        # 场景子目录不存在时（旧平铺布局）退回 data/ 根
        if scenario and not (data_root / str(scenario)).is_dir():
            data_dir = data_root

    info: dict[str, Any] = {
        "scenario": scenario,
        "data_root": data_root,
        "data_dir": data_dir,
        "dem_tif": _resolve_dem_tif(data_cfg, data_root, data_dir),
        "building_paths": _resolve_building_paths(data_cfg, data_root, data_dir),
    }
    _SCENE_CACHE = info
    return info


def local_frame(solution: dict[str, Any] | None):
    """由 ``solution.local_frame`` 构造局部 ENU 坐标系（建筑/DEM 与任务同框）。

    取不到（无 local_frame / 项目根不在 sys.path / 导入失败）返回 None —— 调用方
    据此回退，**不要**抛异常打断出图。
    """
    lf_info = (solution or {}).get("local_frame") or {}
    if "lat0" not in lf_info or "lon0" not in lf_info:
        return None
    if not ensure_project_root():
        return None
    try:
        from task_allocation.methods.utils import LocalFrame
    except Exception as exc:                                # pragma: no cover
        warn_once("local_frame_import", f"导入 LocalFrame 失败：{type(exc).__name__}: {exc}")
        return None
    try:
        return LocalFrame(float(lf_info["lat0"]), float(lf_info["lon0"]))
    except Exception as exc:                                # pragma: no cover
        warn_once("local_frame_build", f"构造 LocalFrame 失败：{type(exc).__name__}: {exc}")
        return None
