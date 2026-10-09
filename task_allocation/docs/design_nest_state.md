# 机巢状态与容量更新设计（design_nest_state.md）

> 对应 `Chapter3_README.md` §1(3) 多机巢起降与终点机巢选择、§4.3 机巢容量或可用性约束、
> §6 事件驱动重规划（机巢不可用）。
>
> 本文解决的问题：**机巢自身的状态如何定义、容量何时校验、归属如何更新**，
> 以及在滚动时域下如何避免容量漂移与超卖。

---

## 0. 三条设计原则

1. **机巢只存静态属性 + 可用性**，不存占用计数。
2. **"占用"从 `uav.nest_id` 推导**，不落第二份状态。
3. **`nest_id` 的唯一写点是分配决策**（`z_ub` 落地），起飞/降落一律不改。

做到这三条，容量就不会漂、不会超卖。

---

## 1. 背景：容易做错的两个点

### 1.1 按"返回架数"校验 → 误杀可行解

「机巢可用容量 2，本次分配 3 架返回」——若其中 1 架本来就是从该巢起飞的，
它起飞时已经让出了名额，净增只有 2，**放得下**。只比较 `arrivals` 与 `free_slots`
会误判为超容。

### 1.2 "降落时才占名额" → 滚动时域超卖

```
周期 t    UAV#1 分配返回机巢 b（尚需 5 分钟降落）
          若此刻不占名额，b 的可用容量未变
周期 t+1  重新规划，看到 b 仍有空位
          → 又分配 UAV#2 降落 b
结果      两架都要降，但 b 只有一个空位
```

降落时间不确定（执行偏差、故障、天气），**已分配未降落的 UAV 必须已经占住名额**。
校验与占用必须发生在同一时刻。

---

## 2. 属性分层

| 层 | 字段 | 来源 | 变不变 |
|---|---|---|---|
| **静态** | `id` | `data/nest_location_data.csv` 行序 | 永不变 |
| **静态** | `x` / `y` / `z` | `data/nest_location_data.csv` + DEM 采样 | 永不变 |
| **静态** | `capacity` | `config.yaml` → `problem.nest_capacity` | 一次实验内不变 |
| **动态** | `available` | 运行事件（故障 / 维护 / 管控） | 随事件变 |
| **派生** | `occupied` | `\|{u : u.nest_id == b}\|` | **不存，算出来** |

⚠️ **不要给 `Nest` 加 `occupied` 字段。** 存两份（UAV 一份 `nest_id`、机巢一份计数）
迟早对不上账。从 UAV 状态推导是唯一事实来源，天然幂等、随时可重算校验。

现有 `common/types.Nest` 的 `capacity: int` / `available: bool` **够用，不必改类型定义**。

---

## 3. 容量语义（需写入论文）

**机巢容量 = 同时归属架数上限**（泊位容量）

```
occupied_b = |{ u : u.nest_id == b }|        # 含飞行中的、含尚未降落的
约束：occupied_b ≤ capacity_b                # 恒成立
```

**为什么含飞行中的？** 降落名额在决策时即预占。若等降落才占，见 §1.2 的超卖。

**时间维度简化**：按**周期末状态**校验，不追踪降落时刻的错峰。论文中明确定义
「机巢容量 = 同时归属架数」。若需更严，可加"降落时间窗内并发数"约束，
但计算量会显著上升，不建议放在第 3 章模型中。

---

## 4. 状态模型

### 4.1 机巢自身

```
available=True  ←─事件─→  available=False
  （正常）                 （故障 / 维护 / 管控关闭）
```

原因与时刻记入 `Nest.meta`：`close_reason`、`close_time`。
目前不引入状态枚举；若日后需区分"计划性维护"与"故障"，再升级。

### 4.2 UAV 侧的归属（重点）

```
uav.nest_id = b        # 该 UAV 归属机巢 b，占用 b 的一个名额
```

`nest_id` 表示**归属**，而非"当前物理位置"。飞行中的 UAV 仍归属其终点机巢。

---

## 5. 状态更新规则（核心）

### 5.1 改 `nest_id` 的三个写点（仅此三处）

| 事件 | 触发位置 | `nest_id` 变化 | 容量影响 |
|---|---|---|---|
| UAV 入役登记 | `UAVFleet.register()` | `None → b` | `b`: **+1** |
| **分配决策 `z_ub`** | **`UAVFleet.apply_solution()`** | `b_old → b_new` | `b_old`: **−1**、`b_new`: **+1** |
| 退役 / 报废 | `UAVFleet.decommission()` | `b → None` | `b`: **−1** |

### 5.2 不改容量的事件（易误做，重点标注）

| 事件 | 改什么 | 容量 |
|---|---|---|
| 起飞执行 | `status → BUSY` | **不变**（仍归属） |
| 降落完成 | `status → IDLE` | **不变**（名额早已预占） |
| UAV 故障 | `status → FAULT` | **不变**（保守，不释放名额） |
| 充电 | `status → CHARGING` | **不变** |
| 机巢关闭 | `nest.available → False` | **不变**（在途 UAV 仍归属它） |

> **降落与容量无关。** 这是本文最重要的一条结论。

### 5.3 归属变更示例

机巢 `b` 容量 4，当前归属 2 架（`UAV#1`、`UAV#2`）：

| 分配方案 | `nest_id` 变化 | 末态归属 | 判定 |
|---|---|---|---|
| `#1`、`#2` 回 b，`#3` 从 c 调来 | `#3`: c→b | 3 | ✅ |
| `#1`、`#2` 回 b，`#3`、`#4` 外来 | `#3`、`#4`: →b | 4 | ✅ 刚好满 |
| `#3`、`#4`、`#5` 全外来 | 三个 →b | **5** | ❌ 超容 1 |
| `#1` 改降 c，`#3`、`#4` 来 b | `#1`: b→c，`#3`/`#4`: →b | 3 | ✅ |

「可用 2 却来 3 架」的判定：看这 3 架里有没有本来归属 b 的。
有 → 末态 = 2（不变）+ 外来数；全外来才超容。

---

## 6. 校验：两道，共用一份判定

### 6.1 第一道 · 规划期（软约束）

落点：`methods/muas/problem.py` 的 `check_feasible`。

```
对每个机巢 b：
    arrivals_b   = 解中 end_nest_id == b 且当前 nest_id != b 的 UAV 数
    departures_b = 解中 end_nest_id != b 且当前 nest_id == b 的 UAV 数
    occupied_b_after = occupied_b_now - departures_b + arrivals_b
    违反 iff occupied_b_after > capacity_b
同时校验：nest.available == True
```

违反计入 `penalty`，进入 `f2 = 路径代价 + w_penalty × penalty`，
允许优化器权衡（例如"宁可超容 1 架也不飞 200 km 去别的巢"）。

### 6.2 第二道 · 落地期（硬约束）

落点：`UAVFleet.apply_solution()`，回写 `nest_id` **之前**再验一次。

**超容 → 拒绝该 tour 并告警，不得静默接受。**

> 为何要两道：规划期的解在执行反馈后可能已失效（另一架中途改降同一巢、
> 机巢临时关闭）。落地期是最后一道防线。

两道**必须共用同一份判定函数**，否则口径会漂。

---

## 7. 超容处理链

```
① repair 改派  →  ② penalty 兜底  →  ③ 落地拒绝
```

1. **repair 改派**（首选）：终点机巢是决策变量 `z_ub`，把超容的 UAV 改到次优巢。
   这是论文 §5(4)「约束处理」的落点，也是 `repair_rules/` 的一部分。
2. **惩罚兜底**：所有巢均满、无可行解时，保留违反但计入 penalty 并标记 violation。
3. **落地拒绝**：`apply_solution` 时仍超容，拒绝 + 告警。

⚠️ **只靠惩罚而不做 capacity-aware repair 是不够的** —— DMDE 会浪费大量代数
在不可行个体上，收敛性与解质量都会受影响。

---

## 8. 边界情况

| 情况 | 处理 |
|---|---|
| 机巢关闭时已有 UAV 归属它 | **保持归属**（可能正在返回），仅禁止新分配选它 |
| 机巢长期关闭 | 触发事件重规划（§6），把在途 UAV 改降其它巢 |
| UAV 故障卡在野外 | 归属**暂不变**（保守）；回收 / 报废时显式 `decommission` 或 `reassign_nest` |
| `capacity = 0` 的巢 | 仅可作过路点，不可作终点 |
| 所有巢均满 | repair 无解 → penalty + violation，解标记 `feasible=False` |
| 多周期滚动 | 因决策即占用，**不会重复分配** ✅ |

---

## 9. 代码落点

### 9.1 数据流

```
data/nest_location_data.csv ─┐
config.yaml (nest_capacity) ─┴→ scenario.build_nest_scenario
                                    ↓
                              list[Nest]（静态属性）          [已有]
                                    ↓
UAV 初始状态 ─────────────────→ UAVFleet.nest_id（归属）      [已有，需补写点]
                                    ↓
                       occupied_b = |{u : u.nest_id == b}|   [推导，不存]
                                    ↓
规划期  selection → muas solve → check_feasible（容量校验）   [需补]
                                    ↓
落地期  fleet.apply_solution(sol) → 按 z_ub 更新 nest_id      [需补：唯一写点]
                                    ↓
故障    fleet.mark_fault → pool.release                      [已有]
```

### 9.2 需要改动的文件

| 文件 | 改动 |
|---|---|
| `methods/uav_state.py` | `apply_solution()` 内按 `tour.end_nest_id` 更新 `uav.nest_id`；新增 `reassign_nest(uav_id, nest_id)`、`decommission(uav_id)`；新增 `nest_occupancy()` 派生查询 |
| `methods/muas/constraints/nest_capacity.py` | **新增**，两道校验共用的判定函数 |
| `methods/muas/problem.py` | `check_feasible` 增加容量约束与 penalty 项 |
| `methods/muas/representation/repair_rules/` | capacity-aware 终点机巢改派（与阶段 0 一并实现） |
| `methods/rolling/event_trigger.py` | 机巢不可用 → 提前重规划触发 |

### 9.3 建议接口

```python
# methods/muas/constraints/nest_capacity.py
def nest_occupancy(uavs, nest_id) -> int:
    """归属该机巢的 UAV 数（含飞行中、含未降落）。"""

def check_nest_capacity(uavs, nests, assignments, *, strict=True) -> CapacityReport:
    """按终点机巢分配方案校验容量。

    assignments: {uav_id: end_nest_id}
    返回各巢 arrivals / departures / occupied_after / violations。
    """

@dataclass(frozen=True)
class CapacityReport:
    per_nest: dict[int, dict[str, int]]
    violations: list[int]          # 超容的 nest_id
    penalty: float
```

```python
# methods/uav_state.py 新增
class UAVFleet:
    def nest_occupancy(self, nests) -> dict[int, int]:
        """各机巢当前归属架数，从 uav.nest_id 推导。"""

    def reassign_nest(self, uav_id: int, nest_id: int | None) -> UAV:
        """显式改归属（回收 / 转场）；触发容量变化。"""

    def decommission(self, uav_id: int) -> UAV:
        """退役：清空归属，释放名额。"""
```

`apply_solution` 的更新顺序（**先校验后写入**）：

```python
def apply_solution(self, solution):
    # 1) 校验（硬约束）
    report = check_nest_capacity(self.all(), nests, assignments, strict=True)
    if report.violations:
        raise CapacityViolation(report)      # 或拒绝超容的 tour 并告警
    # 2) 写入：按 z_ub 更新 nest_id
    for tour in solution.tours:
        if tour.end_nest_id is not None:
            self.reassign_nest(tour.uav_id, tour.end_nest_id)
        # 3) 状态与绑定
        self.assign(tour.uav_id, tour.task_ids) if tour.task_ids else self.mark_idle(tour.uav_id)
```

---

## 10. 与论文的对应

| 论文位置 | 本设计落点 |
|---|---|
| §1(3) 起点已知、终点 `B_u^end` 为决策 | `nest_id` 由 `z_ub` 决定，写点唯一 |
| §4.3「机巢容量或可用性约束」 | §6 两道校验：容量 + `nest.available` |
| §5(4) 约束处理 | §7 处理链的 ① repair 改派 |
| §6「机巢不可用」事件驱动重规划 | §8 边界 + `rolling/event_trigger.py` |
| §4.3「终点机巢可达性约束」 | 已有 `check_feasible` 的 `end_nest_id` 分支 |

---

## 11. 待定项（需拍板）

1. **FAULT 的 UAV 是否占名额？**
   默认**占**（保守，不超卖；且故障机大概率仍需回收到某巢）。
   若场景设定故障机直接报废不占位，则改为不占。

2. **容量只限停放，还是也限"同时充电 / 起降"？**
   本文按**停放**建模。若需建模充电排队（如 4 架返回、仅 2 个充电桩），
   属于另一类资源约束，建议留待第 5 章系统实现阶段，不进入第 3 章模型。
