# 架构与关键实现口径

本文记录**分层结构**与**会静默出错的口径决策**。详细公式见
`conflict_model.md`（冲突判定）与 `time_mapping.md`（时间映射）。

---

## 1. 分层与数据流

```
data/guangzhou (DEM / building / nest / nofly)
  └─> common/scenario.py        场景装配（机巢、地形、建筑、禁飞区 → 局部 ENU）
       ├─> common/grid_spec.py     栅格坐标契约（轴对齐 + 斜栅格统一）
       ├─> methods/utils/          坐标变换、DEM 米制网格、建筑图层
       └─> methods/spatial/
            ├─ occupancy_sdf.py    障碍棱柱 → 占位栅格 → 两遍 EDT → SDF
            └─ world_field.py      **全域一次建场** + 窗口取样
                 ├─> corridor_window.py   走廊斜栅格（ROI + margin）
                 ├─> rrt_path_search.py   多初值几何路径
                 └─> methods/timeline/    单机时空联合 NLP（CasADi + IPOPT, min ΣΔt）
                      └─> methods/planning/tour_planner.py  逐段编排 + 悬停段拼接
                           └─> methods/planning/delivery_gate.py  交付闸（全域 0.2 m 稠密 + 真几何）
                                └─> 预算判定 → trajectory（带时间，经 api.py::PathPlanner 出面）
                                     └─> methods/trajectory_registry.py  注册 + 跨周期遗留
                                          ├─> methods/conflict/       冲突检测（时空解耦）
                                          │    └─> methods/coordination/  空间让路 + 局部重规划
                                          └─> busy_until / prune  →  closed_loop  拍末结算
```

`api.py` 是唯一对外门面（`plan` / `plan_fleet` / `coordinate` / `busy_until` / `prune`）：上层不再拼
"当前在飞集合"，那是 `TrajectoryRegistry` 的事；也不再自己决定"先排后调还是边排边调"，
`plan_fleet` 已经按"先排全部、再统一协调、最后回写 Registry"走完。

两条不可混的口径：

| 层 | 时空关系 | 做什么 |
|---|---|---|
| 单机层（timeline） | **必然耦合** | 几何、速度、时间联合优化，目标 min ΣΔt |
| 协调层（conflict / coordination） | **必须解耦** | 冻结出发时刻与速度口径，只改空间几何；时间由单机层重解 |

---

## 2. SDF 为什么是全局的

参考实现 `path_planning1/core/world_field.py` 给出的结论（原注释）：

> 以前每个航段都把窗口内的楼重新栅格化 + 两遍 EDT（2.8 km 约 3–4 s）。这里把这一步
> 挪到**进程启动**：按全域轴对齐栅格算一次，之后每条 OD 只做一次三线性取样
> （约 0.2 s），RRT / NLP 的输入形状与以前完全一样。

按窗口现算时，一条 OD 要试多个初值、每个初值可能多次重规划，建场开销会压过优化本身。
本项目实测（广州 4.8 × 4.7 km，`spacing=(4,4,2)`）：

| 指标 | 实测值 |
|---|---|
| 栅格形状 | `(1201, 1179, 121)` |
| 节点数 | 171,333,459 |
| 竖直范围 | 3.3 – 243.3 m（兜住 224.5 m 最高楼） |
| 建场耗时 | **85.4 s**（一次性） |
| 常驻内存 | **857 MB**（`sdf` float32 z-major + `occupancy` bool） |
| 走廊窗口取样 | `(710, 11, 40)` → **17 ms** |
| 占位比例 | 15.13%（与 `sdf<0` 比例一致，符号未接反） |

代价：全域栅格是**轴对齐**的，走廊窗口是**斜**的，取样必然跨格插值——同一几何在两种
基准下 SDF 数值不同。因此场参数（origin / spacing / shape / bias）必须进结果指纹
（`field_signature`），否则缓存会把"另一套 SDF 基准"的解当成同一条 OD 的解静默复用。

`world_field.check_coverage()` 在启动自检与每次取样前验证窗口八角点落在场内（容差半格）。
不能靠 `map_coordinates(mode="nearest")` 兜底：出界时它会静默取边缘值，返回一个看起来
正常的解。

---

## 3. 三条离散化口径

1. **第 2 轴是高度**。`(4,4,2)` 意为水平 4 m、竖直 2 m。轴序接反不抛异常，只会让竖直方向
   按 4 m 离散、水平按 2 m，净空判据静默偏移。
2. **正值 = 自由空间**，单位米。`edt(~occ)` 给自由区距离、`edt(occ)` 给障碍内部深度；
   接反后整张 SDF 退化为 0，路径笔直穿楼。
3. **在完整高度上算完再裁带**。若先裁飞行带再算 EDT，带底/带顶的距离被栅格边界截断成 0，
   贴带飞的路径会被判为"全程贴障碍"。

### 3.1 保守膨胀

每个 footprint 先 `buffer(半格对角线 / cos(π/(4·quad_segs)))`，竖直区间各外扩半格，再判
**格心**。两个理由：

- **整栋漏检**：只测格心时，比格距还窄的房子可能一个格心都不落进去。膨胀半径
  ≥ 格点最大空隙 `0.5·√(sx²+sy²)` 后，任何棱柱至少含一个格心。
- **障碍被缩**：裸格心判据让障碍最多"缩"半格，SDF 比真几何乐观。膨胀后障碍只大不小，
  SDF 成为保守下界。

实测印证：屋顶上方 2 m 名义几何应为 `+2.00`，实际 `+2.55`（膨胀抬高顶面 0.5 m 后为
`2.5`，再由三线性插值微调）。

### 3.2 节点密度与段内约束（2.8 km 航段的实测教训）

**节点满足净空 ≠ 航迹满足净空。** NLP 的净空约束写在节点与每段的
`clearance_samples_per_segment` 个内插点上；Hermite 在节点之间仍可鼓出。2.8 km 航段、
60 节点（间距 ~48 m）、段内 3 点时，独立复核实测最小净空 **−9.7 m**：节点上都是 +6，
曲线在节点之间穿进了楼。把节点数提到 120（间距 ~24 m）仍不够（−5.4 / −7.5 / −7.8 m），
必须同时把段内约束加密到 **8 点（间距 ~2.6 m，与复核的 2 m 采样同量级）**才通过。
代价是单段求解从几秒涨到 4–5 分钟——这是"2.8 km 一条腿"的固有成本，不是可以随手
调小的参数。

推论（写进配置注释）：`n_nodes` 与 `clearance_samples_per_segment` 必须按**段长**定，
不是全局常数。短段（≤300 m）用 60/3 即可；城市长航段（km 级）必须 120/8。

**高度带必须写进 NLP，而且要写在交付曲线上。** 位置是自由变量、目标只有 `min ΣΔt`：
不给 z 上下界时，求解器会**爬高越过障碍**（实测爬到 208 m AGL，远超 120 m 建场带）。
后果有两层：一是超出低空作业口径；二是冲突层按椭球竖直半轴 15 m 判定，两机在不同
高度层会被判成"安全"，让路机制永远不触发。因此 `NlpConfig.alt_min_m/alt_max_m` 由
`TourPlanningContext.alt_range_m` 给出，`VerificationLimits.alt_range_m` 同步校核。

两条实测教训（2026-11-21，exp02 首次 120/8 跑）：

1. **只钉节点不够。** 交付的是 Hermite 段，节点 z 全在带内时曲线仍会鼓出去：节点
   贴顶、`v_z≠0` 时段中点偏移 = `Δt·(v_z0−v_z1)/8`，实测节点 ≤103.30、曲线到
   **110.42**。故 z 带按交付曲线在段内 `s∈{0.2,0.4,0.6,0.8}` 上一起约束
   （`trajectory_optimizer._hermite_axis`）。这条与"节点满足净空 ≠ 航迹满足净空"是
   同一个结构性问题：NLP 内部用节点线性插值，交付是 Hermite。
2. **上界不能按最低地面算。** 按 `ground_min + agl_max` 得 103.30，而清障 6 m 要求
   机组在高楼上方飞到 ~110 m（同一航段复核净空掉到 4.17/5.30 m）。广州地面 2.3–75.6 m，
   按最低地面算等于给全城压了一条穿楼的天花板。改为 `ground_max + agl_max`
   （实测带 `[5.30, 173.63]`），代价是低洼处实际真高可超过 `agl_max`——与
   `z_envelope_from_ground` 的取舍同理，属已知近似，不是判据放宽。

**SDF 轴序：内层与复核必须读同一个场。** `WorldField.sdf` 存 **z-major
`(nz,nx,ny)`**（按层取样快一个量级），而 `SdfOnGrid` 要按 `(nx,ny,nz)` 展平成
"i 变化最快"喂 `interpolant`。局部重规划把 `context.field`（全域场）直接传进内层，
早先漏了转置，等于整场换了坐标：同一点内层读 68.62 m、复核读 53.01 m。症状极具
误导性——局部让路候选在 120/8 下全部 `Maximum_Iterations_Exceeded` /
`Infeasible_Problem_Detected`（同一候选 40/3 却能收敛），节点被推到 155 m、弦上读到
−60 m，看着像"求解器不行"。现由 `SdfOnGrid` 归一化两种布局并对形状不符抛错，
`test_sdf_on_grid_matches_world_field` 用不对称小场逐点对拍守住。

---

## 4. 端点净空必须豁免（两级：NLP 与交付）

实测机巢处 SDF：

| 机巢 | 位置 z | SDF(点) | SDF(z+6) |
|---|---|---|---|
| nest0 | 13.1 | **+1.63** | +7.82 |
| nest1 | 12.0 | **−0.63** | +6.69 |
| nest2 | 11.0 | **+1.47** | +7.56 |

> 这三个数是**当前数据集**的实测值，会随机巢位置调整而变化（换巢流程见 `README.md §7.1`）。
> 代码不写死坐标：机巢来自 `data/guangzhou` 的场景数据，实验只声明 `home_nest`。

机巢位于地面，净空天然接近 0（nest1 因保守膨胀略为负）。因此 **NLP 的净空约束必须对
首末节点（或首末一小段）豁免**，否则问题恒不可行。豁免窗口应按"起飞/降落爬升段"给，
而不是只给节点 0 与 N——单点豁免会让相邻节点仍不可行。

**交付侧还有第二级豁免，口径必须与审计同源**：`delivery_gate` 与实验的"稠密净空"判据
都先剔除距端点 `endpoint_exempt_radius_m = 25 m` 球内的采样点，球内按
`endpoint_clearance_m = 2.0 m` 单独判、球外仍按 `clearance_m = 6.0 m`。两级不能互相顶替：
NLP 豁免是为了"解得出"，交付豁免是为了"别把机巢地面那几个点算成违规"，球外一律 6 m。

同时实测：机巢 0 → 机巢 1 的**直线**最小 SDF 为 **−34.29 m**，即直线必然穿过多栋建筑。
这就是 RRT 与连续优化的必要性证据（不是"可能穿"，是"一定穿"）。这条同样是数据集结论，
换巢后要重测（`exp03` 的"巢2↔巢0 走廊被楼群切断"同理）。

---

## 5. 协调层的三处结构性坑

都是"看起来实现了、跑起来等于没做"的静默失效，属性各不相同，必须分开记：

1. **位置是自由变量，让路位移会被 min ΣΔt 抹平**。内层 NLP 只钉住两端点，其余节点是
   决策变量，目标是最短时间——不给约束时它必然把折线拉回直线段，于是"候选几何"只活在
   初值里。修法是 `NlpConfig.guide_radius_m`（几何引导管：``‖x_i − path_i‖ ≤ r``），
   协调层把它设成 `params.local_replan_guide_radius_m`；**初规划保持 None**，因为 RRT
   折线本来就该被拉直。判据：所有候选时长几乎相同 + 让路量在轨迹里查不到。
2. **候选必须对"对手航迹"取证，不能只验自身可行性**。只做自身复核时 24 档全 accepted，
   而竖直 8 m 远不够椭球要求的 15 m（T 空间）——于是每轮都挑最便宜那档，冲突永远不动。
   `replan_locally(..., others=..., detection=...)` 把同批次其余航迹一起送进证书判定。
3. **初值几何必须"沿当前轨迹采样"，不是取节点**。单段轨迹的 `trajectory_nodes` 只有两个
   端点，而端点冻结，于是让路候选中没有任何可动节点，`replan_locally` 永远返回
   `NO_LOCAL_SOLUTION`；跨轮迭代时还必须以"上一轮已经让开的那条线"为起点，否则改完撞回去。
   `global_recheck._polyline_of` 按 5 m 步长沿轨迹采样。

另有一条**场景可解性前提**（不是算法缺陷）：椭球水平半轴 30 m 时，若被让路航线的端点离
对方航线不足 30 m，则任何"端点冻结"的局部位移都消不掉冲突——起点本身就在安全泡内。
合成测试的场景必须让端点落在泡外（例如 90 m 长的十字交叉），否则测的是"无解场景"。

---

## 6. 文件职责与命名口径

命名统一用**完整的词**，不用缩写或过于宽泛的单字（`field.py` 这类会被改成
`occupancy_sdf.py` / `world_field.py`）。

| 文件 | 职责 |
|---|---|
| `common/plan_types.py` | 轨迹、段、请求/结果、时间来源等纯数据类型 |
| `common/plan_params.py` | 参数体系（空间 / 时间 / 检测 / 协调 / 策略）与校验 |
| `common/failure_codes.py` | 13 个失败码、严重度分级与处置建议 |
| `common/flight_budget.py` | 续航 / 航程双口径预算判定 |
| `common/time_intervals.py` | 半开区间与区间集合运算 |
| `common/vehicle_spec.py` | 机体规格与推力/加速度派生量 |
| `common/scenario_types.py` | 场景/机巢/禁飞区数据类，高程同源查询 |
| `common/scenario_loader.py` | GeoJSON / CSV / 地形装配（禁飞区 id 加文件前缀） |
| `common/terrain_hole_filling.py` | 地形 NaN 空洞四邻扩散填充 |
| `common/grid_spec.py` | 规则三维栅格契约（轴对齐 / 斜栅格） |
| `methods/utils/*` | 坐标、DEM GeoTIFF、米制地形、建筑图层（自包含复制） |
| `methods/spatial/occupancy_sdf.py` | 棱柱栅格化 + 两遍 EDT + 单栅格查询 |
| `methods/spatial/world_field.py` | 全域场对象、三线性取样、覆盖契约 |
| `methods/spatial/world_field_builder.py` | 建场（竖向包络、禁飞区、节点上限） |
| `methods/spatial/field_contract.py` | 场契约异常 |
| `methods/spatial/field_artifact.py` | 场指纹（可复现性） |
| `methods/spatial/corridor_window.py` | 走廊半宽规则、斜栅格窗口、建场外扩 |
| `methods/spatial/rrt_path_search.py` | RRT 采样（窗内、SDF 自适应步长、端点豁免、多初值） |
| `methods/spatial/rrt_config.py` | RRT 参数结构 + `from_params` 派生（无独立默认值） |
| `methods/spatial/initial_paths.py` | 候选评估 / 去重 / NLP 初值排序 |
| `methods/spatial/sdf_casadi.py` | SDF → CasADi 三线性插值转接头（NLP 净空读数；窗口场 `(nx,ny,nz)` 与全域场 z-major `(nz,nx,ny)` 都归一化后再展平） |
| `methods/spatial/prism_clearance.py` | 真几何净空闸（STRtree + 方框粗筛 + 竖直区间，`sqrt(水平²+竖直²)`） |
| `methods/timeline/splines.py` | Hermite 求值 / 解析导数 / Gauss 弧长 / 按时刻查询 |
| `methods/timeline/initial_guess.py` | 弧长重采样 + 逐节点 (v,a,Δt) 初值 |
| `methods/timeline/nlp_config.py` | NLP 旋钮 `NlpConfig`（几何引导管 `guide_radius_m` + 绝对 z 飞行带） |
| `methods/timeline/trajectory_optimizer.py` | 主 NLP（min ΣΔt、口径 B、三级退让、Hermite 交付） |
| `methods/timeline/feasibility.py` | 独立复核：速度三项/加速度/推力/净空/走廊/端点/z 飞行带/Δt/弧长相容 |
| `methods/timeline/fallback.py` | `OPT_FAILED` 降级巡航剖面（`time_source=estimate`，保守 1.2） |
| `methods/conflict/chord_sampling.py` | Hermite → 匀速弦（递归细分 + 实测偏差） |
| `methods/conflict/cpa_geometry.py` | 匀速直线对闭式 CPA、时间窗交集 |
| `methods/conflict/ellipsoid_metric.py` | 椭球 → T 空间球（包围盒余量、判定半径） |
| `methods/conflict/pair_certificates.py` | 三级证书 + 合并 CPA（阈值严格互补） |
| `methods/conflict/conflict_detector.py` | 全对检测、弦化缓存、证书统计 |
| `methods/conflict/conflict_clusters.py` | 并查集连通域（单点改 vs 多点同时改） |
| `methods/coordination/local_region.py` | 局部域（CPA 中点 + 余弦钟形过渡权重） |
| `methods/coordination/detour_candidates.py` | 让路候选几何（垂直优先、端点冻结、边界权重归零） |
| `methods/coordination/local_replan.py` | 候选逐一重解时间 → 复核 → 真几何净空 → 对手证书（首个通过即停） |
| `methods/coordination/global_recheck.py` | 检测-聚类-让路-全对复检的轮次编排（机号大者让路） |
| `methods/planning/tour_planner.py` | 单航次编排：逐段走廊 → RRT 多初值 → NLP → 复核 → 真几何 → 组装整条 → 交付闸 → 预算 |
| `methods/planning/delivery_gate.py` | 交付闸：整条航迹在全域场 0.2 m 稠密净空 + 全域真几何；不过即 `CLEARANCE_VIOLATION`（不带航迹） |
| `methods/planning/leg_segments.py` | 航段原语：纯竖向段（`v_up`/`v_dn`）、悬停段（`is_hover`）、沿弧长采点 |
| `methods/trajectory_registry.py` | 跨周期航迹注册表（`busy_until`、在飞集合、落地注销、整体平移） |
| `api.py` | 顶层唯一门面 `PathPlanner`：`plan` / `plan_fleet` / `coordinate` / `busy_until` / `prune` |
| `experiments/<exp>/support/` | 每个实验私有：配置解析、建场缓存、机队清单、指标、审计、质量报告、主流程（实验间不共享） |

> 多周期派活（`CyclicTaskAllocator`）不属于本模块：它是环的上层端口实现，已迁到
> `closed_loop/allocators/cyclic.py`，实验层只负责把配置里的任务点转成 `LoopTask`。

---

## 7. 数据侧已修正的坑

记录一次，避免重复踩：

- `building/*.dbf` 是 **GBK**；pyshp 默认 UTF-8 会抛 `dbfFileException`。
- 机巢 CSV 是**单字段** `"23.111467N 113.307078E"`（**纬度在前**，列名 `Coordinate`，
  文件带 BOM）。顺序搞反不报错，只得到无意义高程。
- DEM `nodata=255`，`to_metric_terrain` 置零并记权重，四邻全无效时得 `0/0 = NaN`。
  实测 158 × 161 网格有 **10.89%** 空洞（含内部一整块约 900 格）。不补的后果是
  **静默放行**：净空判据 `z_clear + clearance ≤ ground` 与 NaN 比较恒为 False。
  已由 `terrain_hole_filling.fill_terrain_holes()` 以四邻扩散补齐（13 轮，残量 0）。
- `meta.bounds` 的 right/top 是"节点外沿"，`query` 在 `x == right` 处越界返回 NaN。
  可规划范围必须收到**节点包络**（`data_bounds_xy`），否则最外 30 m 一圈整圈是空洞。

