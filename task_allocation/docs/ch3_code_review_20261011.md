# 第 3 章 ↔ 代码一致性审查报告

- **审查基准**：`Chapter3_README.md`（fed8de0 + 3ccc77c 版）
- **审查范围**：`task_allocation/` 全部代码（选择层 / MUAS 模型 / DMDE / 约束 / 代价 / 滚动层 / 测试）
- **审查方式**：逐节主张对照实现 + 既有测试与探针取证 + 全量测试运行
- **日期**：2026-10-11 ｜ 审查提交：`3ccc77c`

---

## 一、总体结论

章节主张与实现**总体一致**，核心机制（两阶段分层、选择性、DMDE 映射、终点机巢分层、滚动闭环）均有实现与测试对应。发现 **P1 级 1 项**（论文核心主张缺实验支撑，机制已就绪待复测）、**P2 级 5 项**（实现/一致性缺口，不阻断）、**P3 级 4 项**（文档表述对齐）。无 P0 阻断项。

| 章节 | 主张 | 实现落点 | 判定 |
|---|---|---|---|
| §1(1) | T_t = remain ∪ new ∪ release | `methods/task_pool.py` | ✅ |
| §1(2) | U_t^avail / K_avail(t)、故障释放 | `methods/uav_state.py` | ✅ |
| §1(3) | 起点已知、终点 z_ub 决策 | `problem.py` / `muas_stage.py` | ✅ |
| §1(4) | 任务数与能力不匹配 → 分层 | `selection/` + `muas/` | ✅ |
| §1(5) | 三维代价贯穿 | `cost/vertical_section.py` | ✅ |
| §2 | 两阶段 + 滚动总体框架、包含关系 | `rolling/horizon.py` | ✅ |
| §3.2 | 五因素优先级、指标归一化 [0,1] | `selection/priority.py` | ✅（见 P3-2） |
| §3.3 | Top-αK 预筛 α∈[2,3] | `priority.top_alpha_preselect`（α=2.5） | ✅ |
| §3.4 | 边际价值 ΔB/(ΔC+ε)、插入式估计 | `selection/marginal_gain.py` | ✅（见 P3-2） |
| §3.5 | 快速可行性 5 条 | `selection/feasibility.py` | ⚠️ 第 5 条缺位（P2-1） |
| §4.2 | 决策变量 y/x/π/z、一任务一机 | `muas_stage.build_solution` | ✅ |
| §4.3 | F1/F2/F3 + 标量 f、8 条约束 | `problem.py` | ✅ |
| §5(1) | 统一编码 + 代价映射/逆映射三规则 | `representation/*` | ✅ |
| §5(2) | 终点机巢分层求解 + 完整成本评价 | `nest_capacity.solve_end_nest_assignment` | ✅ |
| §5(3) | 选择性 y_j=0 / n_u=0 / K 解耦 | `_prune` / `select_participating_uavs` | ✅（见 P3-1） |
| §5(4) | 修复优先、罚则兜底 | `_repair_range` / 不动点循环 / Lamarckian | ✅ |
| §5(5) | 三维代价嵌入（不逐次航迹搜索） | `cost/cost_matrix.py` | ✅ |
| §5(6) | 算子与 Ming 2017 公式逐条对应 | `operators/*` | ✅（fidelity 测试） |
| §6 | 状态流转 + T_{t+1} + 事件闭环 | `rolling/*`、`task_pool` | ✅（见 P2-2） |

---

## 二、问题清单

### P1（影响论文主张成立性）

**P1-1｜"顺序随 DMDE 联合优化"主张仍无实验支撑**（机制已修复，实验未跑）
- 位置：实验层（exp03 缺）；根因此前在 `select_participating_uavs`（K 恒顶格 M-1 → M-K 塌缩）
- 现状：K 三层解耦已落地（`3ccc77c`，`tests/test_participation_decouple.py` 8 项），但**顺序敏感性复测尚未执行**
- 判据（已写入 TODO）：场景矩阵 M/K ∈ {1.5, 3, 5, 10} × K ∈ {3, 6, 9}，对照 (a) 顺序进搜索 (b) 贪心固定序 (c) 随机序，同预算多次运行；**M/K ≥ 5 时 (a) 显著优于 (b) 方可支撑主张**，否则论文措辞降级
- 状态：🔴 待复测（最高优先）

### P2（实现/一致性缺口，不阻断）

**P2-1｜§3.5 第 5 条"三维地理环境下不可达"在选择层缺位**
- 位置：`selection/feasibility.py: prefilter`
- 现象：可达性判定用欧氏距离（`_nearest_avail_uav`），不读三维 pairwise；地形遮挡/禁飞导致的 inf 代价只靠任务状态 `UNREACHABLE` 预标记才被过滤
- 建议：prefilter 接收 pairwise（或 cost_hint=inf）并以 `not isfinite(cost)` 判不可达，与 MUAS 层的 inf 判据统一

**P2-2｜事件响应是"整周期重规划"，非导言草案的"局部重选+重分配"**
- 位置：`rolling/horizon.py: run_cycle` vs 文档导言注记框
- 现象：事件命中后强制纳入选择 + 全量 MUAS 重跑（`forced_ids` 绕过 Top-αK），并非仅对受影响 UAV/任务的局部修复
- 建议：导言注记删除"局部"表述（§6 正文"提前重规划"与实现一致）；若要保留"局部响应"卖点则属新增工作量，论文现阶段不宜宣称

**P2-3｜终点兜底"一架冲突 → 全体改派"粒度过粗**
- 位置：`muas_stage._choose_end_nests` ①→②
- 现象：编码终点任一 UAV 超容/不可达/命中禁用 → 所有 UAV 全部转入内层重新分配，其余 UAV 的编码终点被丢弃
- 建议：按 UAV 粒度回退（仅冲突 UAV 进兜底）；方案 B（联合编码）下收益更大，可留 pro 分支

**P2-4｜两处成本口径不一致（latent）**
- 位置：`muas_stage._tour_cost`（空航次返回 0）vs `problem.tour_path_cost`（空航次+有终点计起点→巢）
- 现象：当前调用路径不触发差异，但将来复用 `_tour_cost` 会踩
- 建议：统一口径或在 `_tour_cost` 文档显式标注差异

**P2-5｜内层软容量罚与适应度权重耦合**
- 位置：`nest_capacity.solve_end_nest_assignment` 的 `overflow_penalty`（固定 1e4）
- 现象：默认权重（w_nest_capacity=w_penalty=λ=1）下与适应度权衡一致；用户改权重后内层"代价 vs 溢出"的权衡与适应度偏差
- 建议：overflow_penalty 从 problem 配置透传

**P2-6｜模型边界（已声明，留档）**
- 无任何合法机巢时非空航次 end=None → `end_nest_missing` 罚可见（审查修复 `79a05f4`），物理上"不返航"为模型边界
- 机巢容量为数量口径；时段占用（充电位/时间窗）留待扩展——两处均已在 Chapter3 §5(2) 与 TODO 显式声明

### P3（文档表述对齐）

**P3-1｜§5(3) y_j 判据公式与代码不完全一致**
- 章：`y_j = 0 if ΔC_j > R_j^eff`；码（`_prune`）：`λ·saving > R_j^eff·(1+margin)`
- 建议：章节公式补 λ 与裕度 (1+margin)（saving 与 ΔC 互为移除/插入对偶，语义同源）

**P3-2｜§3.4 插入式估计公式偏简**
- 章：`ΔC_i ≈ C_ai + C_ib − C_ab`；码（`insert_delta_cost`）：对所有插入位置取最小（更强）
- 建议：章节补"对全部插入位置取最小"一句

**P3-3｜死代码**
- `repair_rules/repair_terminal_genes` 无调用点（方案 B 管线用 `normalize_srp_segments` 重建替代）；建议 pro 分支接入或删除
- `_pick_nest_by_cost` 列下标 clamp 在欠尺寸矩阵下折叠多巢到同一列（方案 B 边界）

**P3-4｜方案 B 仅 SRP 路径有终点编码**
- balanced/overloaded 路径不生成终止基因；方案 A（默认）下终点由内层子问题覆盖（模型无关），方案 B 仅在 K<M 时生效——pro 分支文档需注明

---

## 三、已验证正确的关键主张（证据）

| 主张 | 证据 |
|---|---|
| 映射/逆映射/算子与 Ming 2017 公式逐条一致 | `test_operators_fidelity.py`、`test_tour_order_encoding.py` |
| 访问顺序进入搜索空间（规则 2(c) 迭代行替换） | `test_tour_order_encoding.py`（历史 S2 停滞缺陷回归锁定） |
| 终点机巢由 DE 代价槽驱动（非解码贪心） | `test_end_nest_encoding.py` + 构造探针（槽→远巢/近巢双向验证） |
| 内层终点分配 = 给定路线下最优 | `test_end_nest_assignment.py`（30 组随机实例 vs 暴力枚举逐一对齐） |
| 返航代价进入完整评价（陷阱规避） | `test_return_cost_participates_in_fitness`（差恰为返航差 300） |
| 返航义务缺失可见化 | `test_missing_end_nest_is_penalized` |
| K 三层解耦（不变式/架次预算/边际出动） | `test_participation_decouple.py` 8 项 |
| 评价链路确定性 → 评估缓存安全 | 代码审计（`_prune`/`_repair_range`/内层求解器无随机） |
| 五因素归一化 [0,1] + 权重和为 1 | `compute_priorities`：`map(_minmax_norm, (R,U,W,G,C))` |
| 规划期/落地期容量同判据（末态口径） | `check_nest_capacity` 双处复用 + `uav_state.apply_solution` 硬校验 |
| 任务池状态流转与 §6 公式一致 | `task_pool.begin_cycle/apply_solution/release/expire` 审计 |

**测试状态**：全量 52 项通过（含 20 项本日新增契约测试）。

---

## 四、建议处置顺序

1. **跑顺序敏感性复测**（P1-1）：起 exp03 骨架 → M/K 矩阵 → 三组对照 → 决定论文措辞强度；
2. P2-1（选择层三维不可达）与 P2-5（罚权重透传）：小改动、可与 exp03 一起做；
3. P2-2 文档措辞修订（导言"局部响应"）；
4. P2-3/P2-4 与 P3 各项：随 pro 分支/后续重构处理。
