# -*- coding: utf-8 -*-
"""项目侧导出：实验结果 -> ArcGIS Pro 可消费的 GeoJSON。

职责：把 exp01_smoke 的落盘航迹（ENU 米制 + 相对秒）转成 WGS84 经纬度高程 +
完整时间戳，输出点表与线表两份 GeoJSON。只读结果，不重跑规划、不改实验数据。

边界：
- 坐标转换复用建场那个 frame（从源实验 config 的 data 段重建），不自己拼变换。
- 现有落盘没有逐点冲突标志，故不写 conflict_flag（不伪造数据）。
- 数据源为空壳时直接报错退出，不产出误导性的空 GeoJSON。

跑法（仓库根、项目 .venv）::
    python -m visualization.visualize_for_arcgis_pro.export
    python -m visualization.visualize_for_arcgis_pro.export --check   # 只体检数据源
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from path_planning.common.config_io import load_config_mapping
from path_planning.common.scenario_loader import load_scenario

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # visualize_for_arcgis_pro -> visualization -> 仓库根


def _resolve(p: str | Path) -> Path:
    q = Path(p)
    return q if q.is_absolute() else REPO / q


def _load_config() -> dict[str, Any]:
    return load_config_mapping(HERE / "config.yaml")


def _frame_from_source(cfg: dict[str, Any]):
    """用源实验 data 段重建 ENU frame，保证与建场时同一变换。"""
    src = load_config_mapping(_resolve(cfg["source"]["config_file"]))
    d = src["data"]
    scenario = load_scenario(
        _resolve(d["scenario_dir"]),
        nest_count=int(d["nest_count"]),
        terrain_step_m=float(d["terrain_step_m"]),
        bbox_margin_m=float(d["bbox_margin_m"]),
        # 这里只需要 frame（坐标变换），地形填充结果用不上，跳过以省一次全网格填充。
        # frame 只由机巢经纬度决定，与地形无关，跳过不影响坐标一致性。
        fill_holes=False,
    )
    return scenario.frame


def _check_source(cfg: dict[str, Any]) -> dict[str, Any]:
    """体检数据源：文件齐全 + 航迹非空。返回轨迹字典。"""
    res = _resolve(cfg["source"]["results_dir"])
    traj_path = res / "trajectories.json"
    if not traj_path.is_file():
        raise FileNotFoundError(f"缺 {traj_path}")
    tracks = json.loads(traj_path.read_text(encoding="utf-8"))
    filled = {c: u for c, u in tracks.items() if u}
    if not filled:
        raise ValueError(
            f"{traj_path} 是空壳（各周期均无航迹）。\n"
            "请先重跑 exp01_smoke 恢复真实数据：\n"
            "  python -u -m orchestration.experiments.exp01_smoke.run\n"
            "（三周期约 20–25 min，场缓存已在，重跑不改算法）"
        )
    total = sum(len(u) for u in filled.values())
    try:
        shown = traj_path.relative_to(REPO)
    except ValueError:
        shown = traj_path
    print(f"数据源 OK：{shown}，周期 {sorted(filled)}，共 {total} 条航迹")
    return tracks


def _feature(geom: dict[str, Any], props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geom, "properties": props}


def export(cfg: dict[str, Any], tracks: dict[str, Any]) -> tuple[Path, Path]:
    frame = _frame_from_source(cfg)
    base = datetime.fromisoformat(cfg["time"]["base_iso"])
    xy_nd = int(cfg["output"].get("xy_ndigits", 7))
    z_nd = int(cfg["output"].get("z_ndigits", 3))

    point_features: list[dict[str, Any]] = []
    line_features: list[dict[str, Any]] = []
    for cyc, by_uav in sorted(tracks.items(), key=lambda kv: int(kv[0])):
        for uid, tr in sorted(by_uav.items(), key=lambda kv: int(kv[0])):
            n = len(tr["t"])
            coords: list[list[float]] = []
            for i in range(n):
                lat, lon, alt = frame.to_geodetic(tr["x"][i], tr["y"][i], tr["z"][i])
                coords.append([round(float(lon), xy_nd), round(float(lat), xy_nd),
                               round(float(alt), z_nd)])
                stamp = (base + timedelta(seconds=float(tr["t"][i]))).isoformat(timespec="milliseconds")
                point_features.append(_feature(
                    {"type": "Point", "coordinates": coords[-1]},
                    {"timestamp": stamp, "t_rel_s": tr["t"][i], "cycle": int(cyc),
                     "uav_id": int(uid), "track_id": f"C{cyc}_U{uid}"},
                ))
            if len(coords) >= 2:
                line_features.append(_feature(
                    {"type": "LineString", "coordinates": coords},
                    {"track_id": f"C{cyc}_U{uid}", "cycle": int(cyc), "uav_id": int(uid),
                     "t_start_s": tr["t0"], "t_end_s": tr["t_end"], "n_points": n},
                ))

    out_dir = _resolve(cfg["output"]["geojson_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, feats in (("tracks_points.geojson", point_features),
                        ("tracks_lines.geojson", line_features)):
        p = out_dir / name
        p.write_text(
            json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False),
            encoding="utf-8",
        )
        paths.append(p)
        try:
            shown = p.relative_to(REPO)
        except ValueError:
            shown = p
        print(f"写出 {shown}：{len(feats)} 个要素")
    if not line_features:
        raise RuntimeError("没有任何线要素，导出失败")
    return paths[0], paths[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="实验结果 -> ArcGIS Pro GeoJSON")
    ap.add_argument("--check", action="store_true", help="只体检数据源，不导出")
    args = ap.parse_args(argv)
    cfg = _load_config()
    try:
        tracks = _check_source(cfg)
        if args.check:
            return 0
        export(cfg, tracks)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"导出中止：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
