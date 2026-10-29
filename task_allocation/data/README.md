# 场景数据注册表（`task_allocation/data/`）

每个场景一个目录，目录名 = 场景 id；实验 `config.yaml` 的 `data.scenario`
声明场景名，文件名由 `data.path` / `data.dem` / `data.buildings` / `data.region_geojson` 声明。
**路径解析统一走 `task_allocation/common/scenario_data.py`**，不要在实验代码里
自行拼 `data/` 路径。

## 已注册场景

| 场景 id | 内容 | 文件 |
|---|---|---|
| `guangzhou` | 广州市研究区（2026-10-29 城市场景，当前实验场景） | `nest_location_data.csv`（3 机巢经纬度，实测）<br>`dem.tif`（地面基底 DEM）<br>`building/`（5 类建筑 shapefile：Commercial / EducationalCultural / PublicServices / Residential / TechnologyIndustry，含 `Height` 属性，共 20670 栋）<br>`region.geojson`（任务撒点区域门禁多边形） |
| `lasa_chengguan` | 拉萨市城关区（高原山地场景） | `nest_location_data.csv`（6 机巢经纬度，实测）<br>`Chengguan_DEM.tif`（ASTER GDEM 瓦片，约 28×32 km）<br>`Chengguan_district.geojson`（任务撒点区域门禁多边形） |

## 场景切换（只改 config，不改代码）

切换场景 = 改实验 config 的 `data:` 段 + `problem.n_nests`，其余代码零改动。
两套预设：

```yaml
# ── guangzhou（城市场景，当前）──
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

# ── lasa_chengguan（高原山地场景）──
data:
    scenario: lasa_chengguan
    source: file
    path: nest_location_data.csv
    dem: Chengguan_DEM.tif
    buildings: null              # ⚠️ 必须清掉：该场景无建筑目录，留着会响亮报错（故意的，不静默回退）
    region_geojson: Chengguan_district.geojson
    coordinate_column: Coordinate
problem:
    n_nests: 6                   # 城关区 CSV 共 6 条
```

注意：`problem/solver/selection` 的标定值（航程/时间窗等）本来就是拉萨口径；
切回即还原原标定，无需重标。

## 新增场景

1. 建 `data/<新场景_id>/`，放入该场景数据；
2. 实验 config：`data.scenario: <新场景_id>`（其余 data 键按文件名填）；
3. 在上表登记一行。

## 约定

- 数据是**实测/共享事实**，跨实验复用、不随实验改（实验可调项在各自
  `config.yaml`）；
- 文件命名保持场景内自解释；同名文件在不同场景目录下即为不同数据，解析
  按 `data.scenario` 严格隔离；
- 外部绝对路径（如临时 geojson）可直接写在 config 里，解析层透传。
