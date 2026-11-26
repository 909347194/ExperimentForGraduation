# 场景数据（仓库根 `data/`）

`data/` 是**两个方法的共享场景层**，不属于 `task_allocation`，也不属于 `path_planning`：
同一份广州/拉萨数据，上层用它算三维航程，下层用它建 SDF、判净空。谁都不复制第二份。

## 1. 谁怎么读它

| 使用方 | 入口 | 解析规则 |
| --- | --- | --- |
| 第 3 章 `task_allocation` | `task_allocation/common/scenario_data.py` | `DATA_ROOT = <repo>/data`；场景目录取 `DATA_ROOT/<data.scenario>`，文件由 `data.path` / `data.dem` / `data.buildings` / `data.region_geojson` 声明 |
| 第 4 章 `path_planning` | 实验 `config.yaml` 的 `scenario.data_dir` | 相对路径按**项目根**解析（`support/experiment_config.py::_resolve`），即写 `data/guangzhou` |

两条纪律：

1. **不要在实验代码里自行拼 `data/` 路径**——各走自己的解析器，换布局时只改一处；
2. **实验只读数据，不回写**。实验可调的是自己的 `config.yaml`，不是 `data/`。

## 2. 已注册场景

| 场景 id | 内容 | 文件 |
| --- | --- | --- |
| `guangzhou` | 广州市研究区（城市场景，两个方法的当前实验场景） | `nest_location_data.csv`（3 机巢经纬度，实测）<br>`dem.tif`（地面基底 DEM）<br>`building/`（5 类建筑 shapefile：Commercial / EducationalCultural / PublicServices / Residential / TechnologyIndustry，`Height` 属性，共 20 670 栋）<br>`region.geojson`（任务撒点门禁多边形）<br>`nofly_static.geojson` / `nofly_dynamic.geojson`（禁飞区，仅第 4 章场景装配使用）<br>`region_shp/`（面要素原始 shapefile，备查） |
| `lasa_chengguan` | 拉萨市城关区（高原山地场景，第 3 章标定口径） | `nest_location_data.csv`（6 机巢经纬度，实测）<br>`Chengguan_DEM.tif`（ASTER GDEM 瓦片，约 28×32 km）<br>`Chengguan_district.geojson`（任务撒点门禁多边形）<br>`Chengguan_shp/`（行政区面 shapefile） |

## 3. 场景切换（只改 config，不改代码）

```yaml
# ── guangzhou（城市场景）──
data:
    scenario: guangzhou
    source: file
    path: nest_location_data.csv
    dem: dem.tif
    buildings: building          # 建筑目录；无建筑场景置 null
    region_geojson: region.geojson
    coordinate_column: Coordinate
problem:
    n_nests: 3                   # 广州 CSV 共 3 条

# ── lasa_chengguan（高原山地）──
data:
    scenario: lasa_chengguan
    source: file
    path: nest_location_data.csv
    dem: Chengguan_DEM.tif
    buildings: null              # ⚠️ 必须显式清掉：该场景无建筑目录，留着会响亮报错（故意的，不静默回退）
    region_geojson: Chengguan_district.geojson
    coordinate_column: Coordinate
problem:
    n_nests: 6                   # 城关区 CSV 共 6 条
```

第 4 章对应的写法是把 `scenario.data_dir` 指到 `data/<场景 id>`（如 `data/guangzhou`），
`nofly_*` 与 `building/` 由场景装配自己找。

注意：`problem/solver/selection` 的标定值（航程/时间窗等）是拉萨口径；切回即还原原标定，无需重标。

## 4. 新增场景

1. 建 `data/<新场景 id>/`，放入该场景数据；
2. 第 3 章：实验 config 里 `data.scenario: <新场景 id>`（其余 `data` 键按文件名填）；
3. 第 4 章：实验 config 里 `scenario.data_dir: data/<新场景 id>`；
4. 在上表登记一行。**两侧共用同一目录，不要各建一份。**

## 5. 入不入库

- 小文本（CSV / geojson / shp 的 prj/dbf 等）入库，保证 clone 即能跑通城市场景；
- 大二进制（原始建筑 `.shp`/`.dbf` 源、DEM）按 `.gitignore` 走外部数据源补齐；
- `.npz` 场缓存、实验结果图不入库。

## 6. 数据是事实，不是参数

机巢坐标、任务点坐标、机巢处 SDF、巢间连通性都是**当前 `data/guangzhou` 数据集**的结论，
不是算法常量。换巢、换城、换 DEM 后的复核清单见 `path_planning/Chapter4_README.md` §6.1 与
`task_allocation/Chapter3_README.md` 的场景假设表。
