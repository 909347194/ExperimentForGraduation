# exp03_event_rolling

**status:** design-v2 + 前置修正已落地（2026-11-03：F1–F6 补丁完成并回归；`run.py` / `config.yaml` 待实现）
**论文锚点:** §3.5.6 动态滚动场景下的适应性分析  
**不负责:** 算法性能排名、选择层规模扫、完整基线竞赛（见 exp02 / exp05）

---

## 目的

在 **多周期滚动** 框架下，验证方法对 **动态事件** 的响应是否符合问题定义，而不是只跑“平滑到达、无故障”的理想轨迹。

具体回答：

1. 任务池是否按 \(\mathcal{T}_t = \mathrm{remain}\cup\mathrm{new}\cup\mathrm{release}\) 演化，且 \(\mathcal{T}_t^{\mathrm{exec}}\subseteq\mathcal{T}_t^{\mathrm{sel}}\subseteq\mathcal{T}_t\) 始终成立？
2. 注入 UAV 故障 / 突发高优先级任务 / 机巢不可用后，是否触发重规划或强制纳入，且释放任务不丢失？
3. 滚动过程中完成率、滞留年龄、按时率、硬违约是否保持可解释的变化（而非偶然单次成功）？

---

## 与其它实验的边界

| 实验 | 关系 |
|------|------|
| exp01_smoke | 提供闭环与指标口径；本实验在其滚动骨架上 **加事件注入与对照** |
| exp02_selection_scale | 只关心 \(\|T\|\gg K\) 选择压缩；本实验不扫 N |
| exp04_ablation | 单因素机制消融；本实验固定生产方法，变的是 **环境事件** |
| exp05_baselines | 外部对照算法；本实验默认只用本文方法 |

---

## 机制现状盘点（2026-11-03，与 methods/rolling 代码对齐）

事件三件套在代码里的识别 → 注入面 → 状态流转 → 记录字段（**注入面**即「实验怎么把事件配出来」，是本实验设计的核心接口）：

| 事件 | 识别（event_trigger） | 注入面（现状） | 状态流转（horizon） | 记录字段 |
|------|----------------------|---------------|---------------------|----------|
| E1 UAV 故障 | `UAV_FAULT`（⚠️ 电平触发，见下） | `HorizonConfig.faults: {周期: [uav_id]}`，执行中注入 | `fleet.mark_fault` → `pool.release(reason="uav_fault")` | `record.released_by_fault` |
| E2 突发高优先 | `URGENT_TASK`（⚠️ 电平触发） | 无专用面；可经 `arrivals` 注入特制任务（reward / deadline） | `forced_task_ids` 绕过 Top-αK 预筛 | `record.selection.forced_task_ids` |
| E3 机巢不可用 | `NEST_UNAVAILABLE`（✅ 边沿触发，`known_unavailable`） | **缺口：无注入面**（`Nest.available` 建后静态） | `excluded_nest_ids` 剔除终点候选 + `_rehome_stranded_uavs` 改降 | `record.events` / `record.muas.excluded_nest_ids` |

**代码审查结论**（详见 `task_allocation/docs/rolling_review_2026-11-03.md`）：
P0-1 `event_replan=false` 未真正关停强制纳入/异巢剔除；P0-2 `events.enabled` 不闸检测；P1-3 电平触发使 `replan` 不可直接计「重规划次数」；P1-4 故障释放的强制纳入只有一周期记忆；P2-6 E3 无注入面。**已全部修复（2026-11-03，见下表）**。

---

## 前置代码修正（F1–F6，✅ 2026-11-03 已落地）

| # | 改动 | 位置 | 状态 |
|---|------|------|------|
| F1 | `forced_ids` / `excluded_nest_ids` 挂上 `replan` 闸门（与声明语义一致） | `horizon.py` run_cycle | ✅ + test C |
| F2 | `detect_events` 开头 `if not cfg.enabled: return []` | `event_trigger.py` | ✅ + test F |
| F3 | 事件边沿化：`_fresh_events`（task_id/uav_id/nest_id 主键去重）；`replan` = 本期新事件，可计「重规划次数」 | `horizon.py` | ✅ + test G/H |
| F4 | `_pending_forced` 集合承接故障释放任务（退化周期不丢强制） | `horizon.py` | ✅ + test H |
| F5 | `HorizonConfig.nest_closures: {周期: [nest_id]}` 注入面（周期初置 `available=False` + `meta.close_reason`） | `horizon.py` | ✅ + test I |
| F6 | `faults` 支持 `"busiest"` 选择器（注入时选绑定任务最多的未故障机；id 字面量兼容保留） | `horizon.py._resolve_fault_target` | ✅ + test H |

回归证据：默认路径（`event_replan=true`）合成场景逐位不变；关停路径行为改变系 F1 生效（任务集合守恒）；全量单测 383 过。

---

## 因子设计（v2 具体化）

### 场景底座（全因子统一，口径对齐 exp01）

- 场景：`guangzhou`（3 巢 ×2 泊位、6 机、`uav_max_range=10000`、`uav_speed=15`、DEM+建筑垂直切面）。
- 到达流：**持续到达**（本实验要「多周期池非空」，不能用 exp01 的 fixed-30 早停口径）——`arrivals.mode=poisson, lambda=3.0`，`n_cycles=20`（安全上限），`cycle_length=600`，**`stop_when_stable.enabled=false`**（到达贯穿全程，早停无意义）。
- 时间窗：`deadline_cycles_min/max = 2.0/4.0`（沿用 exp01 标定）。
- 生产方法全开（selection / muas / solver 沿用 exp01 `config.yaml` 口径，含 `event_replan=true`、`anti_starvation=true`）。
- seeds：调试 `[42]`；正式 `[42,43,44]`（结论加 `[45,46]` 补 5 seed）。
- 事件注入周期统一取 **t=6**（运行中段：此前有基线轨迹、此后观察恢复/滞留），组合因子 E4 取 t=6 与 t=10 双注入。

### 事件因子（一次一类；E4 组合只作补充）

| 因子 | 注入配置（config 级） | 期望行为（可断言） | 指标方向预期 |
|------|----------------------|-------------------|--------------|
| E0 无事件 | `events.enabled: false`（F2 后一键全关；**若 F2 未落必须显式 `on_urgent_task: false`**，否则默认 `reward_threshold=8` 使 ~20% 任务恒判紧急、对照组常年 replan）、`faults: {}`、`nest_closures: {}` | `record.events` 全空、`replan` 恒 false | 基准曲线 |
| E1 UAV 故障 | `events.schedule.faults: [{cycle: 6, uav: busiest}]` | 周期 6：`released_by_fault>0`（F6 保证）；周期 7：释放任务进 `forced_task_ids`（F4）；可用机数阶跃 −1 | 完成量短降、滞留年龄升、重规划 +1；硬违约为 0 或仅资源性微升 |
| E2 突发高优先 | `events.schedule.burst_tasks: [{cycle: 6, n: 2, reward: 9.5, deadline_cycles: 1.0}]`（build 合入 `arrivals[6]`） | 周期 6：burst 任务触发 `URGENT_TASK` 边沿、进 `forced_task_ids`；高概率当期或下期完成 | 该任务等待 ≤2 周期；不影响其它任务完成守恒 |
| E3 机巢不可用 | `events.schedule.nest_closures: [{cycle: 6, nest_id: 0}]` | 周期 6：一次 `NEST_UNAVAILABLE` 边沿；`excluded_nest_ids` 含巢 0；在场/在途 UAV 改降他巢（`_rehome_stranded_uavs`）；此后 `nest_occupancy[0]=0` | 终点变更次数 ≥1；航程均值升（改降绕行）；完成量短降 |
| E4 组合 | E1@6 + E3@10（错峰，便于分因归因） | 两事件各自满足 E1/E3 断言 | 压力轨迹；只作补充证据 |

> 注入强度分级原则：先跑单事件弱注入（1 架故障 / 1 巢闭塞 / 2 任务突发），确认协议行为后再考虑强注入；E1 一次只故障 1 架（占机队 1/6），避免大面积不可行。

### 事件时刻表 schema（`config.yaml` 草案）

事件自变量集中在 `events.schedule` 一段（build.py 翻译成 `HorizonConfig.faults` / `nest_closures` + 合入 `arrivals`；`rolling.faults` 旧键继续兼容）：

```yaml
rolling:                # 沿用 exp01 口径，仅列本实验差异项
    n_cycles: 20
    stop_when_stable: {enabled: false}
    arrivals: {mode: poisson, lambda: 3.0}
    event_replan: true
events:
    enabled: true
    on_uav_fault: true
    on_nest_unavailable: true
    on_urgent_task: true
    reward_threshold: 8.0     # E2 语义边界：突发任务 reward=9.5 > 阈值
    slack_threshold: 900.0
    schedule:                 # 因子注入（run.py --factor 覆盖此段）
        faults: []            # [{cycle: 6, uav: busiest | 0}]
        burst_tasks: []       # [{cycle: 6, n: 2, reward: 9.5, deadline_cycles: 1.0}]
        nest_closures: []     # [{cycle: 6, nest_id: 0}]
```

单因子矩阵由 `run.py --factor E0|E1|E2|E3|E4` 展开 schedule 段，**实际生效值落 `effective_config.json`**（复现要求）。

---

## 指标 schema（与全局口径一致 + 事件增量）

| 类 | 指标 | 来源 |
|----|------|------|
| 效果 | 有效完成率、奖励产出率、周期完成量 | exp01 metrics 口径 |
| 时间 | 按时率、总迟到、平均/最大等待（滞留年龄） | exp01 metrics 口径 |
| 动态 | 每周期 \(\|remain\|,\|new\|,\|release\|\)、`overdue_ids` | `record.pool_counts` / `overdue_ids` |
| 可行 | 硬违约、执行弹回（`infeasible_ids`+`infeasible_tags`） | exp01 metrics 口径 |
| **事件（新增）** | `event_occurrences`：按类型计**边沿**次数（F3 后 = `record.events` 计数）；`n_replan_cycles`；`n_fault_injected` / `n_released_by_fault`；`n_forced_in`（forced 去重数）；`n_nest_closures` / `n_end_nest_changed`（终点机巢变更次数，由 `solution.json` 相邻周期 end_nest 对比）；`response_latency`（注入周期 → 对应 forced/excluded 生效周期，期望 = 1） | exp03 `metrics.py` 新增 |

产物按 `experiments/README.md` 四件套：`metrics.json`（上表数值）、`solution.json`（tours/事件结构）、`run_meta.json`、`effective_config.json`；事件时间表必须进 `effective_config.json`。

---

## 成功标准

1. **结构:** 全种子、全周期 `exec ⊆ sel ⊆ pool`；release 任务出现在后续 \(\mathcal{T}_t\)（释放守恒：到达 = 完成 + 池内 + 不可达，复用 exp01 `audit_correctness` 口径）；无静默丢任务。
2. **事件响应:** E1/E2/E3 断言表逐项过（检测 → 状态流转 → 记录字段三段闭环）；`response_latency=1`。
3. **对照:** E0 与 E1/E3 在同 seed 上可对比：可用机数、完成量、滞留分布、重规划次数有差异且方向合理（上表「指标方向预期」）。
4. **可行:** 硬违约不因事件注入而系统性爆炸；若升高，需在 notes 记录原因（资源变紧属预期）。
5. **可复现:** 事件时刻表与 seed 固定进 `config.yaml` + `effective_config.json`；`results/` 含 `metrics.json`、`run_meta.json`、事件摘要。

---

## 建议目录（实现时）

```text
exp03_event_rolling/
├── notes.md          # 本文件
├── config.yaml       # 场景 + 事件时刻表（events.schedule）+ seeds
├── run.py            # 编排：--factor E0..E4 × seed → 落盘
├── config.py / build.py / metrics.py   # 从 exp01 裁剪（独立性原则：允许重复）
│                     # build.py 增：burst_tasks 生成 + schedule→HorizonConfig 翻译
├── audit_events.py   # 事件协议断言（成功标准 1/2 的机器检查）
└── results/
    ├── E0_seed42/…
    ├── E1_seed42/…
    └── summary.json  # 跨因子汇总表
```

---

## 运行（实现后）

```bash
python task_allocation/experiments/exp03_event_rolling/run.py --factor E0
python task_allocation/experiments/exp03_event_rolling/run.py --factor E1 --seeds 42,43,44
python task_allocation/experiments/exp03_event_rolling/run.py --dry-run   # 只验生效配置
```

---

## 实现清单（有序）

1. ~~**前置修正 F1–F6**~~（✅ 2026-11-03 完成，含回归与新增测试 F–I）。
2. `config.py`：从 exp01 裁剪 + `events.schedule` 解析；`build.py`：burst 任务生成、schedule → `HorizonConfig` 翻译、`"busiest"` 解析留 horizon。
3. `run.py`：因子 × seed 编排、进度回报、四件套落盘。
4. `metrics.py`：事件增量指标 + 跨因子 `summary.json`。
5. `audit_events.py`：结构不变量 + 事件断言表（成功标准 1/2 自动化）。
6. 最小闭环先跑 E0/E1/E3 单 seed → 再全因子多 seed → 论文表（§3.5.6 同图给 E0 曲线与事件曲线）。

---

## 已知风险 / 写作注意

- 事件过强会导致大面积不可行，应 **分级注入**，先弱后强。
- 论文 §3.5.6 应同时给 E0 曲线与事件曲线，避免只展示无扰动理想情况。
- 本实验 **不能** 替代 exp05 的算法对比结论。
- **E0 必须「无事件」干净**：P2-7（默认 `reward_threshold=8` 使约 1/5 任务恒判紧急）未修前显式关 `on_urgent_task`，否则对照组与事件组区分不开。
- **`replan` 计数口径**：F3 落地前不可直接对 `record.replan` 求和当「重规划次数」（电平触发虚高）；论文取数等 F3。
- burst 任务的 `deadline_cycles=1.0` 比常规窗口紧一倍——是「紧急」的语义来源，写作时声明这是刻意构造，不代表常规任务分布。
