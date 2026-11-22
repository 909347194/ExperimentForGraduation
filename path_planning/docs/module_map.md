# 模块职责对照表

一页看完"谁干什么、吃什么、吐什么、被谁消费"。按数据流排序，不按目录排序。
详细口径见 `architecture.md` / `time_mapping.md` / `conflict_model.md`。

## 对外

| 模块 | 吃什么 | 吐什么 | 谁消费 |
|---|---|---|---|
| `api.py::PathPlanner` | `PlanRequest` + `TourPlanningContext` | `PlanResult` / `FleetPlan` | 上层 `horizon` / `closed_loop` |

`api.py` 只做装配与契约归一，不含算法。深路径对上层是封闭的。

## 公共层 `common/`

| 模块 | 职责 | 关键约束 |
|---|---|---|
| `plan_types.py` | 轨迹/段/请求/结果类型 | `Trajectory` 零段即抛；`validate()` 查自洽 |
| `plan_params.py` | 参数体系 + `validate` + 派生量 | **只定义结构不定义数值**；数值进实验配置 |
| `vehicle_spec.py` | 机体规格与推力/加速度派生 | `f_min ≤ m·g ≤ f_max` 必须逐机校验 |
| `flight_budget.py` | 续航（时间）/ 航程（距离）双口径判定 | 两条独立，互不派生 |
| `failure_codes.py` | 失败码 + 严重度 + 处置建议 | 降级码与不可行码必须分清 |
| `scenario_loader.py` | GeoJSON/CSV/地形装配 | 建筑 dbf 是 GBK；机巢列纬度在前 |
| `scenario_types.py` | 场景/机巢/禁飞区数据类 | 高程同源查询 |
| `grid_spec.py` | 栅格契约（轴对齐 + 斜栅格） | 轴序 `(x, y, z)`，yaw 只对斜栅格 |
| `time_intervals.py` | 半开区间与集合运算 | 弦集合用半开区间，端点不能漏 |

## 空间层 `methods/spatial/`

| 模块 | 职责 | 关键约束 |
|---|---|---|
| `occupancy_sdf.py` | 棱柱 → 占位 → 两遍 EDT → 单栅格 SDF | 第 2 轴是高度；正 = 自由 |
| `world_field.py` | 全域一次建场 + 窗口取样 + 覆盖契约 | 场参数必须进指纹，否则缓存串味 |
| `world_field_builder.py` | 建场（含 `obstacle_prisms` 共用棱柱） | 建场与真几何闸同一批棱柱；`pad_m` 非间距公倍数当场报错 |
| `corridor_window.py` | 走廊斜栅格（半宽 + margin + 建场外扩） | `pad_m` 必须是两轴间距公倍数 |
| `rrt_path_search.py` | 多初值几何路径（SDF 自适应步长） | 端点豁免圈内降阈值 |
| `rrt_config.py` | RRT 参数结构与 `from_params` 派生 | 无独立默认值 |
| `initial_paths.py` | 候选评估 / 排序 / 去重 | 空列表 = `RRT_NO_PATH`，不抛异常 |
| `sdf_casadi.py` | SDF → CasADi 三线性读数（窗口场/全域场统一归一化） | 窗口硬边界必须一起加；`WorldField` 是 **z-major** `(nz,nx,ny)`，不转置就是场上错位 |
| `prism_clearance.py` | 真几何净空闸（STRtree + 方框粗筛 + 竖直区间） | 补 SDF 的三项先天乐观 |

## 时间层 `methods/timeline/`

| 模块 | 职责 | 关键约束 |
|---|---|---|
| `nlp_config.py` | NLP 旋钮（引导管 + 绝对 z 飞行带） | 数值只从 `PlanParams`/`VehicleSpec` 派生 |
| `trajectory_optimizer.py` | 主 NLP：min ΣΔt，口径 B，三级退让 | 速度三条分别约束，不写 min；z 必须有上下界 |
| `initial_guess.py` | 弧长重采样 + 逐节点 (v,a,Δt) | 重采样按弧长，不按节点序号 |
| `splines.py` | Hermite 求值 / 导数 / Gauss 弧长 | 节点两侧加速度不等，逐段取单边极限 |
| `feasibility.py` | 独立复核（9 类检查） | 净空豁免、z 飞行带与优化器必须同步 |
| `fallback.py` | `OPT_FAILED` 降级巡航剖面 | 保守 1.2 加在时长上，`time_source=estimate` |

## 冲突层 `methods/conflict/`

| 模块 | 职责 | 关键约束 |
|---|---|---|
| `chord_sampling.py` | Hermite → 匀速弦（递归细分） | 实测偏差进 T 空间要按 z 放大 |
| `cpa_geometry.py` | 匀速直线对闭式 CPA + 时间重叠 | 相对静止要单独处理 |
| `ellipsoid_metric.py` | 椭球 → T 空间球 | 水平两轴必须相等 |
| `pair_certificates.py` | 三级证书 + 合并 CPA | 证书与 CPA 阈值严格互补 |
| `conflict_detector.py` | 全对检测 + 证书统计 + 弦缓存 | `min_distance` 是 T 距离，不是米 |
| `conflict_clusters.py` | 并查集连通域 | `pairs` 是 `(id_a, id_b)` 元组 |

## 协调层 `methods/coordination/`

| 模块 | 职责 | 关键约束 |
|---|---|---|
| `local_region.py` | 局部域（CPA 中点 + 余弦钟形权重） | 域中心取两点中点，不取可动侧 |
| `detour_candidates.py` | 让路候选几何 | 端点权重强制 0；垂直优先 |
| `local_replan.py` | 候选重解 + 复核 + 真几何 + 对手证书 | 引导管必须有；首个通过即停；`field` 直接收 `context.field`（全域场） |
| `global_recheck.py` | 检测-聚类-让路-全对复检 | 机号大者让路；改不动就提前退出 |

## 编排与状态

| 模块 | 职责 | 关键约束 |
|---|---|---|
| `methods/planning/tour_planner.py` | 单 tour 全流程（逐段）+ 交付闸 + 预算判定 | 每段独立走廊/建窗；`t0` 由上游给；整条组装后必须过交付闸 |
| `methods/planning/delivery_gate.py` | 整条航迹在全域场上的 0.2 m 稠密净空 + 全域真几何 | 判据口径与实验审计同源；段内「涨阈值求解」只留余量，不收窄验收 |
| `methods/planning/leg_segments.py` | 纯竖向段 / 悬停段 / 沿弧长采点 | 竖向限速用 `v_up`/`v_dn`；悬停段标 `is_hover` |
| `methods/trajectory_registry.py` | 跨周期航迹注册表 | 同机不得两条未结束；`replace` 供让路回写 |

## 实验层 `experiments/`

下表 `support/` 指**各实验目录下的私有副本**（本实验自持），实验之间不共享；项目公共层仍是 `path_planning/common/`。

| 模块 | 吃什么 | 吐什么 | 关键约束 |
|---|---|---|---|
| `support/experiment_config.py` | `config.yaml` | `ExperimentConfig`（含 `PlanParams`、飞行带、误差口径） | 未知键/缺键一律报错；一实验一份数值 |
| `support/world_field_store.py` | 场景 + 场参数 | `WorldField`（可缓存）+ 真几何棱柱 | 缓存键含 origin/spacing/shape/agl/pad/bias |
| `support/fleet_spec.py` | `uav_config.json` | 逐机 `VehicleSpec` + `PlanRequest` | 同机只允许一条 tour；机号升序决定让路顺序 |
| `support/runtime.py` | 配置 | 场景/场/棱柱/门面装配 | 飞行带上界按 `ground_max+agl_max` 截断（按 ground_min 算会压出穿楼天花板） |
| `support/scene_meshes.py` | 场景 + bbox | 建筑棱柱（合并为一个 Mesh）+ 地面两片三角 | 建筑只取 bbox 留 350 m 内的：两万栋逐个画会拖垮渲染 |
| `support/scene_3d.py` | 落盘航迹 + 场景 | 三维图（PNG/PDF/HTML）：`split_by=uav` 一机一块、`split_by=stage` 一段一块；`--out` 缺省写 `<results>/figures` | 只画航迹 bbox + 300 m 内的建筑；竖向放大是阅读约定，图注必须写明；分段标签可换（拍/轮） |
| `support/experiment_run.py` | runtime + 机队 | `plan_fleet` + 四份 JSON | `effective_config` 写解析后的实际值；`solution` 落**交付**航迹（让路后的几何），不落首轮原航迹 |
| `support/flight_metrics.py` | 航迹 | 时长/弧长/两套净空/复核/预算 + 协调轮次与 `moves` | SDF 与真几何净空都要记；`pairs`（判过的对）不要拿 `conflict_ids`（涉及机号）替代 |
| `support/audit_checks.py` | 落盘 `solution.json` | 五项判据 | 只读文件，能对旧结果复跑 |
| `support/trajectory_margins.py` | 一条已交付航迹 | 六项约束利用率 + 时间下界比 + 绕行比；另给 `split_legs`/`leg_trajectory` 把航迹切成逐段 | 只报"用到几分"，不做判据；`LegSlice` 的 `[t0,t1]` 只含飞行，停靠时长单列 `hover_s`（否则会重复计） |
| `support/quality_report.py` | 落盘航迹 + 场 + 请求停留点 | 逐 leg 明细行 + 五项判据（时间来源 / 分段边界 / 运动学裕度 / 稠密净空 / 端点豁免） | 分段边界：leg 数 = 1 + 停过的中间停留点数，且每个 leg 终点与停留点 ≤1 mm；稠密净空剔端点豁免球；多段绕行比参照"逐段直线和" |
| `exp01_smoke/` | 单机巢间航段 | 一条可交付航迹 + 十项审计 | 长航段必须 120/8；终点真高 18 m；单机专属再加时间来源、分段边界、稠密净空（0.2 m 复采样）与端点豁免 |
| `exp02_two_uav/` | 同线反向双机 | 让路航迹 + 十项审计 | 两机同型才能进同一协调组；"检出/让路/冲突"三条互相咬合 |
| `exp03_registry/` | 遗留航迹 + 本轮新任务 | 注册表语义 + 九项审计 | 遗留航迹只作约束、不可改；冲突项按"含遗留"重算；"巢2↔巢0 走廊被楼群切断"是**当前三巢**的连通性结论，换巢要重测 |
| `exp04_ablation/` | 同一冲突对 + 4 个协调变体 | 消融表（档位/代价/遗留冲突）+ 五项审计 | 规划只做一次；检测配置用基线参数建一次、四变体共用，否则冲突数不可比 |
| `exp05_multileg/` | 单机多段航次（巢→A→B→巢） | 3 段航迹 + 十一项审计（多"分段计时自洽"） | 停留点必须是可悬停净空点；服务时长 0 的停留点不成 leg；多段总时长是逐段串行相加，不是跨段联合优化 |
| `exp06_cycles/` | 双机 3 拍多周期（投放 → 派活 → 排航迹 → `busy_until` → prune） | 4 条真实航迹 + 34 项审计（逐票/逐拍/跨周期冲突/prune/超框） | 每拍 `t0` 冻结在拍起点；`busy_until` 累计不覆盖；空拍必须清空 `last_trajectories`；跨周期冲突用虚拟 id `cycle*10+uid` 检测 |
| `exp07_two_rounds/` | 单机两轮链式（轮1 停在任务点，轮2 从该点续飞） | 2 条真实航迹 + 16 项审计（逐轮质量 / 链式三项接口 / 停靠点净空 / 共时冲突 / 总预算） | 终点不必是巢（`PlanRequest` 只要 stops 非空）；轮2 的起点/时刻/速度三项必须与轮1 末端全 0 差；衔接点上检测器读到的一对是航迹接缝，判据是"时间窗不重叠 + CPA 落在衔接时刻" |

> 注意：`exp06` 的派活算法已迁出本模块——`CyclicTaskAllocator` 现在住在
> `closed_loop/allocators/cyclic.py`（环的上层端口实现），实验层只把配置里的任务点转成
> `LoopTask`。本模块不再持有任何上层派活代码。

**数据依赖口径**：机巢坐标、任务点坐标、机巢处 SDF、巢间连通性都是**当前 `data/guangzhou` 数据集**的结论，不是算法常量。代码里没有一处写死机巢坐标（实验只声明 `home_nest`，坐标从 `scenario.nests[i].position` 读）；换巢的改动清单见 `path_planning/README.md §7.1`。真正会"随数据失效"的只有：各实验 `uav_config.json` 的端点、`exp06` 投放点、端点豁免阈值、巢间连通性结论、显式给了 `half_width_m` 的走廊。

## 测试对应

| 测试 | 覆盖 |
|---|---|
| `test_spatial_field` / `test_corridor_window` / `test_rrt_*` / `test_initial_paths` | 空间层（20 项起） |
| `test_splines` / `test_initial_guess` / `test_trajectory_optimizer` / `test_feasibility` / `test_fallback` | 时间层 |
| `test_conflict_geometry` / `test_conflict_certificates` / `test_conflict_detector` | 冲突层（40 项） |
| `test_prism_clearance` | 真几何闸 |
| `test_coordination` / `test_global_recheck` | 协调层（局部域/候选/重规划/轮次编排） |
| `test_tour_planner` / `test_trajectory_registry` | 编排 / 状态 / 门面 |
| `test_leg_quality` | 分段账（leg 切分 / 切片 / 停留点边界判据） |
| `closed_loop/tests/test_cyclic_allocator`（不在本模块） | 多周期派活（投放/空闲筛选/最近巢/装不下留痕/`task_ids`） |
| `exp01_smoke/audit.py` 等 | 真实场景端到端（不进 unittest，按实验单独跑） |

