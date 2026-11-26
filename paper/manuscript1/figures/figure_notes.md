# 图注与说明登记（paper/manuscript1/figures）

> 规则：本目录每张论文图在此登记一行；标题与图注供正文 / LaTeX caption 直接取用；图面放不下的口径细节写进"澄清"；有分歧的写进"待澄清"。

## 文件登记

| 文件 | 内容 | 状态 |
| --- | --- | --- |
| `framework_overview_zh.svg` | 分层调度总体框架（中文版） | v4 定稿候选 |
| `framework_overview_en.svg` | 分层调度总体框架（英文版，与中文版同几何） | v4 定稿候选 |

## framework_overview（总体框架图）

### 建议标题

- 中文：图 X 基于滚动时域的分层任务分配与航迹规划总体框架
- 英文：Fig. X Overview of the rolling-horizon hierarchical framework for task allocation and trajectory planning

### 图注（中文，可直接入正文）

> 系统在滚动时域框架下按周期 t 组织调度。每个周期内，任务分配层先对动态任务池 𝒯ₜ 中的候选任务全池排序，经规模闸门 nₛₑₗ = min{cap(t), n_compute} 截断后，由阶段二完成机队指派与航次排序（MUAS），输出访问序 π、终点机巢 z 且出发时刻 t₀ 冻结的航次计划；航迹规划层据此分两步求解：单机层在走廊约束下对 (x, v, a, Δt) 做时空耦合联合优化，多机协调层保持时空解耦，仅通过局部几何让路消解冲突，几何改动后回单机层重解时间。航迹经全域净空复核（交付判据）后交付执行，周期结算完成机时记账、超期判定与航迹清理。三类快动态事件（无人机故障、突发高优先级任务、机巢不可用）以边沿检测方式对齐周期边界注入本周期决策入口，实现"周期管慢、事件管快、事件借周期落地"；下层报出的**几何不可行航段**在本周期内以极大代价写回并重解一次，**未交付任务回池**在下一周期补飞，两者共同构成闭环。

### 图注（英文）

> The system is organized over rolling-horizon cycles t. Within each cycle, the task-allocation layer ranks the candidate tasks of the dynamic pool 𝒯ₜ, truncates them through the scale gate nₛₑₗ = min{cap(t), n_compute}, and completes fleet assignment and tour planning (MUAS), yielding a tour plan with visit order π, end nest z and frozen departure time t₀. The trajectory-planning layer then proceeds in two steps: the single-UAV layer performs spatio-temporally coupled joint optimization of (x, v, a, Δt) under corridor constraints, while the multi-UAV coordination layer remains spatio-temporally decoupled and resolves conflicts via local geometric detours, after which time is re-solved on the single-UAV layer. Trajectories are delivered upon full-domain clearance verification, and cycle settlement performs occupancy accounting, overrun detection and trajectory pruning. Three classes of fast dynamics (UAV faults, sudden urgent tasks, nest unavailability) are edge-detected at cycle boundaries and injected into the current cycle's decision entry, so that the cycle governs slow dynamics while events govern fast ones on a unified channel. Geometrically infeasible legs reported by the trajectory layer are removed once within the same cycle and the plan is re-solved, while undelivered tasks are re-pooled and re-flown in the next cycle; together they close the loop.

### 澄清（图面放不下、正文可引用的口径）

| 术语 | 口径 |
| --- | --- |
| 动态任务池 | 三来源汇入：计划到达 ∪ 突发高优 ∪ 故障释放；图示候选 / 滞留（年龄补偿与防饥饿）/ 出池（几何永久不可达）三状态 |
| 事件注入 | 边沿检测、对齐周期边界、借周期决策入口落地；不存在独立于周期通道的异步重派 |
| n_compute | 单周期航迹规划求解规模上限，实验取 32 |
| 交付判据 | 全域净空复核：段内可行 ≠ 整条可交付 |
| 代价交互（TA↔PP） | 层间双向：下行航次计划携带估计代价 λ·Cₚₐₜₕ；上行**只回灌事实数值**——已飞航段按实测长度、同一权重口径重定该边代价 \(c_{ij}=\ell^{real}_{ij}w_j\)；几何不可飞边（白名单几何失败码且 severity=infeasible：走廊过窄/出域/无路径/净空违约/端点出带）写成极大值 \(10^7\) m，由任务分配内部已有的航程检查判死（不另设可行性闸门）。跨周期不写、不缓存、不做倍率校准——实测/估计比的可迁移性依赖"同一批 OD 反复飞"，任务点逐周期变化时假设不成立 |
| 闭环反馈 | ①硬不可行剔除：TA↔PP 层间上行红虚线（唯一触发=首版解含几何不可行边；同一冻结快照至多重解一次 max_in_cycle=1，不推进时间、不写任务池/机队，"剔除动作，不是轮询"）；②失败回池补飞：右侧红虚线（重解后仍未消掉的不可飞边如实记 unresolved_infeasible_edges，与未交付任务一并经 reopen 以 RELEASE 来源重投候选；可证伪指标 infeasible_but_survived） |
| 整航次预算违约 | RANGE/ENDURANCE_EXCEEDED 刻意不写不可飞：整航次预算判定换个任务组合可能成立，写死会误杀可行 OD |
| 机队状态 | Uₜ（位置 · 剩余航程 · 异构性能）为顶部输入行独立框，与任务池并列输入任务分配层 |
| 休眠状态 | 代码口径（horizon.py `_update_service_stall` / `_wake_dormant`）：任务几何可达（最强机往返可及）但连续 service_patience=4 个周期无可用机在往返航程内 → 转休眠（不出池、不进候选、不入完成率分母），条件恢复即唤醒，年龄累计不中断；与静态几何不可达（UNREACHABLE 一次性出池）严格区分——前者是时变条件，后者是永久判据 |
| 顺序组原子性 | 带 sequence_group 的链是不可拆调度单元：组员不齐整组暂缓（准入闸门）、解只装载半组整组撤下（交付闸门）、组内一员物理违约整组连坐暂缓（完成闸门，连坐者不计废飞）；图面未画，正文如需可引用 |
| 收敛判据 | 主判据：池空 = 自然收敛（不存在"能被完成却没被完成"的任务）；stable_patience=4 仅作"无休眠任务的僵死池"零进展兜底（非自然收敛，单独记 stop_reason=no_progress）；n_cycles 为硬安全上限；停机口径图面未画 |
| 事件反应动作 | 三类事件的"反应式调整"具体落点：紧急/故障释放任务 → 强制纳入阶段一候选（绕过 Top-αK 预筛）；不可用机巢 → 剔出阶段二终点候选集；闭塞机巢归属机 → 改降最近可用机巢。均在周期内借正常管线落地，不触发额外重解 |
| 完成闸门 | 周期末仅物理不可行（航程/续航/终点机巢/结构类）弹回回池（计废飞）；迟到不是废飞——晚到正常完成、按 late_reward_decay 在适应度中折减，单列迟到统计 |

### 待澄清（需用户拍板）

1. 回路②失败回池：图中箭头指向任务分配层（阶段二右缘）示意"下一周期上层决策生效"，未直接画回任务池——是否需要明确画回池箭头？
2. 英文图注中 "edge-detected at cycle boundaries" 的表述是否与正文 Chapter 3 用词一致？
3. 图号 X 待正文定稿后回填。
