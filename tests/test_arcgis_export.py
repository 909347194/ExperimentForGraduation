# -*- coding: utf-8 -*-
"""ArcGIS 中转站导出回归：锁住坐标转换、时间戳拼接与要素结构。

用内存合成 tracks 直接测 export()，不读写真实实验结果文件。
"""

from __future__ import annotations

import json

import pytest

from visualization.visualize_for_arcgis_pro import export as ex


def _tracks():
    return {
        "1": {
            "6": {
                "t0": 0.0, "t_end": 2.0,
                "t": [0.0, 1.0, 2.0],
                "x": [-890.7, -888.88, -886.61],
                "y": [1350.27, 1350.30, 1350.35],
                "z": [28.11, 28.15, 28.20],
            }
        }
    }


def test_export_coords_time_and_structure(tmp_path, monkeypatch):
    cfg = ex._load_config()
    cfg["output"]["geojson_dir"] = str(tmp_path / "geojson")
    p_pts, p_lines = ex.export(cfg, _tracks())

    pts = json.loads(p_pts.read_text(encoding="utf-8"))
    lines = json.loads(p_lines.read_text(encoding="utf-8"))

    assert len(pts["features"]) == 3
    assert len(lines["features"]) == 1

    first = pts["features"][0]
    lon, lat, alt = first["geometry"]["coordinates"]
    # 与 base1 巢址吻合（frame 原点附近），经纬度与高程都是合理量级
    assert 113.0 < lon < 114.0
    assert 23.0 < lat < 24.0
    assert 10.0 < alt < 130.0
    assert first["properties"]["timestamp"].startswith(cfg["time"]["base_iso"][:10])
    assert first["properties"]["timestamp"].endswith(".000")
    assert first["properties"]["track_id"] == "C1_U6"

    assert lines["features"][0]["properties"]["n_points"] == 3
    assert lines["features"][0]["geometry"]["type"] == "LineString"


def test_check_source_rejects_empty(tmp_path, monkeypatch):
    """空壳数据源必须报错，不能静默产出空 GeoJSON。

    用临时目录造空壳，不依赖真实实验结果当前是否为空——否则这个测试会随
    一次重跑而假失败（真实数据源非空时它测不到任何东西）。
    """
    res = tmp_path / "results"
    res.mkdir()
    (res / "trajectories.json").write_text(
        '{"1": {}, "2": {}, "3": {}}', encoding="utf-8"
    )
    cfg = ex._load_config()
    cfg["source"]["results_dir"] = str(res)

    with pytest.raises(ValueError, match="空壳"):
        ex._check_source(cfg)


def test_check_source_accepts_filled(tmp_path):
    """非空数据源应通过体检并返回规范化后的航迹字典。"""
    res = tmp_path / "results"
    res.mkdir()
    (res / "trajectories.json").write_text(
        json.dumps(_tracks()), encoding="utf-8"
    )
    cfg = ex._load_config()
    cfg["source"]["results_dir"] = str(res)

    tracks = ex._check_source(cfg)
    assert list(tracks) == ["1"]
    assert "6" in tracks["1"]
