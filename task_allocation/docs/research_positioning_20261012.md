# 研究定位分析：背景、目的、进展与现状、空白与创新性

> 日期：2026-10-12 ｜ 性质：论文引言/相关工作写作素材 + 研究定位自查
> 事实口径：本文件分两类陈述——**【文献】**（来自检索，正式引用前需读全文核对）与**【本项目】**（以代码与实验数据为唯一事实源，详见 `docs/full_review_20261012.md`）。
> 检索范围说明：本轮为定向检索（非系统性文献综述），结论"未发现同时满足……的工作"指**本轮检索范围内**，正式写入论文前需在 Web of Science / Scopus 做系统检索确认。

---

## 一、研究背景

### 1.1 应用背景：电力巡检正从"单机单线"走向"多机巢集群常态运维"

- 输配电无人机巡检已规模落地：大疆 M300 RTK + L1 激光雷达 5 分钟完成千米级线路通道巡检（陕西送变电案例）；EVNNPT 基于 3D LiDAR 自动航线覆盖 4,670 km 线路（占其所辖 220–500 kV 线路的 97%）。
- **机巢（自动机场）成为新的基础设施形态**：国网广德"集群式机巢"三角布局覆盖示范区，实现任务一键下发、按覆盖范围自动分配，效率提升 80%，并明确规划"多机巢协同作业（甲巢任务乙巢接续）"；山西晋城部署 28 套"驻塔式机巢"；广州规划 150 座机巢全域覆盖。
- 产业侧的技术诉求已从"单机航线生成"上移到**机队级任务调度**：多机巢任务动态分配、机巢间接续巡检、突发隐患集中排查。

**【本项目】** 场景落点即为此形态：西藏 N29E091 瓦片（约 97×111 km）、8 个机巢、多架固定翼/多旋翼混合的常态化巡检机队。

### 1.2 环境背景：高原复杂地形使"平面欧氏距离"假设失效

- 青藏高原、云贵山区等地形高差大、峡谷密集，二维平面距离与真实航程差异巨大；相关研究已普遍采用 DEM/DSM + 激光点云构建三维可飞空间（高原森林区域体空间专利 CN121655537B；ASTER GDEM 山地覆盖规划 LF-TF-CPO 2025）。
- 电网巡检对此更敏感：不同电压等级需不同飞行层与安全余量（CN121185319A），三维障碍（杆塔、导线、植被、山体）使二维规划"难以避免空间冲突"。

### 1.3 任务背景：巡检任务是持续到达、允许暂缓的动态流

- 缺陷复查、应急排查、通道隐患等任务按需插入；机巢资源（泊位、电量、换电周期）有限；机巢本身可能因天气/维护不可用。
- 现实约束决定了**并非所有任务都能/都值得在本周期执行**——任务选择（暂缓或放弃）本身是决策变量。

### 1.4 理论背景：四条研究线各自成熟，交叉处空白

```
多无人机任务分配（MUAS/MTSP）────┬── 多机巢 MTSP（起降巢可不同）
选择性任务执行（OP/TOP）─────────┤── 动态/滚动时域（任务到达、重规划）
三维地形代价（DEM→代价场）───────┘── 离散映射差分进化（DMDE 算法母本）
```
四条线均有大量文献，但**同时满足"选择性 × 多机巢 × 三维地形 × 滚动时域"的工作，本轮检索未发现**（详见 §四）。

---

## 二、研究目的

**【本项目】** 要解决的问题可一句话界定：

> 在任务持续动态到达、机队部分可用、机巢异源起降（终点机巢可选）、真实三维地形代价约束下，如何滚动地为多无人机机队生成"执行哪些任务、由谁执行、按何顺序、落哪个巢"的协同方案，并平衡**完成率**与**总航程**。

具体研究目的（与代码落点一一对应）：

| 目的 | 内涵 | 代码/文档位置 |
|---|---|---|
| P1 问题形式化 | 把 MUAS 沿"动态/选择性/多机巢/三维代价"四维扩展为可计算的规划问题 | `methods/muas/problem.py`、`docs/guides/1整体方法.md` |
| P2 分层求解框架 | 阶段一动态任务选择（评分+贪心筛选）+ 阶段二选择性多机巢 MUAS（DMDE）+ 外层滚动闭环 | `methods/selection/`、`methods/muas/solvers/muas_stage.py`、`methods/rolling/horizon.py` |
| P3 编码与算子 | 统一两层编码承载"UAV 归属+访问顺序"，终点机巢分层精确求解；算子逐条对齐 Ming 2017 | `methods/muas/representation/`、`operators/` |
| P4 真实数据验证 | 真实 ASTER DEM 三维代价场 + 8 机巢坐标 + 滚动多周期跑通 | `experiments/exp01_smoke`、`exp02_selection_scale` |
| P5 可信度建设 | 137 项测试、5 seed 可复现、集合关系不变量守护 | `tests/` |

---

## 三、研究进展与现状（分线综述）

### 3.1 多无人机任务分配：综述层面已承认"复杂耦合约束 + 大规模 + 鲁棒性"未解

**【文献】**
- 毕文豪等《无人机集群任务分配技术研究综述》（系统工程与电子技术，2024）：梳理 TSP/VRP/NFO/CMTAP/MILP/MDP 建模范式；指出启发式（GA/PSO/GWO/ACO）"大部分针对特定应用背景改进，无法全面考虑复杂耦合约束，鲁棒性与可扩展性较低"；市场机制类（CBBA、拍卖）实时性好但通信开销大。
- Ghauri et al.（JSAN, 2024）SAR 场景综述、DOAJ《Multi-UAV Task Assignment in Dynamic Environments》（2013–2024 横评）：三大类方法（market-based / 智能优化 / 聚类）各有短板；明确指出研究缺口为**可扩展算法、对突发任务/故障的鲁棒性、以及平衡能耗与完成率的多目标框架**——与本项目的问题设定高度呼应。
- MDPI Drones 2024（ASTRRA）：把"任务路径决策变量 + 协同行动约束"引入拍卖框架，承认传统启发式慢、拍卖算法不保证最优。

### 3.2 多机巢 MTSP / 无人机路由：终点可选的建模与两阶段范式是主流

**【文献】**
- 经典异构多机巢 MTSP：Oberlin, Rathinam & Darbha（IEEE RAM, 2010）把 HMDMTSP 变换为单一 ATSP 后用 LKH 求解；Sundar & Rathinam（ICUAS 2015）给出 MILP + branch-and-cut 精确算法（100 目标/5 机 300 s 内）。
- **与"终点机巢自由"最接近的一篇**：Li et al.（IEEE Access, 2021）MMDUAVRP——"不限制 UAV 起降机巢"，目标为最小化 UAV 数与总航程，用启发式分配 + 混合大邻域搜索（HLNS）求解，与 CPLEX 及知名算法对比更优。**注意：其终点选择是搜索过程中的启发式行为，未做"路线—终点"的分层精确子求解。**
- Nature Sci. Rep.（2026）MDCP-MTSP：总结"k-means 聚类 → 单机 TSP"两阶段分治范式为领域主流，同时批评"解质量受第二阶段元启发式能力制约"——这正是本项目"阶段一筛选 + 阶段二进化 + 内层精确兜底"分层路线的论据空间。
- 综述性：Coutinho et al.（CIE, 2018）taxonomic review；Macrina et al.（TR-C, 2020）；Khoufi et al.（Drones, 2019）；Poikonen & Campbell（Networks, 2021）future directions。
- 无人机辅助配送侧：Murray & Chu（2015）flying sidekick TSP 及其多扩展（路径依赖、多卡车）——说明"载具与基地耦合"问题族庞大，巡检侧文献相对稀薄。

### 3.3 选择性任务执行：OP/TOP 线成熟，但多在单层/单基地/确定性设定

**【文献】**
- OP 综述：Vansteenwegen & Souffriau（EJOR, 2011）；TOP 及其时间窗扩展（OPTW）自 Kantor & Rosenwein（1992）起。
- **与"选择性+动态"最接近的一篇**：Evers, Barros, Monsuur & Wagelmans（EJOR, 2014）——把 OP 同时扩展为：旅行/记录时间不确定、时间窗、**飞行中到达的时敏目标**，用 MCS-OPTW 在每个目标离开前重规划。**单 UAV、单基地、无三维地形、无进化编码。**
- **与"选择性+多机巢"最接近的一篇**：Xia et al.（TR-B, 2019）把多机巢多航次无人机船只监测调度表述为"广义 team orienteering problem"，网络流 + Lagrangian 松弛求解（300 时点/100 船）。**无三维地形、无滚动编码、非元启发式框架。**
- 其他：Prize-Collecting Multiagent Orienteering（IEEE T-ASE, 2022，通信拒止下的自利智能体，PoA 界）；Sadov & Sharp（JAMIS, 2025）TOP-PDLT 时变奖励 UAS 航线。

### 3.4 动态/滚动时域：MILP+RHTA 与事件触发重规划两条路

**【文献】**
- Song, Kim & Morrison（JINT, 2016）：持续服务场景 MILP 滚动规划，RHTA/STAH 启发式分别比 CPLEX 快 45/2100 倍——**"滚动时域 + 精确模型 + 快速启发式"组合的经典证据**。
- Kiema et al.（2025）：大规模交通监视的分层架构（空间聚类 + 各子区 RHTA）；MDPI Drones 2026（RITRA）：贝叶斯信念跟踪 + Shapley 关键性的滚动分配，含 200 次 Monte Carlo 压力测试与 CBBA 基线对比——**基线建设做得规范的范本**。
- 电力巡检侧的滚动实践：中文期刊（2026，低空经济）时变图 + 滚动时窗自适应 GA（机队总历时 8.5 h→6.6 h）；专利 CN117993672A"多机电力巡检滚动调度"（聚类-优化流程、精英保留 GA、**含返回机巢选择步骤**）。

### 3.5 三维地形代价：路径规划层成熟，正开始向任务分配层渗透

**【文献】**
- **与本项目代价构造最同源的一篇**：IEEE（2023，WAFC-RRAS 混合算法）提出"垂直切面法"（vertical tangent method）近似飞行代价：DEM 描述地貌 → 取 UAV-任务点连线垂直截面高程 → 坐标变换后累加离散距离，并对山体穿越段赋高权重 θ₁。**本项目 `cost/vertical_section.py` 的构造思路与此一致（垂直切面 + 离地余量 + 穿越惩罚）。**
- 任务分配层引入地形感知的尝试：TA-MUCSTA（IEEE, 2025）加权体素地形地图 + 可见性分析 + MILP 区域分配 + Dubins-ACO 局部规划 + Contract-Net 动态联盟（冗余覆盖 −23.6%）；ES-EEA*（IEEE, 2026）DEM 地形图上能量-安全感知巡检（A* 基，N=30 实例）。
- 工程化：高原森林区域 DEM+DSM+点云体空间、垂直安全余量、地形感知启发式（专利 CN121655537B）；3D 输电线路巡检航迹（ACTA Press）；Voronoi-A* DEM 分层（MDPI Drones 2025）。
- **共性：三维地形几乎只以"路径规划的约束/代价"身份出现，很少作为任务分配层 cost matrix 的系统性输入。**

### 3.6 求解算法：离散映射 DE 是本项目的直接母本

**【文献】**
- **母本**：Zhao Ming, Zhao Lingling, Su Xiaohong, Ma Peijun, Zhang Yanhang，"Improved discrete mapping differential evolution for multi-unmanned aerial vehicles cooperative multi-targets assignment under unified model"，*Int. J. Mach. Learn. & Cybern.*, 8(3): 765–780, 2017（DOI: 10.1007/s13042-015-0364-3，哈工大）。核心：统一基因编码 + 飞行代价代偿连续化 + 逆映射回离散 + 动态 CR + 混合进化策略。同团队 2012 年在自动化学报提出统一建模方法。
- 衍生：CN114399161A（离散映射 DE 多 UAV 协同分配专利）；西电（2023）多目标分解 DE + 精英初始化 + 禁忌搜索；海军航空大学（2023）区间数不确定距离 + 多变异策略 DE；IEEE（2024）禁飞区 DE。
- 另一大主流：CBBA/拍卖族（Choi 等原始工作 + 前述 ASTRRA、CN122175310A 集群巡检 CBBA 专利）。

**【本项目】** DMDE 的五个结构扩展（哨兵基因统一编码、终点巢处理、选择性剔除、解码期约束修复不动点、Lamarckian 回写）即以此为基线；缩放因子 F 与灭绝率 GMR 两处已修正母本偏差（有 fidelity 测试守护）。

### 3.7 应用侧：多机巢集群巡检的工程实践远快于学术发表

**【文献】** 国网/南网系案例（见 §1.1）+ 专利群（CN121455215B 无公网区多机巢协同飞巡：主控巢+备用中继巢层次架构；CN120747799B 输变配一体化：NSGA-II + 滚动时域 15 min 重规划；CN122175310A CBBA 集群巡检自主分配）。
**特征**：工程方案普遍采用"聚类分区 + 每机独立优化 + 简单优先规则"，**缺少统一的问题模型、编码与可复现实验口径**——学术侧的贡献空间正在于此。

---

## 四、研究空白（Gap 分析）

> 每条空白标注：**支撑证据**（本轮检索的最近工作）+ **本项目的对应现状**（事实口径，可能也是短板）。

### G1 四维扩展的交叉空白（本研究的核心立论空间）
- 支撑：选择性+动态（Evers 2014，单机单巢）；选择性+多机巢（Xia 2019，无地形无滚动）；地形+分配（WAFC-RRAS 2023 / TA-MUCSTA 2025，无选择性无滚动终点巢）；滚动+多巢（CN117993672A 专利，无三维地形无统一编码）。
- **本轮检索未发现同时满足"任务动态到达 + 允许不执行 + 多机巢异源起降 + 三维 DEM 代价 + 滚动重规划"的工作。**
- 本项目对应：问题定义层四扩展齐全且均有代码落点。**空白成立，但需系统检索背书。**

### G2 终点机巢（异源起飞—异巢终止）缺乏与路线的联合/分层精确处理
- 支撑：MMDUAVRP（Li 2021）终点自由但用启发式；多数 MTSP 文献固定"返回起飞机巢"。
- 本项目对应：方案 A 把终点巢剥离为解码后最小费用流精确子问题（返航代价进 F2 完整评价）；方案 B（终点进编码）已实现但**默认关闭、推迟至 pro 分支**，其 −2.4% 消融数据当前主线不复核。
- **空白成立；但"分层 vs 联合孰优"本项目尚无定论，论文只能宣称分层口径。**

### G3 选择性（y_j=0）在优化框架中的位置缺乏统一认识
- 支撑：OP/TOP 线普遍把选择性内建于搜索空间（prize-collecting）；任务分配线则常假设"任务集合固定、全部执行"（毕文豪 2024 综述所列模型的共同前提）。
- 本项目对应：选择性在**解码端**由 `_prune` + `_repair_range` 迭代至不动点裁量（判据 λ·saving > R_eff·(1+margin)），**不在 DE 搜索空间内**——这是一个有辨识度的设计选择，但也意味着"搜索不为选择性服务"，需在论文中明确定位（条件最优/后裁量），否则易被审稿人质疑。
- **空白部分成立；本项目自身逻辑需自洽（见 G5/G6）。**

### G4 三维代价进入任务分配 cost matrix 的系统性不足；ETA 口径易分裂
- 支撑：三维地形文献集中于 path planning 层；任务分配文献 overwhelmingly 用欧氏距离（如 CN122175310A、CN120747799B、多数 CBBA/DE 工作）。
- 本项目对应：代价矩阵 `(K+M, M+B)` 已用垂直切面 3D 代价装配；**但审查发现评价端时间窗惩罚与 F3 瓶颈时间仍用欧氏距离算 ETA（P1 问题，系统性乐观）**，预筛端已三维、评价端未三维，口径分裂。
- **空白成立；本项目自身存在同款病症，须先修。**

### G5 基线对照缺失是领域共性，也是本项目当前最大硬伤
- 支撑：RITRA（2026）以 CBBA 为基线 + 200 次 Monte Carlo；MMDUAVRP 对比 CPLEX 与知名算法；MDCP-MTSP（2026）对比 GA/ACS/PSO。综述（DOAJ 2013–2024）把"缺乏统一评测"列为缺口。
- 本项目对应：`baselines/` 三个文件全为注释占位，`registry.py` 空 dict——**"优于谁"当前完全不可回答**。
- **空白对我们是双刃：既是贡献空间，也是必须补齐的证据义务。**

### G6 约束"惰性"问题：文献常假设理想化资源，实验难以暴露约束
- 支撑：多数 UAV 分配实验只报告距离/时间/收益，航程违约、机巢泊位约束很少被真正激活。
- 本项目对应：exp01（10 机/~10 任务）中 `cap(t)`、`postcheck`、Top-αK 均为死代码，航程违约恒 0，淘汰原因 100% 是 miss_time_window；地形只当代数不产生不可达。**"约束真实生效"的证据链尚未建立。**
- **空白成立；这既是领域共性短板，也是我们实验设计要突破的点。**

### G7 压缩规模是否伤害质量：本项目已发现反证（自我修正点）
- 支撑：MDCP-MTSP 两阶段范式被批评"受第二阶段元启发式能力制约"；任务选择线（Evers 2014）用覆盖率/期望收益折中而非宣称无损。
- 本项目对应：exp02（8 机、|T|≫K）分层 F1≈130~146 vs 全量直接求解 F1=314，**收益损失约 58%**——"压缩规模不伤质量"目前有反证；根因是 Top-αK 按**机数**标定，与巡游模型**吞吐能力**不匹配。
- **这不是文献空白，而是本项目必须修正的立论前提：第 3 章"筛选不伤质量"的叙事需重写为"吞吐标定后的受控折中"，并补目标值差距列。**

---

## 五、创新性分析

> 分四层，每层区分「**可主张**」（有代码/数据支撑）与「**待证明**」（当前无证据或口径未统一）。

### 5.1 问题定义层——**可主张**
- 将 MUAS 同时沿动态（任务到达/滞留/释放 + 滚动时域）、选择性（y_j=0 合法）、多机巢（异源起降、终点巢为决策变量）、三维地理约束（ASTER DEM 代价场）四维扩展，并落到**真实场景数据**（西藏瓦片、8 机巢）。四条线在文献中均有单/双维组合，四维合一的系统性问题定义在本轮检索范围内未见。
- 边界：宣称的是"问题定义与框架整合"，不是"每个维度都新"。

### 5.2 模型层——**可主张**
- 选择性多机巢 MUAS 的整数规划式表述：四类决策（y_j / x_uj / π_u / z_ub）+ 双目标（max 完成率、min 三维航程与惩罚）+ 可选瓶颈机时间项 + 11 个约束开关、9 个权重可配置。
- 分层定位清晰：DMDE 搜"UAV 归属 + 访问顺序"，终点巢由内层最小费用流（自实现 Bellman-Ford，无 scipy，经暴力对照验证最优性）在解码后精确求解——把"路线—终点"耦合拆成可证明最优的子结构。
- 边界：不等于全局联合最优，论文须以"给定路线下条件最优"口径表述（文档已统一）。

### 5.3 方法层——**可主张（增量明确）**，但"优于谁"**不可主张**
- 相对母本 Zhao Ming 2017 的可验证增量：① 哨兵基因统一编码（≥0 起始 / −1 巡游 / −2 终止）与固定布局代价值向量解耦；② 选择性剔除 + 解码期约束修复（prune → repair_range → choose_end_nests 迭代至不动点）；③ Lamarckian 回写只回写 start/terminal 两通道、绝不增删基因（长度恒 M+K 的不变式有测试守护）；④ 算子逐条对齐 Ming 2017 并修正 F 与 GMR 两处偏差（fidelity 测试）。
- 消融有正向证据：Lamarckian 在应力场景 −2.3% 代价 / +5pt 完成率。
- **边界（必须写进论文局限或补实验）**：无 baseline ⇒ "优于谁"不可宣称；终点进编码 −2.4% 系方案 B（默认关闭）下测得，不作为主线证据；Top-αK 压缩质量损失（G7）未解决前，"压缩不伤质量"不可宣称。

### 5.4 应用/数据层——**可主张**
- 端到端真实数据闭环：ASTER GDEM（N29E091）→ 三维代价矩阵 → 两阶段 → 滚动多周期 → 11 张图；5 seed 可复现；137 项测试通过；集合链包含关系 0 违反；满泊位无超容。
- 这是多数任务分配论文（普遍用合成随机数据、欧氏距离）不具备的。

### 5.5 创新性总评
| 维度 | 强度 | 说明 |
|---|---|---|
| 问题整合 | ★★★★ | 四维交叉空白，本轮检索范围内成立 |
| 分层求解结构 | ★★★ | 内层 MCF 可证明最优是亮点，但为条件最优口径 |
| 编码/算子 | ★★★ | 相对母本增量清晰，fidelity 有测试 |
| 实验有效性 | ★★ | 真实数据 + 滚动闭环强；baseline 缺失、约束惰性、压缩反证三处硬伤 |
| 总体 | 可支撑硕士/博士论文的问题创新与方法创新 | **不足以支撑"显著优于现有方法"的宣称，直至补齐基线 |

---

## 六、对论文引言/相关工作的写作建议

1. **引言的故事线建议**：产业现状（多机巢集群巡检已成基础设施）→ 技术断点（单机航线生成 ≠ 机队调度；二维距离 ≠ 高原航程；静态任务集 ≠ 动态到达；全执行 ≠ 允许暂缓）→ 四维问题定义 → 分层求解框架 → 真实数据验证 → 贡献列表（问题/模型/方法/数据四层，按 §5 的"可主张"口径写）。
2. **相关工作结构建议**（对应 §3 七条线，每条以"最近工作 + 未覆盖点"收束到 G1–G7）：
   - 3.x 多无人机任务分配模型与算法（综述级）
   - 3.x 多机巢 MTSP 与终点选择（重点对 MMDUAVRP）
   - 3.x 选择性：OP/TOP 及其 UAV 变体（重点对 Evers 2014 / Xia 2019）
   - 3.x 动态与滚动时域（重点对 Song 2016 / RITRA 2026）
   - 3.x 三维地形代价与巡检航迹（重点对 WAFC-RRAS 垂直切面法——需明示同源与差异）
   - 3.x 离散映射 DE 与改进（母本 Zhao Ming 2017）
   - 3.x 电力巡检应用
3. **必须主动声明的研究局限**（审稿人必问）：
   - 终点巢为分层条件最优（非联合最优）；方案 B 推迟验证；
   - 当前实验规模下航程约束未激活（满充假设须声明）；
   - λ 为决策者偏好参数，主指标应报（完成率, 航程）Pareto 前沿而非单点完成率；
   - 无外部基线前不下"优于"结论。
4. **引用规范提醒**：本文件所列文献多数来自检索摘要，正式引用前须读全文核对（尤其：Xia et al. 2019 期刊卷期页、WAFC-RRAS 全称与作者、中文期刊《低空经济视域下…》的出处信息、以及各专利的法律状态）。

---

## 七、本轮检索文献清单（按线归类，附线索）

**综述**
1. 毕文豪 等. 无人机集群任务分配技术研究综述. 系统工程与电子技术, 2024. https://sys-ele.com/article/2024/1001-506X/20240318.shtml
2. Ghauri et al. A Review of Multi-UAV Task Allocation Algorithms for SAR. JSAN 13(5):47, 2024. https://www.mdpi.com/2224-2708/13/5/47
3. Multi-UAV Task Assignment in Dynamic Environments: Current Trends and Future Directions (2013–2024). DOAJ.

**多机巢 MTSP / 无人机路由**
4. Oberlin P., Rathinam S., Darbha S. Today's Traveling Salesmen Problem: Heterogeneous, Multiple Depot, Multiple UAV Routing Problem. IEEE RAM, 2010.
5. Sundar K., Rathinam S. An exact algorithm for a heterogeneous, multiple depot, multiple TSP. ICUAS 2015.
6. Li X. et al. A Hybrid Large Neighborhood Search Algorithm for Solving the Multi Depot UAV Swarm Routing Problem. IEEE Access 9:104115–104126, 2021. DOI:10.1109/ACCESS.2021.3098863
7. Solving multi-depot closed-path multiple traveling salesman problem (MDCP-MTSP). Sci. Rep., 2026. https://www.nature.com/articles/s41598-026-45824-3
8. Coutinho W.P. et al. The UAV routing and trajectory optimisation problem, a taxonomic review. CIE 120:116–128, 2018.
9. Macrina G. et al. Drone-aided routing: a literature review. TR-C 120:102762, 2020.
10. Khoufi I. et al. A survey of recent extended variants of TSP and VRP for UAVs. Drones 3(2):66, 2019.

**选择性（OP/TOP）**
11. Vansteenwegen P., Souffriau W. et al. The orienteering problem: A survey. EJOR 209(1):1–10, 2011.
12. Evers L., Barros A.I., Monsuur H., Wagelmans A. Online stochastic UAV mission planning with time windows and time-sensitive targets. EJOR 238(1):348–362, 2014.
13. Xia J. et al. Drone scheduling to monitor vessels in emission control areas. TR-B, 2019.
14. Prize Collecting Multiagent Orienteering: Price of Anarchy Bounds and Solution Methods. IEEE T-ASE 19:531, 2022.
15. Sadov T., Sharp T. A prize-dependent loitering time approach to UAS routing (TOP-PDLT). JAMIS, 2025.

**动态/滚动时域**
16. Song B.D., Kim J., Morrison J.R. Rolling Horizon Path Planning of an Autonomous System of UAVs for Persistent Cooperative Service. JINT 84:241–258, 2016. DOI:10.1007/s10846-015-0280-5
17. Kiema T.B. et al. Hierarchical Coordination of UAVs for Dynamic Task Assignment in Large-Scale Traffic Surveillance. SciTePress, 2025.
18. Han B. et al. RITRA: A Rolling Bayesian Information-Theoretic Framework for Multi-UAV Task Allocation Under False Alarm Uncertainty. Drones 10(8):599, 2026.
19. 归智. 低空经济视域下无人机电力巡检航线网络动态优化研究（滚动时窗 GA）. AIPUB, 2026.

**三维地形代价**
20. Multi-UAV Cooperative Mission Assignment Based on Hybrid WAFC-RRAS Algorithm（垂直切面法近似飞行代价）. IEEE, 2023.
21. Terrain-Aware Multi-Uav Cooperative Search Path Planning and Task Allocation Method (TA-MUCSTA). IEEE, 2025.
22. Tang J. et al. Energy- and Safety-Aware Multi-UAV Inspection over DEM Terrain (ES-EEA*). IEEE, 2026.
23. Dong B. et al. A Voronoi–A* Fusion Algorithm with Adaptive Layering for UAV Path Planning in Complex Terrain. Drones 9(8):542, 2025.
24. LF-TF-CPO: A Survivability-Oriented Min–Max Optimization for Multi-UAV Coverage Planning in Mountainous Terrains (ASTER GDEM). Drones 10(5):356, 2025.

**算法（DMDE 及同类）**
25. **Zhao Ming, Zhao Lingling, Su Xiaohong, Ma Peijun, Zhang Yanhang. Improved discrete mapping differential evolution for multi-UAVs cooperative multi-targets assignment under unified model. Int. J. Mach. Learn. & Cybern. 8(3):765–780, 2017. DOI:10.1007/s13042-015-0364-3**（本项目算法母本）
26. Zhao Ming et al. A Unified Modeling Method of UAVs Cooperative Target Assignment by Complex Multi-constraint Conditions. 自动化学报, 2012.
27. 无人机集群任务分配的多目标算法研究（分解多目标 DE + 禁忌）. 西安电子科技大学学报, 2023.
28. 不确定环境下多无人机任务分配（区间数 DE）. 海军航空大学学报, 2023.
29. A Differential Evolution for Task Allocation for Multi-UAVs with No-Fly Zone. IEEE, 2024.
30. Enhancing UAV Task Assignment with the Adaptive Sampling-Based Task Rationality Review Algorithm (ASTRRA). Drones 8(9):422, 2024.

**电力巡检应用**
31. 3D Path Planning of UAVs for Transmission Lines Inspection. Acta Press, 269–282.
32. Intelligent Flight Path Planning Method and System for UAVs in Rural Power Grid Inspection. 专利 CN121185319A.
33. Multi-machine power inspection rolling scheduling algorithm based on task self-adaptive clustering optimization. 专利 CN117993672A.
34. 无公网通信区多机巢协同的无人机自主飞巡方法及系统. 专利 CN121455215B.
35. 无人机集群协同巡检自主任务分配调度方法（CBBA）. 专利 CN122175310A.
36. 国网"集群式"机巢电力巡检报道. 全国电力设备管理网.

---

## 附：与 `docs/full_review_20261012.md` 的分工

| 文件 | 视角 |
|---|---|
| 本文（research_positioning） | **向外看**：文献坐标、空白在哪、创新性能宣称到什么程度 |
| full_review_20261012.md | **向内看**：代码/逻辑/实验的内部问题清单（P0–P3）与修复顺序 |

两者合起来构成"外部定位 + 内部体检"：G4/G5/G6/G7 四条空白同时是内部审查的 P1 问题，**补内部之短 = 填外部之空**。
