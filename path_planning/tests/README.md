# tests/ 测试规划

> 规则：每个模块文件都应可独立单测（architecture.md §2 依赖规则保证）。
> 测试文件命名 `test_<模块>.py`，与被测文件一一对应或按链路聚合。

| 测试文件 | 被测模块 | 关键不变量 |
|----------|----------|------------|
| `test_geometry.py` | utils/geometry | 线段-长方体相交与暴力采样一致；边界退化不漏判 |
| `test_math3d.py` | utils/math3d | 3D→2D 投影保距、曲率估计在圆弧上精确 |
| `test_scenario.py` | common/scenario | cuboid 化包围原建筑；坐标系口径正确 |
| `test_control_points.py` | offline/control_points | d1/d2/d3/禁飞缓冲全部满足；补虚点不侵入禁飞 |
| `test_link_builder.py` | offline/link_builder | 穿建筑/穿静态禁飞链路必删（完备性）；d* 截断正确 |
| `test_virtual_network.py` | offline/virtual_network | 4 对连线规则与 Fig.8 一致；转向可行性与 Fig.9 一致 |
| `test_link_cost.py` | cost/link_cost | Eq.(1)-(4) 分段边界（D 与 v²(1/a+1/a')/2 关系）、四种状态组合、随 D 单调 |
| `test_composite.py` | cost/composite | λ 极端退化（纯时间/纯风险）；口径与回填一致 |
| `test_risk_field.py` | methods/tpr/risk_field | TPR 非负；昼/夜切换正确；预计算=查询 |
| `test_dynamic_prune.py` | online/dynamic_prune | 剪枝视图不污染离线产物；禁飞撤销后立即恢复；连通性判定正确 |
| `test_route_search.py` | online/route_search | 小图上与暴力枚举最优一致 |
| `test_dubins.py` | online/dubins_smoother | 转向弧连续可导；Fig.14 过近点偏差收缩收敛 |
| `test_post_check.py` | online/post_check | 侵入必触发修正；超上限标记 SMOOTH_INFEASIBLE |
| `test_time_optimal.py` | online/time_optimal | 限速/曲率约束全程满足；回溯后速度剖面可行；总时间 ≥ 理想下界 |
| `test_pipeline_online.py` | online/pipeline | 端到端 OD → PlanResult 字段完整、原因标记正确 |
| `test_network_io.py` | offline/network_io | 参数指纹不匹配必须报错；往返序列化无损 |
| `test_cost_matrix.py` | cost/cost_matrix | 不可行 → ∞+原因；回填幂等 |
| `test_feedback.py` | interface/feedback | 三类触发事件条件各测到；反馈口径与 composite 同源 |
| `test_planner.py` | interface/planner | 门面字段契约（PlanResult 必含字段）；坐标口径兼容 |
| `test_baselines.py` | baselines/ | lin_original 不含禁飞/TPR 项；euclidean 代价口径正确 |
