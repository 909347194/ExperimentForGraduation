# 项目 → ArcGIS Pro 中转站

只读实验落盘结果，转成 ArcGIS Pro 可消费的 GeoJSON；不改实验、不重跑规划。

## 数据链路

```
实验结果 (trajectories.json, ENU米制+相对秒)
   │  项目 .venv  export.py
   ▼  WGS84经纬度高程 + 完整时间戳
GeoJSON (点表 + 线表)
   │  Pro 的 Python  to_features.py  (arcpy)
   ▼
文件地理数据库要素类 → 时间滑块 / 3D航迹 / 冲突标注
```

## 跑法

项目侧（仓库根、项目 `.venv`）：

```bash
python -m visualization.visualize_for_arcgis_pro.export --check   # 先体检数据源
python -m visualization.visualize_for_arcgis_pro.export           # 导出 GeoJSON
```

Pro 侧（ArcGIS Pro 自带 Python）：

```bash
python to_features.py --geojson <geojson目录> --gdb <输出.gdb>
```

## 两个环境的分工

- `export.py` 必须在项目 `.venv` 跑：依赖 numpy 与 `path_planning`。坐标转换复用
  `scenario.frame.to_geodetic`（从源实验 config 的 `data` 段重建），保证与建场同一变换。
- `to_features.py` 必须在 Pro 的 Python 跑：依赖 arcpy，项目 `.venv` 没有。

## 数据源字段与口径

| 输出 | 来源 | 关键字段 |
|---|---|---|
| 点表 | `trajectories.json` 的 `t/x/y/z`，1 秒采样 | `timestamp`（基准日+相对秒）、`t_rel_s`、`cycle`、`uav_id`、`track_id` |
| 线表 | 同上的逐条航迹 | `track_id = C{周期}_U{机号}`、`cycle`、`uav_id`、`t_start_s`、`t_end_s` |

- `z` 是绝对 ENU 高程（已含 10–15 m AGL），地理高程直接用。
- 时间基准在 `config.yaml` 的 `time.base_iso`；改它只改显示时间戳，不动几何。

## 两个已知限制（不伪造数据）

1. **无逐点冲突标志**：现有落盘 `loop_trace.json` 各周期 `conflicts_before/after` 全为 0，
   `failures.json` 只有整票失败诊断，没有 CPA 逐点结果。故不写 `conflict_flag`。
   要支持冲突着色，需先在规划侧把 CPA 最近点事件按时间戳落盘，再扩展导出。
2. **数据源当前为空壳**：`trajectories.json` 现为 27 字节空字典（各周期无航迹），
   `export.py --check` 会明确报错。恢复方式：重跑 exp01_smoke（约 20–25 min，场缓存已在）。
