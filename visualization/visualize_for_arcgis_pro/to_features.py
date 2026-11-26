# -*- coding: utf-8 -*-
"""Pro 侧转换：GeoJSON -> 文件地理数据库要素类（在 ArcGIS Pro 的 Python 里跑）。

职责：把项目侧导出的两份 GeoJSON 用 arcpy.JSONToFeatures 转成要素类，再配置
时间启用与 3D 显示。本脚本**不在项目 .venv 跑**（那里没有 arcpy）。

跑法（ArcGIS Pro 的 Python 窗口 / Pro Python 环境）::
    python to_features.py --geojson <geojson目录> --gdb <输出.gdb>

前置：先在向地理数据库导入 GeoJSON 前，把坐标系统设为 WGS84（EPSG:4326，
纬经度），因为导出的是经纬度高程。高程存在几何 Z 里（若 Pro 未识别第三维，
用 Feature To 3D by Attribute 以第三维补 Z）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import arcpy
except ImportError:
    arcpy = None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GeoJSON -> ArcGIS Pro 要素类")
    ap.add_argument("--geojson", type=Path, required=True, help="项目侧导出的 GeoJSON 目录")
    ap.add_argument("--gdb", type=Path, required=True, help="输出文件地理数据库路径 (.gdb)")
    args = ap.parse_args(argv)

    if arcpy is None:
        print("未找到 arcpy：请在 ArcGIS Pro 自带的 Python 环境运行本脚本", file=sys.stderr)
        return 2

    geojson_dir, gdb = args.geojson, args.gdb
    if not geojson_dir.is_dir():
        print(f"GeoJSON 目录不存在：{geojson_dir}", file=sys.stderr)
        return 2
    gdb.parent.mkdir(parents=True, exist_ok=True)
    if not gdb.exists():
        arcpy.management.CreateFileGDB(str(gdb.parent), gdb.name)

    sources = {
        "tracks_points": geojson_dir / "tracks_points.geojson",
        "tracks_lines": geojson_dir / "tracks_lines.geojson",
    }
    for name, src in sources.items():
        if not src.is_file():
            print(f"缺少输入：{src}", file=sys.stderr)
            return 2
        out = str(gdb / name)
        arcpy.conversion.JSONToFeatures(str(src), out)
        print(f"已转换 {src.name} -> {out}")

    # 点层启用时间：时间戳字段 timestamp，事件时刻，逐要素显示
    pts = str(gdb / "tracks_points")
    try:
        arcpy.management.EnableTime(pts, "timestamp", None, "INSTANT", "")
        print("点层已启用时间（timestamp）——时间滑块可用")
    except Exception as exc:  # noqa: BLE001 - arcpy 异常类型不固定，如实报出
        print(f"时间启用需手动设置：{exc}", file=sys.stderr)

    print("\n后续（Pro 里手动，约 1 min）：")
    print("  1. 新建 Local Scene，坐标系 WGS84，加广州 DEM / 建筑底图")
    print("  2. 拖入 tracks_points：图层属性 -> 时间 -> timestamp，开时间滑块看飞行过程")
    print("  3. 拖入 tracks_lines：按 track_id 分类着色，看三周期航迹形态")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
