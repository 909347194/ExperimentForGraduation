# 第 3 章方法:动态滚动的选择性多机巢多无人机协同任务分配

本文只描述**当前实现中的方法**（与 `task_allocation/methods/` 逐条对齐，代码口径（异构 per-UAV 化 + 服务休眠态）；F3 已整体移除），
不写文献综述与贡献口号。配置默认值以 `experiments/exp01_smoke/config.yaml` 为准。

**目标口径(全文一致):完成是约束,成本是目标。** 可完成任务必须全部交付,由覆盖门禁构造性强制;在该可行域上以单一加权标量最小化含返航三维航程与迟到软计价。不采用以交付收益最大化为第一目标的字典序形式,也不采用 big-M 加权把完成数写进目标(该路径已从代码移除);`lexicographic`、`lexicographic_coverage` 等开关名为历史命名,不代表当前方法采用字典序。迟到在搜索适应度与 \(F_1\) 中只经 \(w_{\mathrm{late}}L_{\mathrm{tot}}\) 单一计价、计全额奖励不折减、不淘汰任务;奖励折减仅存在于事后指标,见 §8。

---

## 1. 问题定义

### 1.1 场景与建模假设

| 假设 | 内涵 | 对建模的影响 |
|------|------|--------------|
| 任务是**持续到达的流** | 每周期有新到达、滞留、故障释放任务,不是一次性给定任务集 | 必须滚动时域周期重规划,上一周期方案会失效 |
| **供不应求** | 机队吞吐 < 任务到达规模(广域巡检:万基杆塔 vs 单机日 15~50 基) | "全部执行"不可行,**任务选择(执行/暂缓)本身是决策变量** |
| **多机巢、异源起降** | 机队分散在多个自动机巢;起降点不是同一个 | 起点 = 当前所在巢,**终点机巢是决策变量**(不必返原巢),受巢容量竞争 |
| **三维地理代价** | 高原/山地高差大、城市场景建筑密集,平面欧氏距离与真实航程严重偏离 | 航段代价 = 垂直切面三维航程(障碍包络 = 地形+建筑屋顶),且时间窗/ETA 同源 |
| 机队状态动态 | 位置、剩余航程、归属巢、可用性(充电/故障/忙碌)、**异构飞行性能**(各机 `max_range`/`max_time`/`speed` 可不同，`UAV.full_charge_range()` 解析，None 回退全局缺省=同构退化) | 能力是时变量 cap(t)，"充电中的机不计入幻影容量"；换电恢复到**该机**满电航程 |

一句话:**在任务持续动态到达、机队部分可用、机巢异源起降、真实三维地形代价下,
滚动地决定"执行哪些任务、由谁执行、按何顺序、落哪个巢",并把可完成任务的完成作为硬约束,在该约束下压缩含返航的三维航程。**

### 1.2 单周期决策问题

滚动时域下,每周期 \(t\) 给定动态任务池 \(\mathcal{T}_t\)(新到达 ∪ 滞留 ∪ 故障释放)与
可用机队 \(U_t^{\mathrm{avail}}\)(位置、剩余航程、归属巢、是否可起飞),单周期要求输出:

1. 本周期进入精细规划的任务集 \(S=\mathcal{T}_t^{\mathrm{sel}}\)(阶段一);
2. 指派、访问顺序、终点机巢与可执行航次集合(阶段二)。

决策变量(阶段二):

| 符号 | 含义 | 取值 |
|------|------|------|
| \(y_j\) | 任务 \(j\) 是否在本周期交付 | \(\{0,1\}\),允许 \(y_j=0\)(选择性) |
| \(x_{uj}\) | 任务 \(j\) 是否由 UAV \(u\) 执行 | \(\sum_u x_{uj}=y_j\)(一任务一机) |
| \(\pi_u\) | UAV \(u\) 的访问顺序 | 任务序列,允许空序列(空闲合法) |
| \(z_{ub}\) | UAV \(u\) 的终点机巢 | 在容量约束下与全机队**联合**择优 |

**约束(硬 / 软分开):**

- 硬约束（物理可行性，任何一次违约在适应度中压倒任意交付收益）：
  航程（起点→任务链→**终点巢返航**全航程 ≤ 该机剩余航程，逐机判定）、飞行时间/续航
  （全航迹时长 ≤ `UAV.max_time`（per-UAV），与航程**对称**；null 时按该机
  max_range/speed 自动派生、不新增淘汰，显式更小值即成独立绑定约束）、时序/前置依赖、
  同步到达、机巢容量与可用性、末任务→终点巢可达性、结构（无重复分配/未知任务）；
- 软约束:**时间窗迟到**--「迟到不是废飞」:迟到交付仍计完成、计全额奖励,
  仅按迟到秒数经 \(w_{\mathrm{late}}L_{\mathrm{tot}}\) 单一软计价(不折减奖励、不否决参选资格、不判航次作废;见 §4.5)。

**目标口径(完成是约束、成本是目标):** 以"已选且可装的任务必须全部交付"界定可行域(由覆盖门禁构造性强制,见 §4.5),
在该可行域上以单一加权标量最小化含返航三维航程与迟到软计价;合法解的完成量恒相同,故 \(F_1\) 对解无区分度,只作事后指标。
不采用以交付收益最大化为第一目标的字典序形式,也不采用 big-M 加权把完成数写进目标。**防漂移不构成独立优化目标**--它靠问题层设计体现:终点机巢选择约束、返航代价(\(\lambda C_{\mathrm{path}}\) 已含)、跨周期部署 reposition/轮转巢;模型不立 makespan/负载均衡项。目标分量(建模量):

\[
F_1=\sum_j R_j y_j,\qquad
F_2=C_{\mathrm{path}}(x,\pi,z,y)
\]

其中 \(C_{\mathrm{path}}\) 为含返航的三维航程总和,\(T_k\) 为第 \(k\) 架机的整航迹时长
(飞行+等待+服务+返巢)。门禁口径下 \(F_1\) 只作事后指标、不进搜索适应度 \(f\);\(f\) 为含硬违约/门禁支配层的单一加权标量,合法解上退化为 \(w_{\mathrm{late}}L_{\mathrm{tot}}+\lambda C_{\mathrm{path}}\)(见 §4.5);\(T_k\) 整航迹时长仅作诊断量保留,不立为目标。

### 1.3 为什么分层求解

\(y\times x\times \pi\times z\) 的组合规模随任务数 \(N!\)、机巢选择 \(B^{U}\) 增长,
且代价查询(DEM 三维航程)昂贵;单周期时间预算内做全要素联合精确求解不可达。
方法因此分三层:**选择(哪些任务)→ 分配(谁执行/何顺序/何巢)→ 滚动(跨周期状态)**,
每层把下一层的搜索空间压缩到可计算规模。

---

## 2. 总体框架

```text
任务池 T_t + 机队状态 U_t^avail
    → 阶段一 选择(selection/):n_cand 候选 → n_sel 已选集 S(cap(t) 卡规模)
    → 选机(solvers/participating.py):参与集 K 架(K ≤ N-1,SRP 巡游模型)
    → 阶段二 MUAS(muas/):DMDE 搜索 → 解码修复链 → 终点巢内层精确分配
                            → 交付后成本 polish → 交付解
    → 滚动写回(rolling/):完成闸门 / 滞留回池 / 机队位置与航程 → 下一周期
```

\[
\mathcal{T}_t
\xrightarrow{\text{选择}} S
\xrightarrow{\text{MUAS}} \mathcal{T}_t^{\mathrm{exec}},\; U_t^{\mathrm{exec}}
\]

代码入口:

| 步骤 | 路径 |
|------|------|
| 选择管道 | `methods/selection/pipeline.py` |
| 单周期 MUAS 主入口 | `methods/muas/solvers/stage_runner.py`(`run_selective_muas`) |
| 滚动外壳 | `methods/rolling/horizon.py` |

---

## 3. 阶段一:动态任务选择(`methods/selection/`)

回答的问题:**本周期哪些任务值得进入精细规划**。阶段一不锁定指派与顺序,
跨周期去向由任务池动态、名额与防饥饿机制共同决定。

### 3.1 规模口径:候选与选择解耦

- **候选池** \(n_{\mathrm{cand}}=\lceil\alpha K\rceil\)(α≈2.5):搜索输入,**宜偏富**--
  缓解排序误差、给边际增益发现协同留空间;只受计算预算约束,**不被 cap(t) 砍塌**。
- **选择数** \(n_{\mathrm{sel}}\):真实承诺,**宜偏紧**:

\[
n_{\mathrm{sel}}
=\mathrm{synthesize}\big(\lceil \mathrm{max\_ratio}\cdot K\rceil,\;\mathrm{cap}(t)\big)
\]

  `synthesize` 两种模式:`intersect`(默认)取 min--能力只能收紧、绝不超派;
  `primary` 以 cap(t) 为主、名额兜底(双向)。
- **能力上界** \(\mathrm{cap}(t)=\lfloor B(t)/\tilde c\rfloor\):\(B(t)\) 为**可起飞子集**
  剩余航程之和(充电中的机不计入幻影容量),\(\tilde c\) 为候选池粗代价中位数;
  约束 \(n_{\mathrm{cand}}\ge n_{\mathrm{sel}}\)。

实现:`selection/capability.py`、`selection/pipeline.py`。

### 3.2 管道步骤

1. **基础优先级**(`priority.py`):五项归一化综合分
   \(P_i(t)=w_r\tilde R+w_u\tilde U+w_w\tilde W+w_s\tilde G-w_c\tilde C\)
   (收益 / 时间紧迫度 / 等待补偿 / 空间协同增益 / 粗执行代价,min-max 归一、权重自动归一)。
2. **Top-α 预筛选**:按 \(P_i(t)\) 取前 \(n_{\mathrm{cand}}\) 个进候选池。
3. **物理向预筛**(`feasibility.py`):只拒物理不可达(无机可用、**逐机**往返超航程：
   ∃ 可用机 u 满足 2·c(t,u) ≤ margin·remaining(u) 即放行——异构口径下短程机
   装不下 ≠ 不可服务，更远的长程机可能装得下；归因分界 `no_uav_in_range` /
   `exceed_round_trip_range` 按**机队最强满电航程**判几何可达);
   **迟到不作为参选资格否决**(软约束原则)。
4. **边际纳入**(`marginal_gain.py`):贪心追加,接受判据
   \(\mathrm{eff}=\dfrac{\Delta B}{\max(\Delta C,\varepsilon)}\,(1+\gamma\cdot\mathrm{age})+b_{\mathrm{forced}}\)
   --边际收益/边际代价比,等待时长乘性软加权(防饥饿),
   直至触达 \(n_{\mathrm{sel}}\) 或边际比过低。
5. **事件/防饥饿强制纳入**:紧急任务、故障释放任务、等待超时或临期任务
   绕过候选上限强制纳入(防饥饿强制),终刀仍受可行性约束--保证不被无限期推迟。

输出:已选集 \(S\),\(|S|\le n_{\mathrm{sel}}\)。

---

## 4. 阶段二:选择性多机巢 MUAS(`methods/muas/`)

输入 \(S\)、当前机队、三维代价接口;输出各 UAV 航次(任务序列 + 终点巢)及诊断量。
求解主干:**选机 → DMDE 搜索 → 解码修复链 → 终点巢内层求解 → 交付后成本 polish**。

### 4.1 参与机选择(`solvers/participating.py`)

本周期派 \(K\) 架机进精细规划,与任务数解耦、三层裁剪:

1. **SRP 不变式**:\(K\le N-1\)(一任务一机且允许巡游;overloaded 模型会重复分配任务,不可用);
2. **运营预算**:`sortie_budget`(出动架次上限)、`max_participating_uavs`(硬上限);
3. **出动经济性**(`marginal` / `demand_marginal` 策略):边际贡献不足以抵
   出动代价 γ 的机跳过(gain 非单调 ⇒ 跳过不停机)。

参与策略:`cheapest` / `first` / `marginal` / **`demand`(默认)** / `demand_marginal`。
`demand` = 设施选址式贪心最大覆盖(保证每个选中任务有一架可达参与机,避免机队
堆在稠密区饿死离群任务)+ 服务能力感知补机(覆盖 ≠ 能服务:按航程预算把过载
任务剥给最近空闲机)。**选机层只做效率预筛**--「不漏可达任务」由解码端安全网保证
(`reflow_rescue_scope="fleet"`:救援池扩到全可用机队,可达即执行)。

### 4.2 编码与 DMDE 搜索(`representation/` + `solvers/dmde_solver.py`)

- **统一编码**:起始基因(UAV→首任务倾向)+ 巡游基因(访问顺序相关的连续代价量),
  基因规模与任务数绑定;**终点巢不进基因**(由解码后内层精确分配,方案 A)。
- **离散映射 DE(与 Ming 等 2017 同族)**:差分变异在**飞行代价空间**进行
  (`cost_vector`),再按匹配规则逆映射回离散 \((u,j)\) 指派与顺序;
  每代个体经同一套解码修复链评价。
- **搜索机制**:动态交叉率 \(CR(g)=1-(\log g/\log S)^{\zeta}\)(公式 3-9,ζ=3;
  大 CR 偏探索、小 CR 偏开发)、混合差分策略(公式 3-10:CR 高时 DE/rand/1
  探索、低时 DE/best/2 开发)、动态缩放因子 \(F\)(公式 3-11)、
  逆映射温度退火(\(T=1-g/S\))、GMR 灭绝重启(公式 3-12 原式 GMR=1-CR,
  δ 门限可选)、贪婪选择;主口径 pop50 × **gen200**(由 gen100 上调;
  gen 数是搜索预算的量级标定项,CR/温度调度均以其为分母,500 代作敏感性对照,
  见 exp01 notes)。
- **Lamarckian 回写**(M1,默认开):获胜个体被修复后,把修复解的**起始任务归属**
  写回基因(只改起始基因通道、绝不增删基因--长度不变式有测试守护),使后继
  代际的差分围绕修复后的可行子空间展开。**M2-1 段内巡游序回写**(
  默认关,开关对照进行中):进一步把修复解的**段内访问顺序**写回巡游基因通道,
  五道闸门守护 + `writeback_stats` 诊断计数(`lamarckian_patrol_writeback`,
  见 `experiments/ab_m21_patrol_writeback/`)。

### 4.3 解码与可行性修复(`solvers/decode_repair.py`,`decode_mode=repair_chain`)

DMDE 给出的原始指派不保证可行;解码 = **按固定管线调用修复算子组,修到不动点**:

```text
(≤6 轮不动点)合并 _consolidate → [经济剔除 _prune,门禁口径下恒关]
              → 航程修复 _repair_range → 终点巢 _choose_end_nests
→ 覆盖补全 _complete_coverage(或 coverage_first:EDD 装载至不动点)
→ 时间窗重排 _reorder_for_deadlines(纯调序)
→ [零奖励剔除 _prune_zero_reward,默认关]
→ 航程闸 _repair_range → 终点巢 _choose_end_nests → 航程闸 _repair_range
→ 覆盖门禁 repair_coverage / audit_coverage(§4.5)
→ 装配 _assemble
```

| 算子 | 作用 | 接受纪律 |
|------|------|----------|
| `_consolidate` | 任务少时向更少机合并,闲置合法化 | 只改派不删任务;合并后必须落进受体航程预算 |
| `_repair_range` | 超航程航次逐个移除"移除后节省最多"的任务 | 被移除任务**当场改派**(reflow),全不可行才记 \(y_j=0\) 证明性暂缓;飞行时间预算对称生效--访问序评估(`_best_order`:全航程/速度 > `max_time` 即该序不可行)与可行性/门禁一阶检验均同口径核对 |
| `_reflow_task` | 改派救援:插入现有航次 / 闲置机开班 / 换机腾位 | 可达即执行(存在量化);修复不得制造**新**迟到 |
| `_choose_end_nests` | 终点机巢内层求解(见 §4.4) | 容量约束下联合最优 |
| `_reorder_for_deadlines` | 航次内调序降迟到 | 不改任务集;按 (迟到数, 迟到量, 航程) 依次逐分量比较(算子内判据,与目标层口径无关) |
| `_complete_coverage` | 把"可达却被留在解外"的任务补进航次 | 只补不删;硬不可达保持 \(y_j=0\) |

对照设置 `decode_mode=constructive`(`solvers/decode_constructive.py`,exp04 B1):
把救援前置为主解码(逐任务可行性内联插入),与 repair_chain 共用同一批原语。

### 4.4 终点机巢内层求解(`constraints/nest_capacity.py`)

路线给定后,终点机巢在**容量约束下由最小费用流联合精确求解**(不是各机抢最近巢、
先到先得);**返航代价进入完整 path 参与评价**,不在评价外事后补巢。
机巢容量按"起飞释放原巢名额、落地占终点巢名额"现算(不落第二份状态)。
事件标记不可用的机巢不可选为终点(容错分支)。

### 4.5 覆盖门禁与适应度(`problem.py` / `coverage_gate.py`)

**业务意图(单周期)**:完成是约束、成本是目标--已选且可装的任务必须全部交付;在该约束给定的可行域上比较航程与迟到软计价,合法解完成量恒相同,不存在"覆盖等价时再比成本"的额外目标层。
实现为**覆盖门禁口径(唯一主口径,口径约定)**--"完成是约束、成本是目标"
写成构造性合法条件,而非以收益最大化为主目标的字典序 / big-M 软目标(非门禁 big-M 四层路径已从代码移除):

\[
f(s)=
\underbrace{W_{\mathrm{hard}}\,n_{\mathrm{hard}}+w_{\mathrm{exc}}E_m}_{\mathrm{T0\ 物理硬违约}}
+\underbrace{W_{\mathrm{skip}}\,n_{\mathrm{skip}}}_{\mathrm{T0.5\ 覆盖门禁}}
+\underbrace{w_{\mathrm{late}}L_{\mathrm{tot}}}_{\mathrm{T2\ 迟到(软)}}
+\underbrace{\lambda\,C_{\mathrm{path}}}_{\mathrm{T3\ 航程成本}}
\quad(\text{最小化})
\]

- **T0**:物理硬违约含航程/飞行时间/时序/同步/巢容量/终点不可达/结构各判据。
  \(W_{\mathrm{hard}}=\kappa\, m R_{\mathrm{pool}}\)(自动推导)使任何一次物理违约
  压倒任意交付收益--"纸面可行、执行弹回"的幽灵解永不入选;
  \(E_m\)(超限米数)只在不可行解间做层内排序。
- **T0.5**:\(W_{\mathrm{skip}}\) 同推导。门禁定义合法解:不存在"任务 \(j\in S\)
  未被交付,且存在空闲机可开班或某航次可插入"(**voluntary_skip** → 非法);
  一阶检验确实装不下的记 **capacity_shortfall**(能力不足,允许不交付)。
  一阶可插检验同时核对航程与飞行时间预算(`_time_ok`):超时任务归
  capacity_shortfall(物理装不下),而非 voluntary_skip(能装不装)。
  门禁修复模式下 voluntary_skip 由构造归零;**一阶判据不完备,非全局最大覆盖证明**
  (不查"两个未覆盖任务拼班才装得下"的组合可行--边界显式声明并随
  `gate_stats` 落盘)。
- **T2**:迟到秒数软计价(0.01/s)--迟到可接受,与路径同台竞争;
  迟到与任务数的取舍由门禁(完成是约束)口径,不在目标层作权衡。
- **T3**:\(\lambda C_{\mathrm{path}}\),完成约束给定可行域上的最小化目标(航程含返巢代价,既承担防漂移又不立均衡项);合法解上 \(f\) 即 \(w_{\mathrm{late}}L_{\mathrm{tot}}+\lambda C_{\mathrm{path}}\) 的单一加权标量。
- **固定表述:搜索用 \(f\) 不含 \(-m F_1\)**。门禁下合法解的已选可装任务已全部
  交付 ⇒ \(F_1=\sum_j R_j y_j\) 对解无区分度,只作**事后指标**(奖励和口径,
  不掺迟到折减)。
- 评价权重每次落盘快照(`fitness_weights`),交付解适应度可用同一组权重独立重算对账
  (审计 N 项据此交叉验证)。

### 4.6 交付后成本 polish(`solvers/route_polish.py` + `local_search.py`)

DMDE 收敛后对**交付解**做局部搜索(不重开种群);三层邻域互补:

| 层 | 算子 | 维度 | 默认 |
|----|------|------|------|
| 搜索/收敛后 | `polish_assignment`:relocate / swap / 插入位 | 归属+顺序 | 开 |
| 交付后组内 | 2-opt(段反转)/ Or-opt(段迁移) | 顺序 | 开 |
| 交付后跨机 | 单任务改派(受体含空闲机) | 归属 | 开 |
| 覆盖再装载开关 | 单调成本 polish + 覆盖再装载(`coverage_first`,为算子开关名,非目标层口径) | 两段式 | 关(开关对照消融用) |

统一接受纪律(**四道门**):1 航程门(改后全航程 ≤ 各自剩余航程);
2 迟到门(迟到数与迟到总量逐分量不劣化);3 执行集门(覆盖只增不减);
4 成本门(航程**严格下降** > ε)。全程无随机、逐位可复现;
`sequence_group` 激活时另加两道保守门(时序交叉补齐):
顺序维度算子过**时序门**(候选同链逆序对不得多于基线),跨机改派对
**链成员整体禁迁**,防调序/改派破坏 `sequence_order` 硬约束;
纯成本改进 ⇒ 任意 λ>0 下适应度严格改进,不触碰主适应度阶梯。

口径细节:空任务航次**不计**「机→巢」幻影距离;组内顺序优化只作用于交付解
(解码期最近邻重排已被开关对照否定,`reorder_range_optimize` 默认关)。

### 4.7 搜索期内强化(可选,`polish_in_loop` memetic)

每 `memetic_K` 代对当代精英跑一次从紧预算的 `polish_assignment`,维护 best-polished
档案(**不改基因型**、不扰动 DE 轨迹--RNG-free 不变量有测试守护);交付解取
"收敛后 polish"与"档案最优"的更优者。默认关(exp01 的开关对照中显式打开)。

### 4.8 优化工作的内部分工(三级嵌套消融证据)

为回答「本方法把多少优化工作交给**进化(DE 搜索)**、多少交给**修复链与精修
(polish/定序)**」,做三级嵌套消融(`experiments/ab_optimization_attribution/`,
固定下游、单独关上游量边际贡献):

- **C repair_only**:`max_generations=0`(无 DE 搜索)+ 精修全关 → 修复链裸基线;
- **A evolution**:正常 `max_generations` + 精修全关 → 进化边际 = C − A;
- **B full**:正常 `max_generations` + 精修全开(生产默认) → 精修边际 = A − B。

总改进(相对裸基线) = C − B;进化占比 = (C−A)/(C−B),精修占比 = (A−B)/(C−B)。
修复链地板 C 为可行性必需、不可归零,单独报告。

结论(pop∈{8,12}、gen∈{25,40}、同几何 + 同求解器种子配对,纯几何随机 SRP 实例;
详见 `experiments/ab_optimization_attribution/ab_attr_report.md`;
**机制更正见 §4.9 与 `experiments/ab_mechanism_3steps/SYNTHESIS.md`**):

1. **归属高度体制依赖,非固定比例。**
   - 小批量场景(40:2、20:3,单周期任务少):进化占比 **0%**,全部改进来自精修;
     无论截止宽松(d0,迟到=0)还是过紧不可行(d150,迟到~3.7e4 s)。
   - 大批量可行场景(60:1,d0):该次运行进化占比 **~69%**、精修 **~31%**。
     **但该数值不稳定**:后续用同一 `common.run_rolling` 精确复现参数
     (pop8/gen25/3 seed)仅得进化效应 73.01(非 460.29),pop 改为 10 或只取单
     seed 时效应全为 0(见 §4.9 复现性核查)。故应降级读作「特定初始种群下
     DE 偶尔找到优于初始精英的子代」,**不是稳定的体制规律**。
   - 大批量不可行场景(60:1,d150):截止过紧致全量迟到(不可行),进化回落 **0%**。
2. **为什么进化常常 0%(原「空间坍缩」解释已被证伪)**:本轮逐 trial 诊断
   (`ab_mechanism_3steps` 第一步)显示表型碰撞率 **0**、基因型→表型压缩 **0**,
   每代均产出全新方案与不同适应度——编码/反映射/修复接口**完整传导**了基因差异,
   并非「不同基因被映射到同一可行解」。真正机制是**精英更新停滞**:修复链
   (覆盖优先装载 + 终点巢最小费用流 + 可达即执行)在初始种群阶段就给出近优可行解,
   DE 变异/交叉产出的子代在此表示下系统性劣于初始精英,贪婪选择
   (仅 `child.fitness < population[i].fitness` 才替换)从第 0 代起几乎不采纳
   (冻结代占比 ~0.89、平均改进代数 ~0.2)。因此改进方向应从「改编码/反映射」
   转为「选择压力、初始种群质量、让 DE 能产出优于修复链基线解的变异算子」。
3. **精修算子更强且独立有效**:polish 的 2-opt/Or-opt/relocate 是针对性局部搜索,
   能啃下修复链遗留的路径冗余(40:2 d0:7937→7389,约 7%);2×2 消融进一步显示
   在无进化条件下精修仍独立降低交付适应度,而进化效应在精修开启后大幅萎缩
   (强交互、非可加),见 §4.9。

口径与上限:预算低于生产口径(pop50×gen200);易场景进化 0% 经 gen=80/pop=20、
lamarckian=False 探针与本轮 2×2 双重验证仍为 0%,方向稳健;d150 为不可行截止
体制,其「进化退化」结论不应外推为「任何时间压力都让进化失效」;
每 combo n 偏小(1 几何 × 3 种子配对),且 DE 精英冻结使交付解几乎由初始种群
最优个体决定,**初始种群一变结论即跳变**,故跨配置的占比数字不可作稳定规律引用。

### 4.9 优化器机制三步诊断(`experiments/ab_mechanism_3steps/`)

为把 §4.8 的「进化占比 0%」从现象推进到机制,做三步零侵入诊断(全部为实验侧
monkeypatch,生产代码零改动):

1. **DE 是否真正控制最终解**:hook `DMDESolver._evaluate_result`,逐 trial 记录
   解码前基因表达(`individual.assignment`)与修复链后最终解的
   任务归属 / 段内顺序 / 执行任务集 / 实际参与机 / 适应度。
   结果:250 trial 全为 250 个不同基因型、250 个不同表型、250 个不同适应度,
   **表型碰撞率 0**、每代 10 个全新方案——表示层健康;但 DE 精英总增益仅
   11.62、冻结代占比 0.89、改进代数 0.2。**判定分支落在「检查目标函数与搜索
   策略」而非「改编码 / 反映射 / 修复接口」**。
2. **进化 × 精修 2×2 消融**:四格(无进化无精修 / 无进化有精修 / 有进化无精修 /
   有进化有精修),固定场景、种子、评价口径,分别报告成本 / 迟到 / 覆盖 /
   硬约束违约 / 运行时间。40:2 双截止:无进化时精修独立降交付适应度 364.51;
   进化效应在精修关闭时 202.59、开启后萎缩到 44.52,交互项 158.07 →
   **精修独立有效,DE 的价值主要在无精修时显现,二者强交互、不可加**。
   60:1/d0:精修效应 358.37、进化效应 0、交互 0。
3. **模块拆分**(重点在确认各模块解决什么问题,**不以「每个模块都必须取得收益」
   为判据**):先固定任务归属,离线只测定序(`_best_order`)与路径 polish
   (`_cost_polish_routes`);再端到端测任务归属进化;最后组合。
   - **双基准**:`prod` = `build_solution` 输出(生产解码链已调用
     `_reorder_for_deadlines → _best_order`,即「已消费定序」),其「再叠加定序」
     边际≈0 是口径使然、不是算子失效;`reversed` = 人为逆序劣化基准,才用于测
     定序算子的能力上界。二者分开统计、不混平均。
   - **定序只作用于时间窗维度,且以航程为代价**:d0 零迟到时
     `reorder_range_optimize=False` 早退返回原序;d150 逆序基准下把迟到拉回
     (Δ迟到 为正)却同时增加航程(Δ航程 为负),这是判据
     `(迟到数, 迟到量, 航程)` 字典序优先时间窗的直接体现。
   - **路径 polish 只作用于几何航程维度**:两种基准、两档截止下都稳定压航程
     (如 t20_c1/d0:`prod` 4556.01→3538.34、`reversed` 4976.21→3538.34,
     收敛到同一解、与初始顺序无关),并因几何变短顺带降迟到,
     四道门保证执行集 / 参与机 / 覆盖 / 硬违约不变。
   - **两者互补而非冗余**:`order+polish` 叠加后在 d150 下同时取得最低迟到
     与显著压低的航程。
   - **归属搜索**只在单周期高负载的组合分配难题上显出价值,易场景被修复链
     一次给到近优而冗余;修复链本身是可行性 / 覆盖的地板。

三步实验的综合报告、各步 CSV 与图见
`experiments/ab_mechanism_3steps/SYNTHESIS.md`。

### 4.10 搜索口径与交付口径的对齐性(`selection_gap_report.md`)

§4.9 第 2 步的强交互项(158.07)提示一个比「模块职责不清」更根本的问题:
DE 在搜索期最小化 `f(S_eval(x))`,而最终交付的是 `f(S_final(x))`。两处用的是
**同一个** `problem.scalar_fitness`(`evaluator.py:199` /
`stage_runner.py:346`),故差异**全部来自映射 S**,不是目标函数换了权重:
`S_eval` = `evaluate → build_solution`(`dmde_solver.py:219`),
`S_final` = 重建 → `polish_assignment` → memetic 档案 → `coverage_first` →
交付后成本 polish(`stage_runner.py:344-438`)。

**argmin 相等的充分条件**是存在严格递增 `g` 使 `f_final = g∘f_eval`
(特例:`Δ(x)=f_eval−f_final` 对全体候选为常数)。逐点局部搜索不满足该条件,
故 `argmin_x f(S_eval(x))` 与 `argmin_x f(S_final(x))` **没有对齐保证**。
三点必须补上:

1. 交付后优化**只对胜出者执行一次**,DE 是在「未打磨」的候选集上选 ——
   `select-then-polish` 与 `polish-then-select` 不等价;打磨收益 Δ 依赖候选
   x 的局部结构,必然改写搜索期的序关系。
2. Lamarckian 回写把新基因的 fitness **继承**自旧基因
   (`dmde_solver.py:241-243`),正确性依赖 Q2.1 表型复核;但默认路径
   `_reencode_start_only`(`evaluator.py:267-273`)只改写起始通道、不跑该复核,
   异常还被 `except: pass` 静默吞掉(`dmde_solver.py:244-246`) ——
   属**未加校验的结构性风险**,不是已证实的数值 bug。
3. DE 是**逐代贪心**,`f_final` 意义下的好个体若在早期因 `f_eval` 略差被淘汰
   就永不进入最终候选集 —— 两个 `argmin` 的**定义域本就不同**;且 `S_final`
   在生产链里只在单个点上被求值过,第二个 `argmin` 从未被真正执行。
   这正是「每项收益可独立验证」在现有耦合下做不到的原因。

零侵入探针 `probe_selection_gap.py` 把本次 run **每个被评价过的基因**离线重放
交付映射(D1 重解码 / D2 叠加 `post_cost_polish`,四道门与 ε=1.0 同生产),
实测 top1 重合率、regret、排序反转率、Spearman/Kendall、**并列最优数**与
「选择价值」(相对随机挑一个候选的收益),并以「关掉交付后成本优化总闸」
作隔离对照。关键口径:`regret = f_final(x*_eval) − min_x f_final(x)`,
即现状「先选后打磨」与理论上限「先打磨后选」之差。结论与数值见
`experiments/ab_mechanism_3steps/selection_gap_report.md`。

实测(40:1 单周期、pop8×gen8、3 seeds、d0/d150 各 6 组;
`|f_eval−f_redecode|` 全为 0,重放有效性门槛通过):

- **关掉交付后成本优化总闸**:两体制 Spearman 均 **1.000**、反转率 0、
  top1 重合率 1.000 —— 两口径完全对齐,证明错配由后处理引入而非重解码/随机性。
- **d0(零迟到、纯航程)**:开启后 Spearman 跌到 **0.059**、排序反转率 **0.477**,
  89 个候选里平均 **34.3** 个被 polish 压到同一个 `f_final`(并列最优);
  虽 `regret=0`,但 DE 的「选择价值」(相对随机挑一个候选的收益)从
  **1984.81 塌到 154.72(−92.2%)** —— 不是选错,而是**选择被后处理架空**。
- **d150(强截止、迟到主导)**:开启后 top1 重合率仅 **0.333**、平均 regret
  **49.00**、DE 选中个体在交付口径下平均只排第 **2.7/100** ——
  `argmin_x f(S_eval(x)) ≠ argmin_x f(S_final(x))` **在数值上被直接坐实**。
- `corr(Δ, f_eval)=0.991`:越差的候选被打磨得越多,序关系被系统性压缩;
  `Δ(x)` 非常数,`f_final=g∘f_eval` 充分条件不成立。
- 最终精英的 `f(build_solution(best_assignment)) − best_fitness` 全为 0,
  说明回写不变式在最终精英上成立(结构性风险仍在,见上文第 2 点)。

**论文口径**:不能写「DE 搜索并选择最优方案」;应写「DE 在 `f∘S_eval` 上做
种群搜索并提名精英,交付解经重建与后处理定案;两口径排序一致性由实测给出」,
并把 `best_fitness` / `final_fitness` 并列报告。

---

## 5. 滚动闭环(`methods/rolling/horizon.py`)

每周期:

1. 更新任务池(新到达 ∪ 滞留)与可用机(充电/故障/位置/剩余航程);
2. 调用阶段一 + 阶段二得到交付解;
3. **完成闸门**:物理不可行的任务不顺延完成(解绑回池、下周期重选,完成率不说谎;
   迟到交付仍计完成);
4. 推进仿真时钟;事件触发(UAV 故障 / 突发高优先级任务 / 机巢不可用)本周期重规划;
5. **服务休眠/唤醒**:几何可达但连续 `service_patience` 周期无可用机
   在往返航程内的任务转**休眠**(`DORMANT`:不出池、不进候选,擑 churn)——
   服务可达性是时变条件,异构机队下「连续够不着」≠「永远够不着」,不得出池
   丢失;条件恢复(出现可服务的可用机)即唤醒回 PENDING、年龄累计不中断;
   **几何永久不可达**(`static_unreachable`,按机队最强满电航程判定:最强机也
   够不着)才是唯一出池途径,与休眠严格分账;
6. **收敛停机**(口径约定):主判据为「池空」——到达全部注入后,池内
   不存在「能被完成却没被完成」的任务即自然收敛(`stop_reason=pool_empty`)。
   静态几何不可达已出池(见第 5 条);服务休眠任务留在池中等待条件恢复唤醒,
   池非空时持续推进,不再因「池大小连续不变」提前停(旧稳态口径会把休眠任务锁死)。
   `stable_patience` 仅为兜底:池非空且池内无休眠、连续该数周期零进展才提前停
   (`stop_reason=no_progress`,属非自然收敛,须与 `n_dormant_end` 等单列指标并读;
   剩余为永久不可达/休眠任务,单列分账,不计淘汰)。完成率分母相应排除停机时仍
   休眠的任务(口径 A);自然收敛下该排除项为 0,是恒等操作。

跨周期语义:任务暂缓**不是丢失**--shortfall 非永久淘汰,滞留任务经等待补偿与
防饥饿机制持续获得机会,休眠任务条件恢复即唤醒;机队降落后换电回**该机**满电
航程(`recharge_at_nest`,异构 per-UAV)。带同一时序链的任务在调度层是不可拆
单元,本周期要么整组一起完成、要么整组一起暂缓(见
`methods/rolling/sequence_group.py`)。

---

## 6. 代价模型(`methods/muas/cost/`)

- **三维航程(垂直切面)**:障碍包络(地面高程 + 建筑屋顶)上的地形跟随三维航程(最小离地余量/巡航高度/爬升斜率约束);城市场景(guangzhou)建筑进包络,无建筑场景退化为 DEM 地形口径
  (`vertical_section.py`),不是平面直线;
- 机-任务、任务-任务、任务-巢统一查询接口(`provider.py`);
- **口径唯一**:适应度、ETA/时间窗判定、选择层预筛共用同一距离函数
  (历史教训:判定用直线、飞行用地形 ⇒ 放进来不及的任务白飞);
- 惰性 pairwise 缓存(`lazy_pairwise.py`)按需计算,控内存。

---

## 7. 实验接口与基线(`experiments/exp05_baselines`)

同一滚动外壳、同一任务到达序列、同一约束评价可注入不同第二阶段引擎:
随机可行分配 / 收益代价比贪心插入 / VRPTW 硬时间窗插入 / 本文方法(DMDE),
外加同架构欧氏距离对照(只换代价模型)。**这同时是"求解器可替换"的证据**--
本文贡献在问题形式化与分层框架,DMDE 是该框架的一个求解器实例。

实验台一览(详见 `experiments/README.md`):exp01_smoke(冒烟/回归基线,
3 seed 正式口径)、exp02_selection_scale(选择层规模)、exp03_event_rolling
(事件滚动)、exp04_ablation(消融,含 B1 构造式解码对照设置)、exp05_baselines
(基线对照)、exp06_joint_regime(长程 Poisson 吞吐)、ab_m21_patrol_writeback
(M2-1 巡游序回写开关对照)、ab_optimization_attribution
(进化/修复/精修三级嵌套归属)、ab_order_marginal(基因顺序边际贡献)、
ab_mechanism_3steps(优化器机制三步诊断:基因型→表型 / 进化×精修 2×2 / 模块拆分;
另含 `probe_selection_gap.py` 搜索—交付口径对齐探针,见 §4.10)。

---

## 8. 生产默认口径速览(exp01)

| 项 | 默认 | 备注 |
|----|------|------|
| 适应度 | 覆盖门禁(唯一口径) | `coverage_gate`+`lexicographic_coverage` 开(后者为历史命名,实为覆盖门禁罚层,非字典序口径);F1 只作指标、不进 f |
| λ(成本权重) | 1e-1 | 与 1e-2/1e-4 的历史数据**不可混用** |
| 迟到 | 软约束:迟到只经 `w_violation=0.01/s` 的 `w_late·L` 单一计价 | 迟到计完成、\(F_1\) 计全额奖励且不折减、f 不含奖励项;`W=1800 s` 不进 f,仅用于事后指标 `reward_effective` 的折减、默认关的 `prune_zero_reward`,以及 `check_time_window=true` 消融口径下的选择层时间窗预筛 |
| 飞行时间约束 | `endurance: true`;`UAV.max_time=null` 自动派生(per-UAV max_range/speed) | 与航程对称、逐机判定;显式更小值成独立绑定约束 |
| Lamarckian | M1 起始基因回写开;M2-1 段内巡游序回写**关**(开关对照) | `lamarckian_patrol_writeback` |
| 解码 | `repair_chain` | `constructive` 为 exp04 B1 对照设置 |
| 参与策略 | `demand`(需求感知参与) | 救援池 `fleet`(全机队救援) |
| 剔除 | 全关(`prune_unprofitable=false`) | 与"完成是约束"冲突,仅消融开 |
| polish | assignment + 交付后 2-opt/Or-opt + 跨机改派 均开 | `reorder_range_optimize` 关(开关对照否定) |
| memetic / coverage_first | 关 | 开关对照消融用 |
| DMDE | pop50 × gen200,ζ=3,GMR 原式 | gen 数是预算标定项 |

---

## 9. 模块索引

```text
methods/
  task_pool.py          任务池（到达/滞留/释放/服务休眠 DORMANT 与唤醒）
  uav_state.py          机队状态(位置/航程/巢/可用性)
  utils/                building_layer / coordinates / dem_geotiff(数据与坐标工具)
  selection/            阶段一:priority → feasibility → marginal_gain → capability
  muas/
    config.py / fitness_weights.py / feasibility.py
                        配置、评价权重快照(可独立重算对账)、可行性判据(含飞行时间)
    problem.py          模型、目标分量、scalar_fitness(门禁口径)
    coverage_gate.py    覆盖门禁:一阶检验(航程+_time_ok)/ 审计 / 构造性修复
    cost/               三维地理代价:vertical_section(地形+建筑包络)/ dem_terrain /
                        provider / cost_matrix / nest_keys / lazy_pairwise
    representation/     统一编码 encoder / mapper / inverse_mapper / repair_rules/
    operators/          mutation / crossover / scale_factor / extinction(DMDE 算子)
    constraints/        single_constraints / coop_constraints / evaluator /
                        nest_capacity(终点巢内层 MCF)
    base/               base_optimizer(求解器基类)
    solvers/
      stage_config.py        编排参数 MUASStageConfig / POLICY_*
      participating.py       参与机选择(K 决策)
      tour_cost.py           航次代价 / ETA / 时间窗原语(口径唯一来源)
      decode_repair.py       repair_chain 解码与修复算子组
      decode_constructive.py 构造式解码(exp04 B1 对照设置)
      route_polish.py        交付解成本 polish 算子组(四道门+时序门)
      evaluator.py           MUASAssignmentEvaluator / Lamarckian 回写(M1 起始 / M2-1 巡游序)
      stage_runner.py        run_selective_muas 主入口 / 枚举参照
      dmde_solver.py         DMDE 循环(算法 3.1)
      local_search.py        指派邻域(relocate/swap/position)
      muas_stage.py 兼容门面(拆分,导入路径不变)
  rolling/              horizon.py(滚动闭环)/ event_trigger.py(事件)
```
