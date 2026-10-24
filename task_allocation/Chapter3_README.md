# 第 3 章方法：动态滚动的选择性多机巢多无人机协同任务分配

本文只描述**当前实现中的方法**（与 `task_allocation/methods/` 一致），不写文献综述与贡献口号。

---

## 1. 问题与单周期决策

滚动时域下，每周期 \(t\) 给定：

- 动态任务池 \(\mathcal{T}_t\)（新到达 ∪ 滞留 ∪ 故障释放等）；
- 可用无人机 \(U_t^{\mathrm{avail}}\)（位置、剩余航程、归属巢、是否可起飞）。

单周期要求输出：

1. 本周期进入精细规划的任务集 \(S=\mathcal{T}_t^{\mathrm{sel}}\)；
2. 指派、访问顺序、终点机巢；
3. 可执行航次集合（允许部分 UAV 空闲；允许物理不可行任务不交付）。

决策变量（第二阶段）：

| 符号 | 含义 |
|------|------|
| \(y_j\) | 任务 \(j\) 是否在本周期交付 |
| \(x_{uj}\) | 任务 \(j\) 是否由 UAV \(u\) 执行 |
| \(\pi_u\) | UAV \(u\) 的访问顺序 |
| \(z_{ub}\) | UAV \(u\) 的终点机巢 |

完整成本含**三维 DEM 航段**与**末任务→终点巢返航**。

---

## 2. 总体流程

```text
任务池 + 机队状态
    → 阶段一 selection：得到 S（|S|≤n_sel）
    → 阶段二 MUAS：DMDE 搜索 → 解码修复 → 交付后成本 polish
    → 滚动写回：完成/滞留/机队位置与航程 → 下一周期
```

形式化：

\[
\mathcal{T}_t
\xrightarrow{\text{选择}}
S=\mathcal{T}_t^{\mathrm{sel}}
\xrightarrow{\text{MUAS}}
\mathcal{T}_t^{\mathrm{exec}},\; U_t^{\mathrm{exec}}
\]

- 阶段一：本周期**哪些任务**进入精细规划；
- 阶段二：这些任务**谁执行、何顺序、回何巢**，并在合法覆盖下压缩航程。

代码入口：

| 步骤 | 路径 |
|------|------|
| 选择管道 | `methods/selection/pipeline.py` |
| 单周期 MUAS | `methods/muas/solvers/muas_stage.py` |
| 滚动外壳 | `methods/rolling/horizon.py` |

---

## 3. 阶段一：动态任务选择

### 3.1 规模口径（候选与选择解耦）

- **候选池** \(n_{\mathrm{cand}}=\lceil\alpha K\rceil\)：搜索用，宜偏富；仅受计算上限约束，**不**被 `cap(t)` 直接砍塌。
- **选择数** \(n_{\mathrm{sel}}\)：真实承诺，宜偏紧：

\[
n_{\mathrm{sel}}
=
\mathrm{synthesize}\big(\lceil \mathrm{max\_ratio}\cdot K\rceil,\;\mathrm{cap}(t)\big)
\]

- \(\mathrm{cap}(t)\)：由可起飞子集剩余航程推导的能力上界（充电中的机不计入幻影容量）。
- 约束 \(n_{\mathrm{cand}}\ge n_{\mathrm{sel}}\)。

实现：`selection/capability.py`、`selection/pipeline.py`。

### 3.2 管道步骤

1. **优先级排序**（`priority.py`）：收益、紧迫度、等待、空间协同、粗代价等归一化综合分。
2. **预筛选**（`feasibility.py`）：物理向拒绝（如往返超航程等）；迟到不作为参选资格否决。
3. **边际纳入**（`marginal_gain.py`）：按 \(\Delta B/(\Delta C+\varepsilon)\) 一类准则向选择集追加，直至触达 \(n_{\mathrm{sel}}\)。
4. **防饥饿 / 事件强制纳入**：紧急或故障释放任务可绕过候选上限，终刀仍受可行性约束。

输出：本周期已选集 \(S\)，\(|S|\le n_{\mathrm{sel}}\)。阶段一**不**锁定最终指派与顺序。

---

## 4. 阶段二：选择性多机巢 MUAS

输入：\(S\)、当前机队、三维代价接口。  
输出：各 UAV 航次（任务序列 + 终点巢）及诊断量。

### 4.1 求解结构

```text
编码种群
  → DMDE 代数循环（差分 + 代价映射/逆映射 + 适应度选择）
  → 精英个体解码：build_solution / 修复链
  →（可选）覆盖门禁修复与审计
  → 交付后成本 polish：组内重排 + 跨机改派
  → 交付解
```

| 组件 | 路径 |
|------|------|
| 问题与标量适应度 | `muas/problem.py` |
| 覆盖门禁 | `muas/coverage_gate.py` |
| 统一编码 / 映射 / 逆映射 | `muas/representation/` |
| DMDE 主循环 | `muas/solvers/dmde_solver.py` |
| 解码、修复、polish 编排 | `muas/solvers/muas_stage.py` |
| 指派邻域（relocate/swap/position） | `muas/solvers/local_search.py` |
| DEM 三维代价 | `muas/cost/` |

### 4.2 编码与映射

- **统一编码**：起始基因（任务→优先 UAV 倾向）+ 巡游基因（访问顺序相关连续量）；基因规模与任务数绑定，**终点巢不进基因**。
- **映射 / 逆映射**：在飞行代价空间做差分，再按匹配规则映回离散 \((u,j)\) 关系（与 Ming 等 2017 离散映射 DE 同族）。
- **Lamarckian**：修复/局部改进回写约定通道，保持段结构可解码。

### 4.3 解码与修复

默认 `decode_mode=repair_chain`（另有 `constructive` 构造式臂供对照）：

1. 由编码展开指派与顺序；
2. 航程超限修复、合并/空闲处理、终点巢分配；
3. 时间相关重排与零奖励裁剪等（按配置开关）；
4. 评价前与硬约束、路径口径对齐（含返航）。

**终点机巢**：路线给定后，在容量约束下用内层分配选终点；**返航代价进入完整 path**，不在评价外事后补巢。

### 4.4 覆盖意图与适应度

**业务意图（单周期）：** 在硬约束下尽量交付已选可装任务；再比较航程与软迟到。

实现上并存两套可切换口径（由配置开关控制）：

**A. 四层准字典序（含完成项，兼容/常用）**

\[
\begin{align*}
f &= W_{\mathrm{hard}}\,n_{\mathrm{hard}} + w_{\mathrm{exc}}\,E_{\mathrm{m}} \\
&\quad - m\cdot F_{1} \\
&\quad + w_{\mathrm{late}}\,L_{\mathrm{tot}} \\
&\quad + \lambda\cdot C_{\mathrm{path}} + w_{\mathrm{time}}\,T_{\max}
\end{align*}
\]

- T0：物理硬违约支配；
- T1：完成/收益项（\(m\) 为支配权重）；
- T2/T3：迟到与航程。

**B. 覆盖门禁口径（`coverage_gate` / `lexicographic_coverage`）**

- 一阶检验：空闲机开班或向现有航次插入；
- `voluntary_skip`：已选且一阶可装却未装 → 非法；
- `capacity_shortfall`：一阶不可装 → 允许不交付并记账（**一阶完备，非全局最大覆盖证明**）；
- 搜索用 \(f\) 可去掉 \(-m F_1\)，由 \(W_{\mathrm{skip}} n_{\mathrm{skip}}\) 与修复承担覆盖；\(F_1\) 作指标。

门禁与主实验默认开关以各 `experiments/*/config` 为准；能力以 `coverage_gate.py` 与 `problem.scalar_fitness` 分支为准。

### 4.5 交付后成本 polish

DMDE **收敛之后**，对交付解做局部搜索（不重新开种群进化）：

| 算子 | 作用 |
|------|------|
| 组内 2-opt / Or-opt | 单机访问顺序 |
| 跨机 relocate | 任务改派到其它机（含空闲机） |

接受条件（同时）：执行任务集不减少；硬违约不增加；迟到不劣化；总航程**严格下降**。

短航次上组内顺序常已局部最优；**跨机改派**仍可能明显降航程。  
`polish_assignment`（relocate/swap/position）可在评价链上使用；`polish_in_loop` 为搜索期内精英 memetic，按实验配置。

闲置航次：空任务航次**不计**「机→巢」幻影距离。

---

## 5. 滚动闭环

`rolling/horizon.py`：

1. 更新任务池与可用机；
2. 调用阶段一 + 阶段二；
3. 按完成闸门确认交付；未完成且仍合法的任务留池；
4. 推进仿真时钟；可选事件触发重规划；
5. 可选稳态早停。

阶段一只决定本周期 \(S\)；跨周期去向由池动态、名额与防饥饿共同决定。

---

## 6. 代价模型

- DEM 三维航程（`muas/cost/`）；
- 机–任务、任务–任务、任务–巢统一查询；
- 可用惰性 pairwise（`lazy_pairwise.py`）按需缓存。

---

## 7. 基线接口

同一滚动外壳可注入随机 / 贪心插入 / VRPTW 插入 / 本方法（`exp05_baselines`）。固定到达序列与硬约束，只换第二阶段引擎。

---

## 8. 模块索引

```text
methods/
  selection/          阶段一
  muas/
    problem.py        模型与 scalar_fitness
    coverage_gate.py  覆盖门禁
    cost/             DEM 三维代价
    representation/   编码与映射
    constraints/      硬约束
    solvers/
      dmde_solver.py  DE 循环
      muas_stage.py   单周期编排
      local_search.py 指派邻域
  rolling/            滚动与事件
  task_pool.py
  uav_state.py
```
