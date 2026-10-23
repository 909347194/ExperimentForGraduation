# exp05_baselines

**status:** active（P0 + D0 + B3 已实现并接入统一评价；结果待全量网格跑完回填）

**论文锚点:** §3.5.4 整体方法对照实验  
**不负责:** 冒烟正确性（exp01）；选择规模主结论（exp02）；事件主结论（exp03）；内部消融主表（exp04）

---

## 目的

在 **同一场景、同一指标口径、同一 seed 协议** 下，把本文完整方法与
**构造性基线 / 关键设计变体** 对照，回答：

1. 是否明显好于随机/贪心一类朴素策略？（M0 vs B1/B2/B3）
2. 分配层使用 **DEM 三维代价** 相对 **欧氏距离** 是否改变方案与指标？
   （M0 vs D0——同架构对照，只换代价/距离函数）
3. 朴素策略下三维代价是否仍有影响？（B2 vs B2e）
4. 与经典 **VRPTW 启发式**（硬时间窗插入）的差异来自哪里？

**本实验不宣称领域 SOTA 排行榜**；未实现的算法不出现在正式结果表中。

---

## 与其它实验的边界

| 实验 | 关系 |
|------|------|
| exp01 | 场景与指标口径来源；exp01 本身不做性能排名。本实验 config.yaml 的场景/协议与 exp01 生产口径逐项相同，M0@seed42 可与 exp01 对账 |
| exp02 | 选择压缩的规模故事仍以 exp02 为准；此处不重复大 N 扫描 |
| exp04 | 内部开关消融；本实验是 **方法 vs 基线 / 距离模型** |
| `baselines/` | 对比求解器实现地（random / greedy / vrptw，均已实现）；本目录只做编排与结果 |

---

## 对照对象（已实现）

| ID | 方法 | 求解器 | 代价模型 | 说明 |
|----|------|--------|----------|------|
| **M0** | 本文方法（生产配置） | dmde | DEM | 选择 + DMDE-MUAS + 方案 A 终点 + 解码修复链 |
| **D0** | 同架构欧氏对照 | dmde | 欧氏 | **仅替换代价/距离函数**，求解框架不变（建模贡献证据） |
| **B1** | 随机可行分配 | random | DEM | 性能下界；构造期航程门 + 随机落位 |
| **B2** | 收益/代价比贪心插入 | greedy | DEM | 全局最优插入；不卡时间窗（「迟到不是废飞」） |
| **B3** | VRPTW 启发式 | vrptw | DEM | Solomon I1 式插入，**硬时间窗** + 航程 |
| **B2e** | 贪心 + 欧氏 | greedy | 欧氏 | 朴素策略下距离模型是否仍有影响 |

> P2（简化拍卖/CBBA）仍留待后续；论文表格只出现上表已实现 ID。

---

## 公平性协议（对照的有效性边界）

各臂共享（逐位一致）：

1. **同一滚动外壳**：任务池 / 机队状态 / 完成闸门 / 事件 / 收敛早停 /
   第一阶段动态任务选择（`methods/rolling` + `methods/selection`）；
2. **同一任务到达序列**：同 seed 生成的任务实例与到达时刻表，各臂独立
   深拷贝副本（`run.py` 逐臂重建实体），互不污染；
3. **同一硬约束定义与评价**：交付解一律过
   `SelectiveMUASProblem.check_feasible` / `problem.evaluate` 记账，
   metrics 字段与 exp01 逐字段同口径；
4. **同一 seed 协议**：5 个 seed（42–46），报告均值 ± 标准差。

各臂差异**仅**在（二选一）：

- **第二阶段搜索引擎**：DMDE（搜索 + 解码修复链 + polish + 方案 A 终点）
  vs 构造式启发式（一次成型，构造期即硬可行，贪心终点选巢）；
- **代价/距离模型**：DEM 垂直切面三维航程 vs 三维直线（D0 / B2e）。

**必须写明的口径**（写作注意）：

- 基线在**同一滚动外壳**内每周期被调用（非"基线也原生滚动"）；
  第一阶段选择是共享前端——对照回答的是「给定同一候选集，第二阶段
  搜索/建模差异带来什么」，选择层贡献由 exp02 / exp04（M1）回答。
- 构造式基线走**预构建解通道**（`SolverResult.extra["solution"]`）：
  其解不经过本文方法的解码修复链（合并/航程修复/覆盖补全/reorder）与
  polish——基线的构造策略就是其方法本身；唯一共享的是评价函数。
- B3 的硬时间窗是经典 VRPTW 假设，与生产口径「迟到不是废飞、按 L/W
  折减计奖」**故意不同**——两臂的差异正是这一机制差的体现，不是 bug。
- 终点机巢：B1/B2/B3 逐机贪心（容量余量优先、代价最小，无跨机协调）；
  M0/D0 走方案 A（容量约束下最小费用流联合最优）。

---

## 场景与协议（config.yaml）

- **场景 S1**：与 exp01 生产口径逐项相同（真实机巢 CSV + DEM、8 巢 ×
  容量 4、10 机 @ 15 m/s、满电 80 km、任务 30+10+20=60 个、
  时间窗 4.5~9.0 周期、滚动 600 s/周期 + 收敛早停）。
- seeds：`[42, 43, 44, 45, 46]`（实验协议 ≥5）。
- 若全体方法完成率趋同（"比不出差异"），主比较轴转向
  **奖励产出率 / 按时率 / 三维航程**（时间窗与迟到折减天然提供区分度）。

---

## 成功标准

1. **公平:** 上述协议四条成立，run_meta.json 落盘 `distance_definition`。
2. **可跑:** 六臂全部出 `metrics`；随机方法多次运行方差可报告。
3. **方向合理:** M0 在奖励产出率或航程上不低于 B1；相对 B2/B3 至少在
   一主指标上不全面劣势（否则先查实现 bug）。
4. **D0 vs M0:** 至少报告方案差异或航程/完成率差异；若差异极小，正文
   如实写"本实例不敏感"。
5. **文责:** 论文表格只出现已实现 ID；不写未跑的 CBBA 数字。

---

## 主指标（与全局协议对齐）

对照表（`results/summary.csv`）含：

- 有效完成率 / 奖励产出率（迟到按 L/W 折减后）
- 按时率、总迟到（秒）
- 三维航程（米）——**欧氏臂（D0/B2e）为其自身距离定义下的航程，
  run_meta.distance_definition 明示，不与 DEM 臂混加**
- 硬违约与弹回（完成闸门弹回数 + 归因标签 + 巢容量违约 + 周期可行率）
- 运行时间（wall_seconds / solve_time_ms；辅助列，不作主结论）

---

## 实现要点

```text
task_allocation/baselines/
├── construct.py          # 公共构件：航段时间口径 / 构造期航程门 / 贪心选巢
├── random_assign.py      # RandomAssignSolver（random）
├── greedy.py             # GreedySolver（greedy）
├── vrptw.py              # VRPTWInsertionSolver（vrptw）
└── registry.py           # REGISTRY 注册（get_baseline）
task_allocation/methods/
├── muas/solvers/muas_stage.py  # run_selective_muas(..., solver=None) 注入
│                               # + 预构建解通道（extra["solution"]）
└── rolling/horizon.py          # RollingHorizon(..., solver_factory=None)
experiments/exp05_baselines/
├── config.yaml           # exp01 同构场景 + methods: 对照臂
├── config.py / build.py / metrics.py / run.py / paths.py
└── results/
    ├── <ARM>/seed_<s>/{metrics,run_meta,solution}.json
    ├── <ARM>/summary.json      # 跨 seed mean±std
    └── summary.csv             # 论文主表数据源
```

- `run_selective_muas` 的 `solver=None`（默认）= DMDE 主算法，行为逐位
  不变（pytest 220 例全过，含新增 `test_baselines.py` 6 例）；
- 注入求解器若返回预构建解（`extra["solution"]`），交付解跳过解码修复链
  / polish / memetic 档案；否则退回 assignment 解码通道（兼容未来
  "只换搜索引擎"式基线）。

## 运行

```bash
cd task_allocation/experiments/exp05_baselines
python3 run.py                        # 全部臂 × seeds 42–46
python3 run.py --arms M0,B2 --seeds 42
```

---

## 实测结果

待全量网格运行完成后填入（以 `results/summary.csv` 为准）。

---

## 写作注意（§3.5.4）

- 标题用"对照实验"，避免"全面优于现有方法"。
- 先写协议与基线定义，再出表；基线定义必须含"共享滚动外壳 + 同一评价"。
- 欧氏对照是**建模贡献**证据，不是刷榜。
- B1/B2/B3 明确称为构造性基线（constructive baselines）与经典 VRPTW
  启发式；B3 的硬时间窗假设单独说明。

## 下一步

1. （P2）简化拍卖 / CBBA 风格对照：需明确通信假设，勿名不副实。
2. （P1）选择关闭变体归 exp04，大 N 规模归 exp02，本目录不重复。
3. summary 定稿后按 experiments/README.md 的 plot/ 约定补论文插图
   （report/ 层：多 seed 误差棒对照图）。
