# methods/rolling 代码审查（2026-11-03）

**范围:** `task_allocation/methods/rolling/{event_trigger.py, horizon.py}`（exp03_event_rolling 设计前置审查）
**结论:** 架构分层清晰、可用，但**事件机制的两个消融开关与声明语义不符**，且事件是电平触发——直接拿来做 exp03 的「事件响应」与 exp04 的「关停事件机制」消融会得出错误结论。下列 P0 项建议在 exp03 实现前修正。

> **✅ 修复记录（2026-11-03，同日）**：P0-1/P0-2/P1-3/P1-4/P2-5/P2-6 已全部修复
> （补丁 F1–F6，见文末「修复清单」）；P2-7 经 F2 修复（`events.enabled=false`
> 一键全关），E0 噪声风险消除。行为回归：默认路径（`event_replan=true`）合成
> 场景逐位不变（唯一差异：空 `excluded_nest_ids` 不再落盘，纯日志形状）；
> 关停路径（`event_replan=false`）行为改变系 P0-1 修复生效（强制纳入/异巢
> 剔除真关掉），任务集合守恒。测试：`test_event_replan.py` 新增 F2/F3/F4/F6/F5
> 断言（test F–I），全量套件 383 过 / 6 失败均为缺 DEM 数据文件（与本补丁
> 无关）。注：本机无 DEM/建筑数据（`task_allocation/data/guangzhou/` 下
> `dem.tif`、`building/` 未入库），exp01 端到端基线无法在此环境跑，回归以
> 合成场景逐位对比 + 全量单测为准。

---

## 总体评价（先说好的）

- **状态机解耦正确**：`event_trigger` 只识别/判定、不改状态；故障→释放、解→绑定的跨模块流转全部在 `horizon` 显式串联（`_inject_faults` 同时调 `fleet.mark_fault` 与 `pool.release`）。两个状态机互不 import，符合 `docs/design_nest_state.md` 的写点唯一原则。
- **`CycleRecord` 事件字段齐全**：`events` / `replan` / `replan_context` / `released_by_fault` 足以支撑 exp03 的事件日志，不需要动记录结构。
- **归因口径清楚**：几何不可达（`reason="geo"`）与机队服务不可达（`reason="service"`）两把尺子同源（`static_unreachable` / `_update_service_stall` 都用 `uav_max_range` + 同一 `_distance_fn`），单列计数不混账。
- **`_warn_stale_forced_task_ids`** 把「配置面填了 forced 集但被每周期覆盖」的静默失败显式化，符合「回退永不静默」惯例。

---

## P0（exp03 实现前必须修正）

### P0-1 `event_replan=False` 并没有关停事件驱动机制

**声明语义**（`horizon.py` `HorizonConfig.event_replan` 注释 + exp01 `config.yaml`）：
> false → 事件仅记录、不重规划，用于消融对比关停该机制。

**实际行为**：
- `horizon.py:504` `forced_ids = set(replan_ctx.urgent_task_ids) | set(self._prior_fault_released())` —— **无条件**构造，经 `sel_config.forced_task_ids` 传入第一阶段（强制纳入照常发生）；
- `horizon.py:592-593` `stage_cfg = replace(self.stage_cfg, excluded_nest_ids=set(replan_ctx.unavailable_nest_ids))` —— **无条件**剔除不可用机巢；
- 只有 `_rehome_stranded_uavs()`（:457）和记录里的 `replan` 标志真正受 `event_replan` 控制。

即「反应式调整」三件套里两件（强制纳入、异巢剔除）不受开关控制。**exp04 若用 `event_replan=false` 做消融臂，关掉的只是改降与日志标志，机制本体仍在跑**——消融结论无效。

**修法**：把 :504 与 :592 两处的 `replan_ctx` 消费显式挂到同一闸门上，例如
`forced_ids = (…union…) if replan else set()`；`excluded_nest_ids = (… ) if replan else set()`。
（注意保留 `_prior_fault_released` 的语义：故障释放任务的强制纳入也属于事件响应，同门控制。）

### P0-2 `EventTriggerConfig.enabled` 不是「事件检测总开关」

**声明**（exp01 `config.yaml` `events.enabled`）：
> 总开关：false 则完全不做事件检测。

**实际行为**：`detect_events`（`event_trigger.py:90/95/106`）只看 `on_uav_fault` / `on_nest_unavailable` / `on_urgent_task` 三个子开关，**从不检查 `enabled`**；`enabled` 只在 `summarize_events`（:171）与 `should_replan`（:178）里闸 `replan` 标志。`EventReplanContext` 的 `urgent_task_ids` / `unavailable_nest_ids` / `faulted_uav_ids` 也照常收集（叠加 P0-1，这些集合还会照常生效）。

**修法**（二选一，推荐 a）：
- a. `detect_events` 开头 `if not cfg.enabled: return []`——与注释语义一致，且 E0 对照可只关一个键；
- b. 保留现状，改注释为「enabled 只控制是否触发重规划，不控制检测与强制/剔除」——但这样 E0 想要"干净对照"就得逐个关三个 `on_*`，易漏。

---

## P1（exp03 指标正确性依赖）

### P1-3 事件是电平触发，不是边沿触发——`replan` 不能当「重规划次数」用

`detect_events` 从**当前状态**扫描：
- `UAV_FAULT`：UAV 状态 FAULT 是持续态 ⇒ 故障后**每个周期**重复上报（`event_trigger.py:90-93`）；
- `URGENT_TASK`：判据是静态属性 `reward >= reward_threshold` 或临近 `latest` ⇒ 同一任务**每周期**重复上报（:106-118）。

只有 `NEST_UNAVAILABLE` 做了边沿化（`known_unavailable` 参数，:95-104 + `horizon.py:453` 每周期刷新）。

后果：
1. `record.replan` 语义是「本周期存在任一活动事件」，不是「本周期新发生事件」；E1 故障 1 次会让其后所有周期 `replan=True`。
2. `summarize_events` 的 `replan = enabled and len(events)>0` 同理。

**修法**：对称地给 `detect_events` 增加 `known_faulted` / `known_urgent` 边沿集合（与 `known_unavailable` 同款），或至少在 exp03 的 `metrics.py` 里按 events 的**首次出现**计 occurrence，不直接对 `replan` 求和。前者是正解（事件日志本身就该是边沿语义）。

### P1-4 故障释放任务的「强制纳入」只有一周期记忆，退化周期即丢失

`_prior_fault_released()`（`horizon.py:761-765`）只读 `records[-1].released_by_fault`。若故障释放发生在周期 t，则周期 t+1 强制纳入；但 t+1 若走**退化分支**（no_tasks / no_available_uav / selection_empty 提前 return），`records[-1]` 变成退化记录（`released_by_fault=[]`），周期 t+2 起这些任务只剩普通优先级——「故障释放不丢、下周期强制纳入」的承诺被静默削弱。

**修法**：维护 `self._pending_forced: set[int]`，`_inject_faults` 里并入，选择后消费清空（或并入 anti_starvation 由其兜底）。

---

## P2（健壮性 / exp03 注入面）

### P2-5 `_rehome_stranded_uavs` 的空泊位记账可为负

`horizon.py:742` `free[target.id] = free.get(target.id, 0) - 1`：当所有可用巢都满（`with_slot` 空、`pool=alive`）时逐机递减成负数。当前只影响后续选巢排序（`free.get>0` 判据对负数照常 False），无实际错误，但建议 `max(0, …)` 或记账含义改名，避免误读。

### P2-6 机巢不可用**没有注入面**（E3 无法配置级复现）

- UAV 故障有 `HorizonConfig.faults: {cycle: [uav_id]}`；
- 突发任务可走 `arrivals`；
- 机巢闭塞只能改 `Nest.available`——建巢后静态为 `True`，`build.py` 无闭塞参数、`HorizonConfig` 无对应键。exp03 的 E3 因子在现有代码上**跑不起来**。

**修法**：对称新增 `HorizonConfig.nest_closures: dict[int, list[int]]`（周期→巢 id），在 `run_cycle` 周期开始（`detect_events` 之前）置 `nest.available=False` + `meta["close_reason"]`；`_inject_faults` 同款时机即可让 `known_unavailable` 边沿机制正常报事件。

### P2-7 默认 `reward_threshold=8.0` 会让 E0 对照组常年「有事件」

exp01 口径任务收益 ~U[1,10]，约 20%+ 任务 reward≥8 ⇒ 恒判 URGENT_TASK，E0（无事件对照）也会每周期 `replan=True`、强制集常年非空。E0 必须显式 `on_urgent_task: false`（或 P0-2 修好后 `events.enabled: false` 一键全关），否则「对照轨迹」与事件轨迹区分不开。

---

## P3（可选）

### P3-8 `rolling/__init__.py` 为空

包无导出面，调用方直接 import `methods.rolling.horizon` / `event_trigger` 深路径。补一个 `__all__` 便捷导出即可，非必须。

---

## 对 exp03_event_rolling 的直接含义

| # | 影响 | 处置 |
|---|------|------|
| P0-1/2 | E0 对照不干净；机制开/关对照无效 | 实现前修闸门（一行级改动，须配回归：exp01 基线在默认配置下逐位不变） |
| P1-3 | 「重规划次数」指标虚高、事件日志有重复 | 事件边沿化，或 metrics 按首次出现计 |
| P1-4 | E1 的「释放任务下周期强制纳入」验证可能假阴 | `_pending_forced` |
| P2-6 | E3 无注入面 | 新增 `nest_closures`（exp03 的前置代码项） |
| P2-7 | E0 噪声 | 因子配置显式化（写进 exp03 notes.md 因子表） |

详细因子与注入设计见 `task_allocation/experiments/exp03_event_rolling/notes.md`（2026-11-03 设计 v2）。

---

## 修复清单（2026-11-03 落地）

| 补丁 | 问题 | 修法 | 验证 |
|------|------|------|------|
| F1 | P0-1 假开关 | `forced_ids` / `excluded_nest_ids` 挂 `event_replan` 闸门（horizon.run_cycle） | `test_urgent_task_no_replan_when_disabled` + 回归 off 臂行为改变/集合守恒 |
| F2 | P0-2 总开关失灵 | `detect_events` 开头 `if not cfg.enabled: return []` | `test_events_disabled_detects_nothing` |
| F3 | P1-3 电平触发 | `_fresh_events` 边沿去重（主键 task_id/uav_id/nest_id）；`replan` 只在新事件周期为 True；决策上下文仍取电平全集（基线逐位不变） | `test_fresh_events_edge_dedup` + 故障只报一次断言 |
| F4 | P1-4 单周期记忆 | `_pending_forced` 集合：释放即入集、选择消费后清空，退化周期不丢 | `test_fault_busiest_and_pending_forced`（含跨退化周期强制） |
| F5 | P2-6 E3 无注入面 | `HorizonConfig.nest_closures` + `_apply_nest_closures`（周期起点置不可用，对称 faults） | `test_nest_closures_injection` |
| F6 | P2-6 E1 靠运气 | `faults` 支持 `"busiest"` 选择器（绑定任务最多的未故障机，平手取 id 小者） | 同 F4 测试（released_by_fault 非空断言） |
| 附 | P2-5 | `_rehome_stranded_uavs` 空泊位记账 clamp 到 0 | 代码审阅 |

**残留事项**：
- `record.muas.excluded_nest_ids` 仅在非空时落盘（旧：replan 周期落空表）——metrics 无消费，仅日志形状变化。
- exp01 端到端基线（真实 DEM 场景）待有数据环境补跑一次确认（合成场景回归已逐位一致）。
- exp04 消融臂现在可用 `event_replan=false` 干净关停；`events.enabled=false` 为「连检测都关」的更彻底口径（E0 对照用）。
