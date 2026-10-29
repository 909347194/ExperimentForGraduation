# 第 3 章方法：动态滚动的选择性多机巢多无人机协同任务分配

本文只描述**当前实现中的方法**（与 `task_allocation/methods/` 逐条对齐，代码口径 2026-10-28；F3 已整体移除），
不写文献综述与贡献口号。配置默认值以 `experiments/exp01_smoke/config.yaml` 为准。

---

## 1. 问题定义

### 1.1 场景与建模假设

| 假设 | 内涵 | 对建模的影响 |
|------|------|--------------|
| 任务是**持续到达的流** | 每周期有新到达、滞留、故障释放任务，不是一次性给定任务集 | 必须滚动时域周期重规划，上一周期方案会失效 |
| **供不应求** | 机队吞吐 < 任务到达规模（广域巡检：万基杆塔 vs 单机日 15~50 基） | "全部执行"不可行，**任务选择（执行/暂缓）本身是决策变量** |
| **多机巢、异源起降** | 机队分散在多个自动机巢；起降点不是同一个 | 起点 = 当前所在巢，**终点机巢是决策变量**（不必返原巢），受巢容量竞争 |
| **三维地理代价** | 高原/山地高差大、城市场景建筑密集，平面欧氏距离与真实航程严重偏离 | 航段代价 = 垂直切面三维航程（障碍包络 = 地形+建筑屋顶），且时间窗/ETA 同源 |
| 机队状态动态 | 位置、剩余航程、归属巢、可用性（充电/故障/忙碌） | 能力是时变量 cap(t)，"充电中的机不计入幻影容量" |

一句话：**在任务持续动态到达、机队部分可用、机巢异源起降、真实三维地形代价下，
滚动地决定"执行哪些任务、由谁执行、按何顺序、落哪个巢"，并在交付覆盖与航程之间权衡。**

### 1.2 单周期决策问题

滚动时域下，每周期 \(t\) 给定动态任务池 \(\mathcal{T}_t\)（新到达 ∪ 滞留 ∪ 故障释放）与
可用机队 \(U_t^{\mathrm{avail}}\)（位置、剩余航程、归属巢、是否可起飞），单周期要求输出：

1. 本周期进入精细规划的任务集 \(S=\mathcal{T}_t^{\mathrm{sel}}\)（阶段一）；
2. 指派、访问顺序、终点机巢与可执行航次集合（阶段二）。

决策变量（阶段二）：

| 符号 | 含义 | 取值 |
|------|------|------|
| \(y_j\) | 任务 \(j\) 是否在本周期交付 | \(\{0,1\}\)，允许 \(y_j=0\)（选择性） |
| \(x_{uj}\) | 任务 \(j\) 是否由 UAV \(u\) 执行 | \(\sum_u x_{uj}=y_j\)（一任务一机） |
| \(\pi_u\) | UAV \(u\) 的访问顺序 | 任务序列，允许空序列（空闲合法） |
| \(z_{ub}\) | UAV \(u\) 的终点机巢 | 在容量约束下与全机队**联合**择优 |

**约束（硬 / 软分开）：**

- 硬约束（物理可行性，任何一次违约在适应度中压倒任意交付收益）：
  航程（起点→任务链→**终点巢返航**全航程 ≤ 剩余航程）、时序/前置依赖、同步到达、
  机巢容量与可用性、末任务→终点巢可达性、结构（无重复分配/未知任务）；
- 软约束：**时间窗迟到**——「迟到不是废飞」：迟到交付仍计完成、计奖励，
  仅按迟到秒数线性折减计价（见 §4.5）。

**目标（字典序语义）：** 先在硬约束下尽量交付"已选且可装"的任务（覆盖）；
覆盖等价时再比总航程与软迟到。**防漂移不构成独立优化目标**——它靠问题层设计体现：终点机巢选择约束、返航代价（\(\lambda C_{\mathrm{path}}\) 已含）、跨周期部署 reposition/轮转巢；模型不立 makespan/负载均衡项。目标分量（建模量）：

\[
F_1=\sum_j R_j y_j,\qquad
F_2=C_{\mathrm{path}}(x,\pi,z,y)
\]

其中 \(C_{\mathrm{path}}\) 为含返航的三维航程总和，\(T_k\) 为第 \(k\) 架机的整航迹时长
（飞行+等待+服务+返巢）。标量适应度（覆盖门禁口径）见 §4.5；\(T_k\) 整航迹时长仅作诊断量保留，不立为目标。

### 1.3 为什么分层求解

\(y\times x\times \pi\times z\) 的组合规模随任务数 \(N!\)、机巢选择 \(B^{U}\) 增长，
且代价查询（DEM 三维航程）昂贵；单周期时间预算内做全要素联合精确求解不可达。
方法因此分三层：**选择（哪些任务）→ 分配（谁执行/何顺序/何巢）→ 滚动（跨周期状态）**，
每层把下一层的搜索空间压缩到可计算规模。

---

## 2. 总体框架

```text
任务池 T_t + 机队状态 U_t^avail
    → 阶段一 选择（selection/）：n_cand 候选 → n_sel 已选集 S（cap(t) 卡规模）
    → 选机（solvers/participating.py）：参与集 K 架（K ≤ N−1，SRP 巡游模型）
    → 阶段二 MUAS（muas/）：DMDE 搜索 → 解码修复链 → 终点巢内层精确分配
                            → 交付后成本 polish → 交付解
    → 滚动写回（rolling/）：完成闸门 / 滞留回池 / 机队位置与航程 → 下一周期
```

\[
\mathcal{T}_t
\xrightarrow{\text{选择}} S
\xrightarrow{\text{MUAS}} \mathcal{T}_t^{\mathrm{exec}},\; U_t^{\mathrm{exec}}
\]

代码入口：

| 步骤 | 路径 |
|------|------|
| 选择管道 | `methods/selection/pipeline.py` |
| 单周期 MUAS 主入口 | `methods/muas/solvers/stage_runner.py`（`run_selective_muas`） |
| 滚动外壳 | `methods/rolling/horizon.py` |

---

## 3. 阶段一：动态任务选择（`methods/selection/`）

回答的问题：**本周期哪些任务值得进入精细规划**。阶段一不锁定指派与顺序，
跨周期去向由任务池动态、名额与防饥饿机制共同决定。

### 3.1 规模口径：候选与选择解耦

- **候选池** \(n_{\mathrm{cand}}=\lceil\alpha K\rceil\)（α≈2.5）：搜索输入，**宜偏富**——
  缓解排序误差、给边际增益发现协同留空间；只受计算预算约束，**不被 cap(t) 砍塌**。
- **选择数** \(n_{\mathrm{sel}}\)：真实承诺，**宜偏紧**：

\[
n_{\mathrm{sel}}
=\mathrm{synthesize}\big(\lceil \mathrm{max\_ratio}\cdot K\rceil,\;\mathrm{cap}(t)\big)
\]

  `synthesize` 两种模式：`intersect`（默认）取 min——能力只能收紧、绝不超派；
  `primary` 以 cap(t) 为主、名额兜底（双向）。
- **能力上界** \(\mathrm{cap}(t)=\lfloor B(t)/\tilde c\rfloor\)：\(B(t)\) 为**可起飞子集**
  剩余航程之和（充电中的机不计入幻影容量），\(\tilde c\) 为候选池粗代价中位数；
  约束 \(n_{\mathrm{cand}}\ge n_{\mathrm{sel}}\)。

实现：`selection/capability.py`、`selection/pipeline.py`。

### 3.2 管道步骤

1. **基础优先级**（`priority.py`）：五项归一化综合分
   \(P_i(t)=w_r\tilde R+w_u\tilde U+w_w\tilde W+w_s\tilde G-w_c\tilde C\)
   （收益 / 时间紧迫度 / 等待补偿 / 空间协同增益 / 粗执行代价，min-max 归一、权重自动归一）。
2. **Top-α 预筛选**：按 \(P_i(t)\) 取前 \(n_{\mathrm{cand}}\) 个进候选池。
3. **物理向预筛**（`feasibility.py`）：只拒物理不可达（无机可用、往返超航程）；
   **迟到不作为参选资格否决**（软约束原则）。
4. **边际纳入**（`marginal_gain.py`）：贪心追加，接受判据
   \(\mathrm{eff}=\dfrac{\Delta B}{\max(\Delta C,\varepsilon)}\,(1+\gamma\cdot\mathrm{age})+b_{\mathrm{forced}}\)
   ——边际收益/边际代价比，等待时长乘性软加权（防饥饿 Fix①），
   直至触达 \(n_{\mathrm{sel}}\) 或边际比过低。
5. **事件/防饥饿强制纳入**：紧急任务、故障释放任务、等待超时或临期任务
   绕过候选上限强制纳入（Fix②），终刀仍受可行性约束——保证不被无限期推迟。

输出：已选集 \(S\)，\(|S|\le n_{\mathrm{sel}}\)。

---

## 4. 阶段二：选择性多机巢 MUAS（`methods/muas/`）

输入 \(S\)、当前机队、三维代价接口；输出各 UAV 航次（任务序列 + 终点巢）及诊断量。
求解主干：**选机 → DMDE 搜索 → 解码修复链 → 终点巢内层求解 → 交付后成本 polish**。

### 4.1 参与机选择（`solvers/participating.py`）

本周期派 \(K\) 架机进精细规划，与任务数解耦、三层裁剪：

1. **SRP 不变式**：\(K\le N-1\)（一任务一机且允许巡游；overloaded 模型会重复分配任务，不可用）；
2. **运营预算**：`sortie_budget`（出动架次上限）、`max_participating_uavs`（硬上限）；
3. **出动经济性**（`marginal` / `demand_marginal` 策略）：边际贡献不足以抵
   出动代价 γ 的机跳过（gain 非单调 ⇒ 跳过不停机）。

参与策略：`cheapest` / `first` / `marginal` / **`demand`（默认）** / `demand_marginal`。
`demand` = 设施选址式贪心最大覆盖（保证每个选中任务有一架可达参与机，避免机队
堆在稠密区饿死离群任务）+ 服务能力感知补机（覆盖 ≠ 能服务：按航程预算把过载
任务剥给最近空闲机）。**选机层只做效率预筛**——「不漏可达任务」由解码端安全网保证
（`reflow_rescue_scope="fleet"`：救援池扩到全可用机队，可达即执行）。

### 4.2 编码与 DMDE 搜索（`representation/` + `solvers/dmde_solver.py`）

- **统一编码**：起始基因（UAV→首任务倾向）+ 巡游基因（访问顺序相关的连续代价量），
  基因规模与任务数绑定；**终点巢不进基因**（由解码后内层精确分配，方案 A）。
- **离散映射 DE（与 Ming 等 2017 同族）**：差分变异在**飞行代价空间**进行
  （`cost_vector`），再按匹配规则逆映射回离散 \((u,j)\) 指派与顺序；
  每代个体经同一套解码修复链评价。
- **搜索机制**：动态交叉率 \(CR(g)=1-(\log g/\log S)^{\zeta}\)（公式 3-9，ζ=3；
  大 CR 偏探索、小 CR 偏开发）、混合差分策略（公式 3-10：CR 高时 DE/rand/1
  探索、低时 DE/best/2 开发）、动态缩放因子 \(F\)（公式 3-11）、
  逆映射温度退火（\(T=1-g/S\)）、GMR 灭绝重启（公式 3-12 原式 GMR=1−CR）、
  贪婪选择；主口径 pop50 × gen100（gen 数是搜索预算的量级标定项，见 exp01 notes）。
- **Lamarckian 回写**：获胜个体被修复后，把修复解的**起始任务归属**写回基因
  （只改起始基因通道、绝不增删基因——长度不变式有测试守护），使后继代际的差分
  围绕修复后的可行子空间展开。

### 4.3 解码与可行性修复（`solvers/decode_repair.py`，`decode_mode=repair_chain`）

DMDE 给出的原始指派不保证可行；解码 = **按固定管线调用修复算子组，修到不动点**：

```text
（≤6 轮不动点）合并 _consolidate → [经济剔除 _prune，门禁口径下恒关]
              → 航程修复 _repair_range → 终点巢 _choose_end_nests
→ 覆盖补全 _complete_coverage（或 coverage_first：EDD 装载至不动点）
→ 时间窗重排 _reorder_for_deadlines（纯调序）
→ [零奖励剔除 _prune_zero_reward，默认关]
→ 航程闸 _repair_range → 终点巢 _choose_end_nests → 航程闸 _repair_range
→ 覆盖门禁 repair_coverage / audit_coverage（§4.5）
→ 装配 _assemble
```

| 算子 | 作用 | 接受纪律 |
|------|------|----------|
| `_consolidate` | 任务少时向更少机合并，闲置合法化 | 只改派不删任务；合并后必须落进受体航程预算 |
| `_repair_range` | 超航程航次逐个移除"移除后节省最多"的任务 | 被移除任务**当场改派**（reflow），全不可行才记 \(y_j=0\) 证明性暂缓 |
| `_reflow_task` | 改派救援：插入现有航次 / 闲置机开班 / 换机腾位 | 可达即执行（存在量化）；修复不得制造**新**迟到 |
| `_choose_end_nests` | 终点机巢内层求解（见 §4.4） | 容量约束下联合最优 |
| `_reorder_for_deadlines` | 航次内调序降迟到 | 不改任务集；判据字典序（迟到数, 迟到量, 航程） |
| `_complete_coverage` | 把"可达却被留在解外"的任务补进航次 | 只补不删；硬不可达保持 \(y_j=0\) |

对照臂 `decode_mode=constructive`（`solvers/decode_constructive.py`，exp04 B1）：
把救援前置为主解码（逐任务可行性内联插入），与 repair_chain 共用同一批原语。

### 4.4 终点机巢内层求解（`constraints/nest_capacity.py`）

路线给定后，终点机巢在**容量约束下由最小费用流联合精确求解**（不是各机抢最近巢、
先到先得）；**返航代价进入完整 path 参与评价**，不在评价外事后补巢。
机巢容量按"起飞释放原巢名额、落地占终点巢名额"现算（不落第二份状态）。
事件标记不可用的机巢不可选为终点（容错分支）。

### 4.5 覆盖门禁与适应度（`problem.py` / `coverage_gate.py`）

**业务意图（单周期）**：硬约束下尽量交付已选可装任务；覆盖等价时比较航程与软迟到。
实现为**覆盖门禁口径（唯一主口径，2026-10-24 裁定）**——"完成是约束、成本是目标"
写成构造性合法条件，而非 big-M 软目标（非门禁 big-M 四层路径已从代码移除）：

\[
f(s)=
\underbrace{W_{\mathrm{hard}}\,n_{\mathrm{hard}}+w_{\mathrm{exc}}E_m}_{\mathrm{T0\ 物理硬违约}}
+\underbrace{W_{\mathrm{skip}}\,n_{\mathrm{skip}}}_{\mathrm{T0.5\ 覆盖门禁}}
+\underbrace{w_{\mathrm{late}}L_{\mathrm{tot}}}_{\mathrm{T2\ 迟到(软)}}
+\underbrace{\lambda\,C_{\mathrm{path}}}_{\mathrm{T3\ 航程成本}}
\quad(\text{最小化})
\]

- **T0**：\(W_{\mathrm{hard}}=\kappa\, m R_{\mathrm{pool}}\)（自动推导）使任何一次物理违约
  压倒任意交付收益——"纸面可行、执行弹回"的幽灵解永不入选；
  \(E_m\)（超限米数）只在不可行解间做层内排序。
- **T0.5**：\(W_{\mathrm{skip}}\) 同推导。门禁定义合法解：不存在"任务 \(j\in S\)
  未被交付，且存在空闲机可开班或某航次可插入"（**voluntary_skip** → 非法）；
  一阶检验确实装不下的记 **capacity_shortfall**（能力不足，允许不交付）。
  门禁修复模式下 voluntary_skip 由构造归零；**一阶完备、非全局最大覆盖证明**
  （不查"两个未覆盖任务拼班才装得下"的组合可行——边界显式声明并随
  `gate_stats` 落盘）。
- **T2**：迟到秒数软计价（0.01/s）——迟到可接受，与路径同台竞争；
  迟到与任务数的权衡由门禁管（完成是约束）。
- **T3**：\(\lambda C_{\mathrm{path}}\)，完成等价时的最小化目标（航程含返巢代价，既承担防漂移又不立均衡项）。
- **固定表述：搜索用 \(f\) 不含 \(-m F_1\)**。门禁下合法解的已选可装任务已全部
  交付 ⇒ \(F_1=\sum_j R_j y_j\) 对解无区分度，只作**事后指标**（奖励和口径，
  不掺迟到折减）。
- 评价权重每次落盘快照（`fitness_weights`），交付解适应度可用同一组权重独立重算对账
  （审计 N 项据此交叉验证）。

### 4.6 交付后成本 polish（`solvers/route_polish.py` + `local_search.py`）

DMDE 收敛后对**交付解**做局部搜索（不重开种群）；三层邻域互补：

| 层 | 算子 | 维度 | 默认 |
|----|------|------|------|
| 搜索/收敛后 | `polish_assignment`：relocate / swap / 插入位 | 归属+顺序 | 开 |
| 交付后组内 | 2-opt（段反转）/ Or-opt（段迁移） | 顺序 | 开 |
| 交付后跨机 | 单任务改派（受体含空闲机） | 归属 | 开 |
| 覆盖优先变体 | 单调成本 polish + 覆盖再装载（`coverage_first`） | 两段式 | 关（gated 消融） |

统一接受纪律（**四道门**）：① 航程门（改后全航程 ≤ 各自剩余航程）；
② 迟到门（迟到数与迟到总量逐分量不劣化）；③ 执行集门（覆盖只增不减）；
④ 成本门（航程**严格下降** > ε）。全程无随机、逐位可复现；
纯成本改进 ⇒ 任意 λ>0 下适应度严格改进，不触碰主适应度阶梯。

口径细节：空任务航次**不计**「机→巢」幻影距离；组内顺序优化只作用于交付解
（解码期最近邻重排已被 A/B 证伪，`reorder_range_optimize` 默认关）。

### 4.7 搜索期内强化（可选，`polish_in_loop` memetic）

每 `memetic_K` 代对当代精英跑一次从紧预算的 `polish_assignment`，维护 best-polished
档案（**不改基因型**、不扰动 DE 轨迹——RNG-free 不变量有测试守护）；交付解取
"收敛后 polish"与"档案最优"的更优者。默认关（exp01 memetic 变体显式开）。

---

## 5. 滚动闭环（`methods/rolling/horizon.py`）

每周期：

1. 更新任务池（新到达 ∪ 滞留）与可用机（充电/故障/位置/剩余航程）；
2. 调用阶段一 + 阶段二得到交付解；
3. **完成闸门**：物理不可行的任务不顺延完成（解绑回池、下周期重选，完成率不说谎；
   迟到交付仍计完成）；
4. 推进仿真时钟；事件触发（UAV 故障 / 突发高优先级任务 / 机巢不可用）本周期重规划；
5. 稳态早停：池内未完成任务数连续多周期不变即可停（剩余为物理不可达的死任务）。

跨周期语义：任务暂缓**不是丢失**——shortfall 非永久淘汰，滞留任务经等待补偿与
防饥饿机制持续获得机会；机队降落后换电回满航程（`recharge_at_nest`）。

---

## 6. 代价模型（`methods/muas/cost/`）

- **三维航程（垂直切面）**：障碍包络（地面高程 + 建筑屋顶）上的地形跟随三维航程（最小离地余量/巡航高度/爬升斜率约束）；城市场景（guangzhou）建筑进包络，无建筑场景退化为 DEM 地形口径
  （`vertical_section.py`），不是平面直线；
- 机–任务、任务–任务、任务–巢统一查询接口（`provider.py`）；
- **口径唯一**：适应度、ETA/时间窗判定、选择层预筛共用同一距离函数
  （历史教训：判定用直线、飞行用地形 ⇒ 放进来不及的任务白飞）；
- 惰性 pairwise 缓存（`lazy_pairwise.py`）按需计算，控内存。

---

## 7. 实验接口与基线（`experiments/exp05_baselines`）

同一滚动外壳、同一任务到达序列、同一约束评价可注入不同第二阶段引擎：
随机可行分配 / 收益代价比贪心插入 / VRPTW 硬时间窗插入 / 本文方法（DMDE），
外加同架构欧氏距离对照（只换代价模型）。**这同时是"求解器可替换"的证据**——
本文贡献在问题形式化与分层框架，DMDE 是该框架的一个求解器实例。

---

## 8. 生产默认口径速览（exp01）

| 项 | 默认 | 备注 |
|----|------|------|
| 适应度 | 覆盖门禁（唯一口径） | `coverage_gate`+`lexicographic_coverage` 开；F1 只作指标 |
| λ（成本权重） | 1e-1 | 与 1e-2/1e-4 的历史数据**不可混用** |
| 迟到 | 软约束：折减窗 W=1800 s + 0.01/s 计价 | 迟到计完成、计奖励 |
| 解码 | `repair_chain` | `constructive` 为 exp04 B1 对照臂 |
| 参与策略 | `demand`（Fix④⑤） | 救援池 `fleet`（Fix⑥） |
| 剔除 | 全关（`prune_unprofitable=false`） | 与"完成是约束"冲突，仅消融开 |
| polish | assignment + 交付后 2-opt/Or-opt + 跨机改派 均开 | `reorder_range_optimize` 关（A/B 证伪） |
| memetic / coverage_first | 关 | gated 消融用 |
| DMDE | pop50 × gen200，ζ=3，GMR 原式 | gen 数是预算标定项 |

---

## 9. 模块索引

```text
methods/
  selection/            阶段一：priority → feasibility → marginal_gain → capability
  muas/
    problem.py          模型、目标分量、scalar_fitness（门禁口径）
    coverage_gate.py    覆盖门禁：一阶检验 / 审计 / 构造性修复
    cost/               三维地理代价（vertical_section=地形+建筑包络 / provider / lazy_pairwise）
    representation/     统一编码、映射、逆映射、修复规则
    constraints/        硬约束判据 + 终点巢内层 MCF（nest_capacity）
    solvers/
      stage_config.py        编排参数 MUASStageConfig / POLICY_*
      participating.py       参与机选择（K 决策）
      tour_cost.py           航次代价 / ETA / 时间窗原语（口径唯一来源）
      decode_repair.py       repair_chain 解码与修复算子组
      decode_constructive.py 构造式解码（exp04 B1 对照臂）
      route_polish.py        交付解成本 polish 算子组
      evaluator.py           MUASAssignmentEvaluator / Lamarckian 回写
      stage_runner.py        run_selective_muas 主入口 / 枚举参照
      dmde_solver.py         DMDE 循环（算法 3.1）
      local_search.py        指派邻域（relocate/swap/position）
      muas_stage.py          兼容门面（2026-10-27 拆分，导入路径不变）
  rolling/              horizon.py（滚动闭环）/ event_trigger.py（事件）
  task_pool.py          任务池（到达/滞留/释放）
  uav_state.py          机队状态（位置/航程/巢/可用性）
```
