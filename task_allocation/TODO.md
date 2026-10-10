# task_allocation 实施路线图(TODO)

依据 `Chapter3_README.md` 的技术路线,结合当前代码实际完成度拆解。
状态标记: `[x]` 已完成 / `[~]` 部分完成 / `[ ]` 待做 / `[!]` 阻塞

---

## 0. 现状盘点

### 0.1 论文技术路线 → 代码落点

| 技术路线环节(§8) | 代码位置 | 状态 | 备注 |
|---|---|---|---|
| 动态任务到达 → 任务池更新 | `methods/task_pool.py` | [x] | `T_t = remain ∪ new ∪ release` 归类、周期推进、释放/过期 |
| 当前可用 UAV 确定 | `methods/uav_state.py` | [x] | `U_t^avail` / `K_avail(t)`、状态机、UAV↔任务绑定 |
| 任务收益/紧迫度/等待/协同/代价 | `methods/selection/priority.py` | [x] | 五项加权 `P_i(t)`,权重归一化 |
| 基础优先级预筛选(Top-αK) | `selection/priority.top_alpha_preselect` | [x] | α∈[2,3] |
| 边际收益增量选择 | `methods/selection/marginal_gain.py` | [x] | `insert_delta_cost` 已实现 3D 插入式 `ΔC_i ≈ C_ai + C_ib − C_ab` |
| 快速可行性检查 | `methods/selection/feasibility.py` | [x] | prefilter / postcheck |
| 选择性多机巢 MUAS 模型(§4) | `methods/muas/problem.py` | [x] | 决策变量 y/x/π/z、双目标 F1/F2;已含机巢容量约束 |
| 三维地理代价 `C_ij^3D`(§5(5)) | `methods/muas/cost/*` | [x] | DEM + 垂直切面 + 代价矩阵,已与真实 DEM 打通 |
| 统一编码 / 映射 / 逆映射(§5(1)(2)(3)) | `methods/muas/representation/*` | [x] | 基因用 `uav_id` 哨兵值区分三类:`≥0` 起始 / `-1` 巡游 / **`-2` 终止(终点机巢)**;终止基因随 `cost_vector` 参与差分变异(见 §0.4、§2 的 2.6) |
| 进化算子 | `methods/muas/operators/*` | [x] | crossover / mutation / extinction / scale_factor |
| 约束处理(§5(4)) | `methods/muas/constraints/` | [~] | `nest_capacity.py` 已落地(两道校验共用判定);其余约束仍在 `problem.check_feasible` |
| 改进 DMDE 求解器(§5) | `methods/muas/solvers/dmde_solver.py` | [x] | 可导入、可求解 |
| **DMDE ↔ 选择性 MUAS 桥接** | `methods/muas/solvers/muas_stage.py` | [x] | **新增**:assignment → π/y/z、选择性剔除、航程修复 |
| 动态滚动与闭环(§6) | `methods/rolling/` | [x] | `horizon.py` 单周期编排 + 多周期推进;`event_trigger.py` 事件识别 |
| 对照方法 | `baselines/*` | [ ] | greedy / random_assign 仅规范注释占位 |
| 场景与数据 | `common/scenario.py`、`common/task_scenario.py` | [x] | 机巢 CSV + DEM + 坐标转换;合成任务复用同一链路 |
| 实验 | `experiments/exp01_smoke` | [x] | **端到端已跑通**(两阶段 + 3 周期滚动) |

### 0.2 已解除的阻塞

- ~~`repair_rules/` 三个文件缺失 → 整条链 import 失败~~:已补齐,DMDE 可导入可求解。
- ~~没有把 DMDE 输出翻译成 `AllocationSolution` 的桥接~~:见 `solvers/muas_stage.py`。

### 0.4 按 `docs/guides/1整体方法.md` §5 逐条核对(权威依据)

文档 §5 标题即「**改进**离散映射差分进化算法」,§8 创新点第 3 条要求
「支持起点固定、**终点可选**、部分空闲/不执行的**统一编码**离散映射差分进化扩展」。
→ 论文**就是要写"改进 DMDE"**,改进的落点是**结构扩展**(文档 §5 的 5 个方面),
不是必须改动变异/交叉算子。

| §5 条目 | 文档要求 | 当前实现 | 判定 |
|---|---|---|---|
| (1) 面向任务序列的统一编码 | 编码同时表达 UAV归属 + 访问顺序 + **终点机巢** | 终止基因 `uav_id=-2` 进编码,`target_id` = 机巢下标 | ✅ 已达成 |
| (2) 多机巢终点选择 | 终点机巢作为航次特殊终止节点,在 `B_1..B_K` 间选择 | 终止基因随 `cost_vector` 末尾 K 个分量做温度自适应匹配 | ✅ 已达成 |
| (3) 选择性任务分配 | 允许 `y_j=0`、`n_u=0` | 已达成,但靠解码后 `_prune` 剔除,非编码层 | ⚠️ 效果达成,落点偏低 |
| (4) 约束处理 | 在**个体解码和适应度评价过程中**处理航程/时间窗/唯一性 | `_prune` / `_repair_range` / `choose_end_nests` 正在 `evaluate` 内 | ✅ 符合 |
| (5) 三维地理代价嵌入 | 用预计算 `C_ij^3D`,不在每次评价中重做航迹搜索 | 代价矩阵预计算 + `CostProvider` | ✅ 符合 |

**结论(已闭环)**:2.6 已实现,终点机巢作为终止基因进入编码并参与差分进化,
论文创新点第 3 条「终点可选的统一编码」可自证。详见 §2 的「2.6 验收结果」。

### 0.5 实验场景与文档假设的偏离

文档 §1(4) 设想的问题特征是 **任务远多于机**:`|T_t| ≫ K_avail(t)`。
当前 `exp01_smoke` 是 **24 机 / 10 任务**(机远多于任务),与该假设**相反**。

后果:`K = min(K_avail, M−1)` 的 srp 限制在「机多任务少」时严重浪费
(10 任务只派 9 架,24 架里 15 架空闲)。但在文档设想的 `|T| ≫ K` 场景下
K 会自然取满 `K_avail`,**不构成算法缺陷**。

→ 结论:这不是算法局限,是**实验配置偏离**。exp02/03 应把规模改成
任务数 ≫ 机数(如 60 任务 / 8 机)。

---

## 1. 阶段 0:打通第二阶段(P0) —— 已完成

| # | 任务 | 状态 |
|---|---|---|
| 0.1 | 补 `repair_rules/invalid_mutator.py` | [x] |
| 0.2 | 补 `repair_rules/unique_filter.py` | [x] |
| 0.3 | 补 `repair_rules/nearest_match.py` | [x] |
| 0.4 | 求解器最小闭环产 `AllocationSolution` | [x] `run_selective_muas` |
| 0.5 | 端到端冒烟 | [x] `exp01_smoke` 3 周期无异常 |

**验收结果**(默认 `exp01_smoke/config.yaml`,8 巢 / 容量 4 / 24 机 / 3 周期):

```
周期 1  T_t=10(new 10)  K_avail=24  T_sel=10 → T_exec=7(保留 3)
周期 2  T_t=8 (remain 3/new 5)      T_sel=8  → T_exec=5(保留 3)
周期 3  T_t=3 (remain 3)            T_sel=3  → T_exec=1(保留 2)
汇总:完成 13/20(65%),平均等待 692s,总代价 331 km,求解 2.1 s
```

三个周期的 `check_feasible` **均无 violation、penalty = 0**。

### 0.3 两个实现决策(影响后续,务必知悉)

1. **参与规划的 UAV 数 K 恒取 `K < M`(任务数),走 srp 巡游模型。**
   DMDE 按 `n_uavs` 与 `n_targets` 大小自动选模型,其中 `N > M` 的
   **overloaded 模式允许同一任务分给多架 UAV**,与「一任务一机」冲突,
   本问题不可用。因此 K 取 `min(K_avail, M−1)`,其余可用 UAV 本周期空闲
   —— 正是论文允许的 `U_t^exec ⊆ U_t^avail`。
2. **航线与终点机巢必须联合收敛后再定稿。**
   只有真正有航次的 UAV 才会起飞让出原机巢名额。若先选终点再做剪枝 /
   航程修复,被清空的航次实际并未起飞,让出的名额是虚的 → 落地期超卖
   (实测在满泊位场景触发过 `nest 1 末态 6/4`)。当前实现为
   `prune → repair_range → choose_end_nests` 迭代至不动点。

---

## 2. 阶段 1:补齐 MUAS 约束(P1)

> 机巢容量约束的设计方案见 `docs/design_nest_state.md`,已按其实现。

| 约束(§4.3) | 现状 | 待做 |
|---|---|---|
| 每个任务至多一架 UAV | [x] `validate_unique_tasks` | - |
| 每架 UAV 可不执行任务 | [x] `allow_idle_uav` | - |
| UAV 航程约束 | [x] `check_feasible` + **`_repair_range` 主动修复** | - |
| 任务时间窗约束 | [x] `earliest` / `latest` 游标推进(起点为周期时刻) | - |
| 终点机巢可达性 | [x] `end_nest_id is None` / `nest.available` | - |
| **机巢容量或可用性** | [x] `constraints/nest_capacity.py` 两道校验 | - |
| **三维地理环境 / 可达性** | [ ] 仅有代价近似 | **2.2 增加可达性判定** |

| # | 任务 | 状态 |
|---|---|---|
| 2.1 | 机巢容量约束:按 `end_nest_id` 统计末态归属 ≤ `Nest.capacity`,超出进 penalty(按 `docs/design_nest_state.md`,含落地期硬校验) | [x] |
| 2.2 | 三维可达性:`vertical_section` 只做代价近似,需新增可达性判定(最小离地余量 / 禁飞区) | [ ] |
| 2.3 | `methods/muas/constraints/` 落位 | [~] 容量已拆出,其余仍在 `problem.py` |
| 2.4 | 惩罚归一化:`f2 = 路径代价 + w_penalty × penalty`,各项同量纲 | [~] 容量用 1e4/架,其余仍为 1e3 量级 |
| 2.5 | `UAVFleet.apply_solution` 补 `nest_id` 写点 + `reassign_nest` / `decommission` | [x] |
| **2.6** | **终点机巢进编码(§5(1)(2))**:基因用 `uav_id=-2` 的终止基因承载终点,`inverse_phi` 与 `repair_rules` 同步支持;终点由进化搜索决定而非事后启发式 | [x] |
| 2.7 | 修复结果回写种群(Lamarckian):把 `_prune` / `_repair_range` 后的解编码回 `cost_vector`,使选择压力作用于修复后的解(当前进化搜的是未修复空间) | [x] |

### 2.6 验收结果(终点机巢进编码)

**实现要点**

- 基因类型用 `uav_id` 哨兵值:`≥0` 起始 / `-1` 巡游 / **`-2` 终止(终点机巢)**。
  `Gene` 是 frozen dataclass,**不新增字段**。
- 代价矩阵列从 `M` 扩到 `M+B`:`[:K, M:]` = UAV→机巢(空航次用),
  `[K:, M:]` = 任务→机巢(终止基因在此匹配)。
- `cost_vector` 布局:`[0,K)` 起始 / `[K,K+M)` 巡游 / `[K+M,K+M+K)` 终止。
  终止分量参与 `mutate_population`,终点因此是**进化结果**。
- 终点**以编码为主、启发式为兜底**:`_choose_end_nests` 仅在编码终点
  违反容量时才改派(由 `check_nest_capacity` 判定)。

**验收数据**(默认 `exp01_smoke`,8 巢 / 容量 4 / 24 机 / 3 周期,seed=42)

| 项目 | 结果 |
|---|---|
| 段结构 + 终止基因合法性 | 300 次采样 **0 异常**(起始顺序恒为 `0..K−1`,每航次段末恰好 1 个终止基因,任务不丢不重) |
| 终点是否随进化变化 | 80 次采样得到 **43 种**不同终点组合;3 个 seed 得到 3 种不同终点 |
| 容量(满泊位 32 机 / 8 巢×4 + `strict_capacity`) | 全周期 `occ=4/4/4/4/4/4/4/4`,**无超容、无异常** |
| 可行性 | 三周期 `violations=[]`、`penalty=0` |
| **消融(同 seed)** | 终点进编码 **323.2 km** vs 启发式选巢 **331.0 km**,代价降低 **2.4%** |

**消融开关**:`config.yaml` 的 `muas.end_nest_in_encoding`(默认 `true`)。
关闭后退化为「解码后再用容量感知策略挑终点」,供 `exp04_ablation` 对比。

**附带修复(重要)**:`_perturb_srp_tour` 的 `reverse` / `insert` 会把
`uav_id >= 0` 的起始基因卷进子序列,实测出现过起始顺序 `[0, 2, 1]`。
已新增 `repair_rules/terminal_nest.py::normalize_srp_segments`,在扰动后
强制重建航次分段 —— 否则终止基因无法稳定落在段末。

**补充实现**:`_repair_range` —— 超航程的航次逐个移除「移除收益最大」的任务直到
落入剩余航程。只靠 penalty 兜底不够(DMDE 会在不可行个体上浪费代数),
与容量 repair 同理。

### 2.7 验收结果(Lamarckian 回写)

**问题根因**:`dmde_solver.solve` 的主循环在贪婪选择胜出后执行
`population[i] = child`,而 `child` 的基因是 `inverse_phi` 由**试验向量**译出的
**未修复**版本。`build_solution` 内的 `_prune` / `_repair_range` / `_choose_end_nests`
修复结果只用于算适应度,**未写回基因** —— 于是下一世代的差分变异围绕未修复空间展开,
修复所确定的「起始任务归属」与「终点机巢」(论文 §5(2) 创新点)没有进入后继的
选择压力。

**实现**(落点:`muas_stage.MUASAssignmentEvaluator.lamarckian_reencode` +
`dmde_solver` 主循环;`DMDEConfig.lamarckian` / `MUASStageConfig.lamarckian` 开关):

- SRP 基因契约强制「每 UAV 一段、长度恒为 `M+K`、每个任务都必须出现在基因中」,
  直接删除被 `y_j=0` 剔除的任务会破坏该不变式、使 DE 代价矩阵错位。
  故只回写**可解码**的两条通道:
  - **起始基因**(位置 `[0,K)`):改写为修复解中该 UAV 的首任务(代价/目标同时改),
    使下一轮 `inverse_phi` 的贪心起点精确复现修复解的归属;
  - **终止基因**(位置 `[K+M, K+M+K)`):改写为修复解的终点机巢,使终点机巢真正
    进入进化搜索压力。
- 巡游基因保留原样(其代价在解码时被贪心重建、不被读取),**长度严格保持 `M+K`**,
  零风险;回写后长度校验失败则静默回退到原 `child`。
- 仅当评估器提供 `lamarckian_reencode` 且本次 `solution` 非空时触发,异常安全。

**验收数据**(同 seed / 同场景,开关对照;`exp01_smoke`):

| 场景 | lamarckian | 总代价 F2 | 完成率 | 池内剩余 |
|---|---|---|---|---|
| 默认(10 任务 / 24 机) | on | 323.2 km | 65% | 2 |
| 默认(10 任务 / 24 机) | off | 323.2 km | 65% | 2 |
| 应力(30 任务 / 32 机,满泊位) | **on** | **519.5 km** | **85%** | **1** |
| 应力(30 任务 / 32 机,满泊位) | off | 531.8 km | 80% | 3 |

- 小规模下两者都收敛到同一最优,回写中性、无回归;
- 应力实例(剔除 / 航程修复 / 机巢容量修复压力更大)下,回写带来 **代价 -2.3%**、
  **完成率 +5pt**、滞留任务更少 —— 证明修复结果确实改变了后继搜索方向。
- 全程 `check_feasible` 无 violation、无超容,与 2.6 一致。

**消融开关**:`config.yaml` 的 `muas.lamarckian`(默认 `true`)。关闭即退化为
「围绕未修复空间搜索」,供 exp05 对比。

---

## 3. 阶段 2:动态滚动闭环(P1,论文 §6) —— 已完成

| # | 任务 | 状态 |
|---|---|---|
| 3.1 | `rolling/horizon.py`:单周期编排 `begin_cycle → selection → muas → apply_solution → 反馈` | [x] |
| 3.2 | 状态反馈串联:`fleet.mark_fault(uid)` → `pool.release(ids, reason="uav_fault")` | [x] 故障注入走 `HorizonConfig.faults` |
| 3.3 | `rolling/event_trigger.py`:事件识别(UAV 故障 / 紧急任务 / 机巢不可用)+ 本期重规划响应 | [x] 识别已实现,且已接入 `run_cycle` 触发本期重规划(强制纳入紧急/故障释放任务、剔除不可用机巢) |
| 3.4 | 周期参数:规划周期长度、执行推进步长、重规划触发阈值 | [x] 全部进 `config.yaml` 的 `rolling:` |
| 3.5 | 执行推进:按 `UAVTour` 推进位置与剩余航程 | [x] 含机巢换电回满 |

**验收结果**

- 多周期滚动跑通,8 周期场景完成率 84%–90%,`n_remain` 随周期回落至 0。
- 未纳入本周期的任务以 `REMAIN` 身份在下一周期重新参与分配(实测可见
  `T_t` 中 remain 项在 0→4 之间波动并被逐步消化)。
- `U_t^exec ⊆ U_t^avail`、`T_t^exec ⊆ T_t^sel` 两个包含关系始终成立。
- 满泊位(32 机 / 8 巢 × 容量 4)+ `strict_capacity=true` 下无 `CapacityViolation`。

### 3.3 事件驱动「本期重规划」(审查闭环)

审查发现 `should_replan` 为死代码:事件只被 `detect_events` 识别并记入
`record.events`,但 `run_cycle` 从未据此改变计划(论文 §6 写法会被审稿人证伪)。
现已在 `run_cycle` 接入并真正驱动反应式调整:

**机制**(落点:`horizon.run_cycle` + `event_trigger.summarize_events` /
`should_replan` + `selection.pipeline` 的 `forced_task_ids` + `muas_stage` 的
`excluded_nest_ids`)

- 周期内事件检测后,`summarize_events` 把事件解析为上下文
  (`urgent_task_ids` / `unavailable_nest_ids` / `faulted_uav_ids`),
  由 `should_replan`(受 `EventTriggerConfig.enabled` 与
  `HorizonConfig.event_replan` 双重开关)决定本期是否触发重规划。
- **UAV 故障 / 紧急任务** → 对应任务(含上一周期因故障释放回池的任务)经
  `SelectionConfig.forced_task_ids` **强制纳入**第一阶段选择,绕过 Top-αK 预筛
  (仍过可行性过滤)。
- **机巢不可用** → 该机巢 id 经 `MUASStageConfig.excluded_nest_ids` 传入
  `_choose_end_nests` / `choose_end_nests`,从终点候选集剔除,其归属 UAV
  强制改降其它巢(多机巢异巢终止的容错分支)。
- 每周期在 `CycleRecord` 记录 `replan` 与 `replan_context`,并在
  `selection` / `muas` 诊断中将 `forced_task_ids` / `excluded_nest_ids` 一并落盘。

**开关**:`HorizonConfig.event_replan`(默认 `true`,可由实验 yaml 覆盖)与
`EventTriggerConfig.enabled`(默认 `true`)共同控制;`event_trigger` 模块仍只做
"识别",状态改变由 `horizon` 显式执行(两相状态机互不 import 的边界不变)。

**验收**:`tests/test_event_replan.py` 五项断言全过 ——
(1) `summarize_events`/`should_replan` 解析正确;(2) 低收益任务被 Top-αK 排除、
强制纳入后进入 `T_t^sel`;(3) 紧急任务(低收益、靠 slack 触发)本期触发重规划并被
强制纳入;(4) `event_replan=False` 时事件仅记录、不触发;(5) 机巢不可用触发重规划,
且运行后无 UAV 仍归属该巢(`occ_after=0`)。`exp01_smoke` 端到端无回归(323.2 km)。

### 3.6 反饥饿修正

偏远任务会因「执行代价 > 收益」被连续剔除而**永久滞留**。已按论文 §3.2 的等待
补偿 `W_i(t)` 处理:剔除判据用含等待补偿的有效收益

```
λ · saving  >  R_j · (1 + prune_wait_bonus · (1 − e^(−waited / wait_scale))) · (1 + margin)
```

滞留越久越难被剔除。实测:未加补偿时周期 3 执行 0 个滞留任务,加补偿后执行 1 个,
8 周期长跑下滞留任务被完全消化。

---

## 4. 阶段 3:动态任务到达过程(P2)

| # | 任务 | 状态 |
|---|---|---|
| 4.1 | 任务到达模型:泊松过程 / 分批到达 / 紧急插单 | [~] 仅「分批到达」(`build_task_batches`),泊松待补 |
| 4.2 | 任务点数据:`data/task_location_data.csv` | [ ] 当前为**合成**(`common/task_scenario.py`),CSV 链路待补 |
| 4.3 | 时间窗生成:按任务类型给 `earliest` / `latest` | [x] 均匀区间,按类型区分待补 |
| 4.4 | 任务收益 `reward` 设定:与输电巡检业务对齐 | [~] 均匀区间,杆塔/缺陷等级待建模 |

---

## 5. 阶段 4:对照方法(P2)

| # | 任务 | 状态 |
|---|---|---|
| 5.1 | `baselines/greedy.py`:按代价/优先级贪心匹配,继承 `BaseOptimizer`,`name="greedy"` | [ ] |
| 5.2 | `baselines/random_assign.py`:随机可行分配,`name="random"` | [ ] |
| 5.3 | `baselines/registry.py`:注册进 `REGISTRY`(当前为空 dict) | [ ] |
| 5.4 | 统一入口:所有求解器共用同一 `CostProvider`,保证可比 | [ ] |

---

## 6. 阶段 5:实验体系(P2-P3)

按 `experiments/README.md` 约定,一实验一目录。

| # | 实验 | 验证内容 | 依赖 | 状态 |
|---|---|---|---|---|
| 6.0 | `exp01_smoke` | 端到端链路 + 3 周期滚动 | 阶段 0/2 | [x] |
| 6.1 | `exp02_selection_scale` | 第一阶段规模压缩:\|T_t\| → \|T_t^sel\| 压缩率、选择耗时、α 敏感度 | 阶段 3 | [ ] |
| 6.2 | `exp03_rolling_dynamic` | 滚动闭环:完成率、平均等待、`K_avail(t)` 利用率、故障恢复 | 阶段 2 | [ ] |
| 6.3 | `exp04_ablation` | 消融:去掉 W / G / ΔC / 3D 地形代价 | 阶段 1 | [ ] |
| 6.4 | `exp05_solver_compare` | 对比:DMDE vs greedy vs random | 阶段 4 | [ ] |
| 6.5 | 指标规范化 | `metrics.json` 字段:`n_completed`、`completion_rate`、`avg_wait`、`total_cost`、`solve_time_ms`、`K_avail_mean` | - | [x] exp01 已产出 |

**规模提醒**:`build_pairwise_costs` 实测约 **260 μs/对**,O(n²)。任务数 100 → 约 2.7 s,
300 → 约 24 s。设计 exp02 规模上限时先算这笔账;若要上更大规模,优先做代价矩阵
缓存/增量更新,**不要**先去动坐标转换(那部分只占 234 ms 一次性)。

另外 DEM 重采样(`cost.dem_step_m`)也是一次性成本,当前 200 m 网格约 35 万点,
实测秒级;步长调到 100 m 会翻 4 倍,注意权衡。

---

## 7. 阶段 6:可视化与论文产物(P3)

| # | 任务 | 状态 |
|---|---|---|
| 7.1 | 任务分配图:机巢 / UAV 航次 / 任务点在局部平面上的分布(`plot/`) | [ ] |
| 7.2 | 收敛曲线:DMDE 代数 vs 目标值(`cost_history`) | [ ] 数据已产出(`cycles[].muas`),未绘图 |
| 7.3 | 滚动时间线:`T_t` 各来源规模、`K_avail(t)` 随 t 变化 | [ ] 数据在 `metrics.json`,未绘图 |
| 7.4 | 三维代价场:垂直切面剖面 + 地形叠加 | [ ] |

---

## 8. 建议推进顺序

```
阶段 0(打通)✅ ──┬─→ 阶段 1 剩余(2.2 三维可达性、2.3/2.4 约束落位)
                  │
                  └─→ 阶段 2 剩余(3.3 局部重规划)─→ 阶段 3(到达模型/真实任务数据)
                                                        │
                              阶段 4(对照方法)──────┴─→ 阶段 5 exp02/03/04/05
                                                        └─→ 阶段 6 可视化
```

**当前关键路径**:阶段 4(baselines)→ exp05 对比实验。
没有对照方法,论文 §5 的「改进 DMDE」无法给出相对基线的提升幅度。

**但优先级高于 baselines 的是 2.6(终点机巢进编码)**——它是论文创新点第 3 条
「终点可选的统一编码」的自证前提。编码不改,exp05 只能证明"DMDE + 事后补丁"
优于 greedy,无法支撑"统一编码扩展"这个宣称。

文档 §1 第 3 条另给了对照思路:**联合优化(精确解)仅用于小规模验证或离线
性能上界对比**。即 exp05 除 greedy / random 外,可加一个小规模 Gurobi/穷举上界,
说明"两阶段分层解与联合最优的 gap"。

---

## 9. 风险与提示

1. **`repair_rules` 仍是质量瓶颈**。三个小文件决定连续个体如何稳定映射到可行离散解。
   建议补单测,尤其是 `overloaded`(N>M)与 `balanced`(N=M)的差异
   —— 虽然本问题走 srp,但换规模时可能落到别的分支。
2. **三维可达性工作量不确定**。建议先做「离地余量 + 禁飞区掩膜」的轻量版,
   完整航迹搜索留给第 4 章。
3. **O(n²) 代价矩阵**是实验规模天花板(见 §6)。
4. **`exp01_smoke` 的 `problem.n_uavs`**:设为 `n_nests × nest_capacity` 即满泊位,
   会触发容量约束;设小一些则容量不紧张。做容量灵敏度时改这一个字段即可。
5. **环境**:`pyproject.toml` 已补 `numpy` / `pillow`(DEM 栅格解码惰性依赖),
   用 `uv sync` 建 `.venv` 后运行;系统 Python 未装这些包。
