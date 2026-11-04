# 修改计划：贡献措辞修正 + 异构 per-UAV 化 + 静态代价口径声明（2026-11-04）

> **执行状态（2026-11-04 晚）**：WP-B ✅ 完成（33255e9）、WP-C ✅ 完成（33255e9）、
> 测试 ✅ 完成（a8bad17：异构用例 7 项 + 全量回归 338 过 + 同构 A/B 逐位对账）。
> WP-A（LaTeX）由用户自行修改；WP-D（实验）待启动。
> 细节回看：`Chapter3_README.md`（已同步 2026-11-04 口径）、`tests/test_heterogeneous_fleet.py`。

**触发**：三条贡献 \item 与实现对照审查。裁定：
1. 贡献 3 采用方案 A（改声明，不做增量重分配）；
2. 论文主张异构平台（航程、续航时间可不同）→ geo/service 判定、B(t)、续航 per-UAV 化；
3. 任务分配代价为规划期静态估计；与下层路径规划的代价反馈**不声称实现**（参考 Lu 等 2026, Vehicular Communications 60:101031）。

**现状盘点**（对照基准）：
- `uav_max_range`（全局）波及 19 个 py 文件；`UAV.max_time` 已是 per-UAV 字段但自动派生/部分判据仍走全局；
- `UAV` 无 `max_range` 字段，换电恢复到全局值（`horizon.py` recharge_at_nest）；
- geo 判定 `selection/feasibility.py::static_unreachable`、service 判定 `horizon.py::_update_service_stall` 均用全局 `uav_max_range`；
- capability `selection/capability.py::fleet_range_budget` 已按 per-UAV `remaining_range` 求和（B(t) 本身是对的，口径表述需对齐异构）；
- service 出池为永久移出（`task_pool.mark_unreachable`），与"持续获得调度机会"矛盾 → 休眠态改造。

---

## WP-A 论文声明类（先行，约 1 天）**【用户自行修改 LaTeX】**

| # | 内容 | 目标文件 |
|---|------|----------|
| A1 | 贡献 3 \item 按方案 A 替换（定稿见下） | `paper/manuscript1_journal_article/sections/s01_introduction.tex:101` |
| A2 | 贡献 1 \item 收紧（承诺规模上限、出池两类限定、异构口径） | 同上 :99 |
| A3 | 贡献 2 微调："条件最优"补"给定路线与静态代价口径"限定 | 同上 :100 |
| A4 | ch3 建模假设表加两行：**异构平台**（航程/续航可不同）、**静态代价估计**（DEM 三维航程，规划期静态） | `s03_task_allocation.tex` |
| A5 | ch3"本章研究内容与贡献定位"加**最优性边界声明**段（分层理由/条件最优/暂缓语义三层） | 同上 |
| A6 | 相关工作：Lu 2026 对比定位（joint energy-aware vs 本文分层滚动选择性）；对比表注释核对 | `s01_introduction.tex` |
| A7 | 静态代价口径 + 跨层代价反馈边界声明（引 Lu 2026，列为展望，**不声称实现**） | `s03` + `s01` |

### A1 定稿（贡献 3，方案 A）

> \item 针对事件驱动重规划常从零重建全局方案、连续性差且开销大的问题，本文以固定周期滚动吸收任务到达、平台故障与机巢状态变化：每周期仅对未承诺任务集（滞留∪新达∪故障释放）重规划，已承诺任务当周期执行不回炉，承诺集规模受能力上界约束，使单周期重规划规模与机队能力同阶而非随任务池增长；未完成任务回池滞留并持续获得调度机会，滚动状态（位置、剩余航程、机巢归属）回写能力上界与选择优先级，使选择、分配与机巢决策形成闭环，在保持状态与任务连续性的同时控制每周期求解规模。

### A2 方向（贡献 1 措辞要点）

- "能力上界" → **"承诺规模上限"**（cap(t)=⌊B(t)/c̃⌋ 明确定义为承诺规模操作性上限，不称严格上界）；
- "持续获得调度机会" → 加限定：服务不可达者休眠挂起、条件恢复即唤醒（几何不可达单列归因出池）；
- B(t) 表述改为"可起飞子集剩余航程之和"（天然 per-UAV，异构下语义更强）。

### A7 边界声明要点（不声称实现）

- 任务分配的代价为**规划期静态估计**（DEM 垂直切面三维航程），同层口径唯一（选择/评价/ETA 同尺）；
- 真实飞行代价与静态估计存在偏差，跨层代价回填（如 Lu 等 2026 的任务-路径联合能量优化/反馈更新）可能改变可达性判定；该机制属跨层闭环范畴，**不在本文声称范围**，列为后续工作；
- 与贡献 2"条件最优"一致：条件最优 = 给定路线、给定静态代价口径下的最优。

### 验收（WP-A）

s01/s03 全文无"部分重分配""无限定的持续获得调度机会""严格上界""联合最优"类越界表述；口径词（承诺规模上限/条件最优/静态代价）全文一致。

---

## WP-B 异构 per-UAV 化（核心工程，约 3–4 天）**【✅ 完成 33255e9 + a8bad17】**

| # | 内容 | 涉及文件 |
|---|------|----------|
| B1 | 数据模型：`UAV` 增 `max_range: float \| None`（None=继承全局，同构回退）；`max_time` 自动派生改 per-UAV（max_range/speed）；换电恢复到 `u.max_range` | `common/types.py`, `methods/uav_state.py`, `methods/rolling/horizon.py` |
| B2 | 全局→per-UAV 替换面（19 文件）：`uav_max_range` 保留为**缺省值**，既有 yaml/实验零破坏 | `common/task_scenario.py`, `experiments/exp01|exp02|exp05|ab_m21` 各 run/config/build/metrics, `methods/rolling/horizon.py` |
| B3 | geo 判定分两层：**永久性判定用机队最强**（max over fleet max_range，防短机误杀长机可服务任务）；预筛改 per-(task,uav) 可达（∃可达机即保留） | `methods/selection/feasibility.py`, `methods/rolling/horizon.py` |
| B4 | service 判据改 per-UAV：∃可用机 u 满足 2·c_u ≤ range_u（cost_hint 改 min over per-UAV 可达） | `methods/rolling/horizon.py`, `methods/selection/` |
| B5 | capability：`fleet_range_budget` 已 per-UAV，核对 c̃、n_sel、`effective_cap` 口径；续航绑定约束 per-UAV | `methods/selection/capability.py` |
| B6 | 评价/修复链口径统一：`tour_cost`、`_time_ok`、`_repair_range`、`coverage_gate`、`constraints/single_constraints` 的航程/续航判据 per-UAV | `methods/muas/`（config.py, feasibility.py, coverage_gate.py, solvers/decode_repair.py, solvers/tour_cost.py, constraints/） |
| B7 | 测试：异构用例（混合航程 geo 用 max 判定、短机不误杀、per-UAV 续航拦截、B(t) 部分可用）；既有测试全绿 | `tests/test_unreachable_screening.py`, `test_endurance_constraint.py`, `test_capability_scale.py` + 新增 |

### 设计关键点（写进 design 文档，防踩坑）

1. **geo 永久判定必须用机队最大航程**——任务位置/巢位静态 + 换电回满 ⇒ "永久不可达"只在**最强机也够不着**时成立；用单机/短机判定会在异构下误杀。
2. 全局 `uav_max_range` 语义降级为"缺省满电航程"，同构 = 全部继承缺省；19 文件改动全部走"per-UAV 字段优先、缺省回退"模式。
3. 续航：`max_time` 字段已 per-UAV，只需把自动派生与各判据从全局 `uav_max_time` 切到 per-UAV。

### 验收（WP-B）

全部测试通过；同构配置下 exp01 3-seed 输出与改前一致（回归对账）；异构冒烟（长短航程混合）跑通且行为符合设计点 1。

---

## WP-C service 休眠态（约 1 天，与 B3/B4 同批）**【✅ 完成 33255e9】**

| # | 内容 | 涉及文件 |
|---|------|----------|
| C1 | `TaskStatus.DORMANT` + `mark_dormant`/`wake`；休眠不进候选、不入完成率分母（三分账扩展） | `methods/task_pool.py`, `common/types.py` |
| C2 | `_update_service_stall` 改休眠/唤醒（每周期 cost_hint 复查，可服务即唤醒、年龄累计不中断）；稳态早停改"可服务池稳定" | `methods/rolling/horizon.py` |
| C3 | 记录/归因字段：n_dormant、wakeup 计数、休眠年龄 | `horizon.py` record |
| C4 | 测试：休眠→唤醒→完成全链路、防饥饿边界 | `tests/test_dormant_wake.py`（新）, `test_anti_starvation.py` |

### 验收（WP-C）

"暂缓任务回池保留、持续获得调度机会"字面成立；churn 不复现（休眠任务不进 stage1）。

---

## WP-D 实验补齐（约 2–3 天）**【待启动】**

| # | 内容 | 支撑贡献 | 状态 |
|---|------|----------|------|
| D1 | exp03_event_rolling：run.py/config.yaml 落地 + E3 机巢不可用注入面（Nest.available 动态化） | 贡献 3 | planned |
| D2 | exp05 终点巢策略臂：固定返原巢/最近合法巢/贪心改派/MCF 最优 | 贡献 2 | 缺 |
| D3 | 异构域扫描（异构度 = 航程极差比 → 完成率/选择行为/休眠率） | 异构主张 | 新增（依赖 WP-B） |
| D4 | 同构回归对账（exp01 3-seed vs 历史数据） | 全部 | 依赖 WP-B |

依赖：D1/D2 不依赖异构，可与 WP-B 并行；D3/D4 在 WP-B 后。

---

## 顺序与总量

```text
WP-A（1 天，立即）──→ WP-B（3–4 天）──→ D3/D4
        │                ↕ 同批
        │              WP-C（1 天）
        └──→ D1/D2（可并行）
```

总计约 **1.5–2 周**（单人节奏）。风险点：①geo 判定口径（设计点 1）；②全局缺省回退的回归风险（用测试锁）；③本篇"不声称代价反馈"与 `closed_loop/` 既有代码的口径协调——若学位论文第 4/5 章涉及闭环机制，两稿表述需对齐"声称范围"边界，避免自相矛盾。
