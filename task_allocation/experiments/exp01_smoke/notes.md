# exp01_smoke

**status:** active

## 目的

- 验证实验目录约定、`config.yaml` 读取与 `results/` 写入是否通畅。
- 验证**真实场景链路**：`data/nest_location_data.csv`（机巢经纬度）+ `data/ASTGTMV003_N29E091_dem.tif`（高程）→ `common/scenario.build_nest_scenario` → `Nest` 列表。
- **不**作为算法性能结论；methods 落地前仅为占位。

## 成功标准

1. `python run.py`（在本目录或指定路径）能读配置并写出 `results/metrics.json`、`results/run_meta.json`。
2. 目录结构符合 `experiments/README.md`。
3. `metrics.json` 中记录 8 个机巢的米制坐标 (x, y) 与 DEM 采样高程 z。

## 参数放哪里

| 内容 | 位置 | 理由 |
|---|---|---|
| 机巢经纬度、DEM | `task_allocation/data/` | 实测事实，跨实验复用 |
| 机巢个数、单巢容量、任务/UAV 规模 | 本目录 `config.yaml` 的 `problem:` | 实验设定，做规模/容量灵敏度时只改配置 |
| 算法与超参 | `config.yaml` 的 `method:` / `solver:` | 同上 |

## 已知问题

- 依赖 `PyYAML`；若环境未装，需先在 `pyproject.toml` 增加依赖并安装。
- `methods/` 仍为注释占位，本实验不调用真实求解器。
- `problem.n_uavs` 按 `n_nests × nest_capacity = 32` 填写；若机队规模不同请覆盖此字段。

## 下一步

- 实现 `TaskPool` / `UAVState` 最小数据结构后，将 smoke 改为一次最小合成实例的端到端调用。
- 任务点（`n_tasks`）目前仍是合成的，后续可仿照机巢增加 `data/task_location_data.csv` 并复用同一套坐标链路。
