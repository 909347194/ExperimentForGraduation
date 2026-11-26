# -*- coding: utf-8 -*-
"""任务产生侧：生成并校验作业点位（批次结构由配置决定，当前 1 周期 × 30 个）。

职责：任务点的合理性全部在这里把关——位置在场景数据域内**均匀撒点**（与机巢、
      机队无关），高度按 AGL 带换算，逐点只按算法入口的硬条件筛真高带/绝对带/
      全城 SDF/建筑棱柱；不合格直接丢弃重抽，绝不交给规划器兜底。水域与空地
      不排除（水面也是合法作业面）。归属只按最近巢标注。
边界：不碰算法。run.py 只读 tasks.json；生成器用 ``--make`` 单独运行一次。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .config import EXP_DIR, load_config
from .domain import data_bounds, nearest_nest
from .field import (
    absolute_band,
    load_guangzhou,
    load_or_build_field,
    plan_params,
    prisms_of,
)

TASKS_VERSION = "exp01_fullcity_30x1_v3"
#: 生成侧净空余量：判据是"复核侧阈值 8.0（clearance 6 + 竖直量化 2）"，本级按
#: 9.0 收——场是插值的，贴着 8.00 的点会在复核侧因插值差一点点被拒收。
_GEN_CLEARANCE_MARGIN_M = 1.0
_POINT_KEYS = ("id", "cycle", "x", "y", "z")


def task_tag(tid: int) -> str:
    """任务号 = ``T``+任务 id。id 从 1 连续编到 N，所以 T# 就是清单序号。"""
    return f"T{int(tid)}"


def task_tags(tasks: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    """``{id: T#}``。id 即清单序号（1…N），不再有跨周期的编号偏移换算。"""
    return {int(t["id"]): task_tag(int(t["id"])) for t in tasks}


def fmt_tags(ids: Sequence[int], tags: Mapping[int, str]) -> str:
    """把一串原始 id 写成 ``T2,T8,T10``（保留输入顺序）。"""
    return ",".join(tags.get(int(i), str(int(i))) for i in ids)


def tag_of_id(tid: int, tags: Mapping[int, str]) -> str:
    return tags.get(int(tid), str(int(tid)))


def sha256_of(tasks: Sequence[Mapping[str, Any]]) -> str:
    """点位指纹：只哈希"会改变几何"的字段，改一个毫米就会变。

    有了它，``run_meta`` 能证明这次结果用的是哪一版点位；``load_tasks``
    也能在有人手改 tasks.json 时立刻报警，而不是让结果悄悄对不上文档。
    """
    payload = json.dumps(
        [{k: t[k] for k in _POINT_KEYS} for t in sorted(tasks, key=lambda t: int(t["id"]))],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_tasks(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    p = EXP_DIR / str(cfg["tasks"]["file"])
    if not p.is_file():
        raise FileNotFoundError(
            f"任务清单不存在：{p}。先运行 python -m orchestration.experiments."
            "exp01_smoke.support.tasks --make 生成（点位合规在产生侧校验）。"
        )
    with p.open("r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("version") != TASKS_VERSION:
        raise ValueError(f"tasks.json 版本不符：{doc.get('version')} != {TASKS_VERSION}")
    tasks = list(doc["tasks"])
    per_cycle = int(cfg["tasks"]["per_cycle"])
    n_cycles = int(cfg["experiment"]["cycles"])
    for cycle in range(1, n_cycles + 1):
        batch = [t for t in tasks if int(t["cycle"]) == cycle]
        if len(batch) != per_cycle:
            raise ValueError(f"周期 {cycle} 有 {len(batch)} 个任务，应为 {per_cycle}")
    if len(tasks) != per_cycle * n_cycles:
        raise ValueError(
            f"任务总数 {len(tasks)} 与 per_cycle×cycles={per_cycle}×{n_cycles} 不符："
            "批次结构与配置不同步，先重跑 --make"
        )
    ids = sorted(int(t["id"]) for t in tasks)
    if ids != list(range(1, len(tasks) + 1)):
        raise ValueError(f"任务 id 必须是从 1 起的连续序号，实际首尾 {ids[:2]}…{ids[-2:]}")
    recorded = str(doc.get("points_sha256") or "")
    actual = sha256_of(tasks)
    if recorded != actual:
        raise ValueError(
            "tasks.json 的点位指纹与内容不符"
            f"（记录 {recorded[:12] or '空'}，实算 {actual[:12]}）：有人手改过点位或"
            "生成器已变。先重跑 support.tasks --make，不要让结果与文档对不上。"
        )
    return tasks


def generate(cfg: dict[str, Any]) -> dict[str, Any]:
    """生成全部任务点位：数据域内均匀撒点，只留"能进算法"的点。

    任务出现在哪里与机巢、机队无关（真实环境里需求是外生的）：平面位置在场景
    数据域矩形内均匀抽样，逐点只按算法入口的硬条件筛「场内 → AGL 带 → 绝对带 →
    全城 SDF → 建筑棱柱」，不合格就重抽。水域/空地不做额外排除——只要点能进
    规划器，就允许它存在。归属只按水平最近的巢标注，巢间负载由几何决定。
    """
    scenario = load_guangzhou(cfg)
    field, info = load_or_build_field(cfg, scenario)
    prisms = prisms_of(scenario, field, cfg)
    params = plan_params(cfg)
    band_lo, band_hi = absolute_band(field, params)

    active = set(int(v) for v in cfg["data"]["active_nest_ids"])
    nests = [n for n in scenario.nests if int(n.id) in active]
    if not nests:
        raise ValueError("没有启用的机巢")

    tcfg = cfg["tasks"]
    mw = cfg["mission_waypoints"]
    agl_lo, agl_hi = (float(v) for v in mw["agl_range_m"])
    clearance = float(mw["endpoint_clearance_m"])
    need = clearance + _GEN_CLEARANCE_MARGIN_M
    task_clear = float(mw["task_clearance_agl_m"])
    agl_margin = float(mw.get("task_agl_margin_m", 0.0))
    agl_target = task_clear + agl_margin  # 作业点离屋顶/地面的净空（带内余量）
    rng = np.random.default_rng(int(tcfg["generator_seed"]))
    jitter = float(tcfg["jitter_m"])
    x0, y0, x1, y1 = data_bounds(scenario)

    records: list[dict[str, Any]] = []
    anchors: list[dict[str, Any]] = []
    attempts = 0

    def try_point(x: float, y: float) -> dict[str, Any] | None:
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return None
        ground = float(scenario.ground_height_at(x, y))
        roof = float(scenario.roof_height_at(x, y))
        z = roof + agl_target
        agl = z - ground
        if not (agl_lo <= agl <= agl_hi):
            return None
        if not (band_lo <= z <= band_hi):
            return None
        sdf = float(field.at_one(x, y, z))
        prism = float(prisms.min_clearance([(x, y, z)], 1.0))
        if sdf < need or prism < need:
            return None
        # 两条铁律：① 坐标全精度落盘（round 会把 AGL 变成 9.9999，入口 ±1e-9 判越界）；
        # ② 余量（`task_agl_margin_m`）默认 0：真高带下界就是作业面，不做额外抬升。
        return {
            "x": float(x), "y": float(y), "z": float(z),
            "ground_z": float(ground), "roof_z": float(roof),
            "agl_m": float(agl),
            "sdf_m": sdf, "prism_m": prism,
        }

    # 每个任务一个锚点，在全域数据矩形里均匀抽；多周期编组时同一锚点会被各周期
    # 复用（抖动 ±jitter_m），单周期时锚点数 = 任务数。
    per_cycle = int(tcfg["per_cycle"])
    max_try = int(tcfg["candidates_per_task"])
    for k in range(per_cycle):
        anchor = None
        for _ in range(max_try):
            attempts += 1
            cand = try_point(float(rng.uniform(x0, x1)), float(rng.uniform(y0, y1)))
            if cand is not None:
                anchor = cand
                break
        if anchor is None:
            raise RuntimeError(
                f"锚点 {k} 在 {max_try} 次全域抽样后仍找不到合规点位"
                "（检查净空/真高带/绝对带是否过严）"
            )
        anchors.append(anchor)

    for cycle in range(1, int(cfg["experiment"]["cycles"]) + 1):
        for k, anchor in enumerate(anchors, start=1):
            point = None
            for _ in range(max_try):
                jx = float(rng.uniform(-jitter, jitter))
                jy = float(rng.uniform(-jitter, jitter))
                cand = try_point(anchor["x"] + jx, anchor["y"] + jy)
                if cand is not None:
                    point = cand
                    break
            if point is None:
                point = {key: anchor[key] for key in
                         ("x", "y", "z", "ground_z", "roof_z", "agl_m",
                          "sdf_m", "prism_m")}
            point["nearest_nest_id_diag"], _ = nearest_nest(point["x"], point["y"], nests)
            # id 是清单里的全局序号（1…N），不再用「周期·槽位」编码：
            # 任务就是一个合法点位，没有跨周期配对的含义。
            records.append({
                "id": (cycle - 1) * per_cycle + k,
                "cycle": cycle,
                "service_time_s": float(tcfg["service_time_s"]),
                "reward": float(tcfg["reward"]),
                **point,
            })

    # 最近巢标签由几何决定：记账里给出每巢附近分到几个、最近巢距离分布，
    # 供读者核对"负载不对称是场地的形状造成的，不是生成器凑的"；不表示任务归属。
    nest_xy = {int(n.id): (float(n.x), float(n.y)) for n in nests}
    per_nest: dict[str, int] = {}
    dist: list[float] = []
    for rec in records:
        nid = int(rec["nearest_nest_id_diag"])
        nxy = nest_xy[nid]
        per_nest[str(nid)] = per_nest.get(str(nid), 0) + 1
        dist.append(float(np.hypot(rec["x"] - nxy[0], rec["y"] - nxy[1])))

    # 落盘的 provenance 要能跟着仓库走：只记缓存文件名（不写本机绝对路径），
    # 耗时记到 0.01 s 就够——原来的 float 尾巴是 noise，不是证据。
    info = {
        "source": info.get("source"),
        "cache_file": Path(str(info.get("path", ""))).name,
        "elapsed_s": round(float(info.get("elapsed_s", 0.0)), 2),
    }
    return {
        "version": TASKS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "points_sha256": sha256_of(records),
        "field_info": info,
        "absolute_band_m": [float(band_lo), float(band_hi)],
        "generation": {
            "sampler": "uniform_xy（数据域矩形，与巢/机无关）",
            "bounds_xy": [x0, y0, x1, y1],
            "area_km2": round((x1 - x0) * (y1 - y0) / 1e6, 2),
            "attempts": int(attempts),
            "acceptance": round(per_cycle / max(attempts, 1), 4),
            "note": "只按算法入口硬条件筛点：水域与空地不排除",
            "filters": {
                "agl_range_m": [float(agl_lo), float(agl_hi)],
                "endpoint_clearance_m": clearance,
                "generation_clearance_m": need,
            },
            "per_nest": per_nest,
            "nearest_nest_m": {
                "p50": round(float(np.percentile(dist, 50)), 1),
                "max": round(float(np.max(dist)), 1),
            },
        },
        "rules": {
            "task_clearance_agl_m": task_clear,
            "task_agl_margin_m": agl_margin,
            "agl_target_m": agl_target,
            "endpoint_clearance_m": clearance,
            "generation_clearance_m": clearance + _GEN_CLEARANCE_MARGIN_M,
            "agl_range_m": [float(agl_lo), float(agl_hi)],
            "coordinate_precision": "full（未取整）",
        },
        "tasks": records,
    }


def arrivals(cfg: dict[str, Any], tasks: list[dict[str, Any]], nests: dict[int, Any]):
    """任务清单 → 上层 Task 列表（按周期分组），高度取产生侧已校验的值。"""
    from task_allocation.common.types import Task

    batches: list[list[Any]] = []
    for cycle in range(1, int(cfg["experiment"]["cycles"]) + 1):
        batch = []
        for raw in (t for t in tasks if int(t["cycle"]) == cycle):
            nest = nests.get(int(raw["nearest_nest_id_diag"]))
            if nest is None:
                raise ValueError(
                    f"任务 {raw['id']} 的最近巢诊断标签 {raw['nearest_nest_id_diag']} 不在启用巢内"
                )
            batch.append(Task(
                id=int(raw["id"]),
                x=float(raw["x"]), y=float(raw["y"]), z=float(raw["z"]),
                reward=float(raw["reward"]),
                meta={
                    "service_time_s": float(raw["service_time_s"]),
                    "nearest_nest_id_diag": int(raw["nearest_nest_id_diag"]),
                    "ground_z": float(raw["ground_z"]),
                    "roof_z": float(raw["roof_z"]),
                    "agl_m": float(raw["agl_m"]),
                    "sdf_m": float(raw["sdf_m"]),
                    "prism_m": float(raw["prism_m"]),
                },
            ))
        batches.append(batch)
    return batches


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="生成并校验 exp01_smoke 的任务点位")
    p.add_argument("--make", action="store_true", help="写 tasks.json")
    p.add_argument("--config", type=Path, default=None)
    args = p.parse_args(argv)
    if not args.make:
        p.error("请显式加 --make 才写 tasks.json")
    cfg = load_config(args.config)
    doc = generate(cfg)
    out = EXP_DIR / str(cfg["tasks"]["file"])
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    agl = [round(float(t["agl_m"]), 3) for t in doc["tasks"]]
    print(f"已写 {out}：{len(doc['tasks'])} 个任务，绝对高度带 {doc['absolute_band_m']}，"
          f"场来源 {doc['field_info']['source']}")
    print(f"  真高 {min(agl):.3f}–{max(agl):.3f} m（目标 {doc['rules']['agl_target_m']:.2f} m，"
          f"余量 {doc['rules']['task_agl_margin_m']:.2f} m）；"
          f"指纹 {doc['points_sha256'][:12]}")
    gen = doc["generation"]
    print(f"  全域抽样 {gen['attempts']} 次、接受率 {gen['acceptance']:.1%}；"
          f"最近巢归属 {gen['per_nest']}；"
          f"离最近巢 P50/最大 {gen['nearest_nest_m']['p50']}/{gen['nearest_nest_m']['max']} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
