# 设计：异构机队 JSON 配置（fleet spec）（2026-11-11）

> 状态：**已落地**（`task_allocation/common/fleet_spec.py` + exp01 接线 +
> `tests/test_fleet_spec.py`）。回答的问题：**未来如何配置异构无人机？**
> 用什么格式、怎么声明初始机巢与航程、怎么与现有同构口径共存。

---

## 1. 背景

- 异构 per-UAV 化已在方法层落地（WP-B/C，见
  `modification_plan_20261104_hetero_claims.md`）：`UAV` 有逐机
  `max_range` / `max_time` / `speed` / `nest_id`，geo 永久判定用机队最强航程、
  预筛/服务判据逐机，换电恢复到逐机满电值。
- 缺的是**配置层**：`config.yaml` 的 `problem:` 段只能写同构机队
  （`n_uavs` + 全局 `uav_max_range`），无法表达「3 架 A 型驻巢 0、航程 10 km；
  2 架 B 型驻巢 1、航程 6 km」。
- WP-D 的 D3「异构域扫描」（异构度 = 航程极差比 → 完成率 / 选择行为 /
  休眠率）需要逐机配置入口——本设计即其前置。

## 2. 选型：为什么是 JSON

| 候选 | 判定 |
|---|---|
| **JSON 机队清单（选定）** | 机队是「装备台账」型**数据**（谁、什么型号、驻哪个巢、航程多少），与机巢 CSV 同类，不是实验旋钮。放在实验目录下由 `problem.fleet_file` 引用：可跨实验共享、可被扫参脚本批量生成（D3 逐档生成一个文件）、无求值风险、可严格 schema 校验 |
| YAML 内联进 config.yaml | 能用，但把台账数据与实验旋钮混进同一文件，违背本仓「参数放哪里」原则（`experiments/README.md` §2）；扫参生成时要改整个 config |
| Python 代码（dataclass 直接构造） | 丧失「配置与代码分离」，非工程人员不可改；扫参仍要写脚本 |

关键取舍：**清单是数据、config.yaml 是旋钮**。`problem.fleet_file` 是旋钮
（指向哪个台账）；台账本身是数据。

## 3. Schema

```json
{
  "version": 1,
  "description": "人读说明（可选，不参与装配）",
  "defaults": {"speed_mps": 15.0, "max_range_m": 10000.0, "max_time_s": null},
  "models": {
    "long":  {"max_range_m": 10000.0, "speed_mps": 20.0},
    "short": {"max_range_m": 6000.0}
  },
  "uavs": [
    {"id": 0, "model": "long",  "home_nest": 0},
    {"id": 1, "model": "long",  "home_nest": 1, "max_range_m": 8000.0},
    {"id": 2, "model": "short", "home_nest": 1, "max_time_s": 300.0}
  ]
}

> Schema 示例的数值仅演示三层继承，不是标定值。exp01 当前清单按
> guangzhou 研究区（约 5 km × 5 km）标为 10 / 8 / 6 km，见
> `experiments/exp01_smoke/uav_config.json` 的 `description`；
> `max_time_s` 在该清单中一律留 `null`，由航程/速度派生。
```

### 3.1 字段

| 键 | 层 | 必填 | 语义 |
|---|---|---|---|
| `version` | 顶层 | 否（=1） | schema 版本，只认 1 |
| `description` | 顶层 | 否 | 人读说明（自由文本，不参与装配，仅溯源/阅读） |
| `defaults` | 顶层 | 否 | 性能缺省（三层继承的底层） |
| `models` | 顶层 | 否 | 型号表：名字 → 性能键 |
| `uavs` | 顶层 | **是** | 逐机条目（非空列表） |
| `home_nest` | 逐机 | **是** | **初始机巢 id**（= `Nest.id`，即机巢 CSV 行序、0 起）。不参与继承——驻巢是逐机事实 |
| `id` | 逐机 | 否 | 机号；缺省取条目序号；清单内唯一 |
| `model` | 逐机 | 否 | 引用 `models` 中的型号 |
| `max_range_m` | 三层 | 装配后必有 | **满电航程（m）**，> 0 |
| `speed_mps` | 三层 | 装配后必有 | 巡航速度（m/s），> 0 |
| `max_time_s` | 三层 | 否 | 最大飞行时间（s）；null/缺省 = 自动派生 `max_range_m / speed_mps`（与航程预算自洽，不新增淘汰）；显式更小值即成独立绑定约束 |
| `meta` | 逐机 | 否 | 透传进 `UAV.meta`（溯源/扩展） |

**继承链**：`defaults` ← `models[model]` ← 逐机键（后者覆盖前者）。
键名显式带单位（`_m` / `_mps` / `_s`），防单位歧义。

### 3.2 严格校验（拼写错误不许静默失效）

以下一律抛 `ValueError`（带文件名与条目定位），不静默修正——与
`experiments/README.md` 的死键教训同因：

- 未知键（任何层）；`model` 引用未定义；`uavs` 空；
- 缺 `home_nest`、类型不对（含 bool）；
- 重复 `id`；`speed_mps`/`max_range_m`/`max_time_s` ≤ 0 或缺失；
- 装配期：`home_nest` 不存在于场景机巢；**初始驻巢数 > 巢容量**
  （超容是配置错误，与 `problem` 注释「不初始超容」同口径；
  `build_uavs_from_spec(..., check_capacity=False)` 可关，交给运行期
  `strict_capacity` 校验）。

## 4. 装配语义（与同构回退逐位一致）

`build_uavs_from_spec(spec, nests)`：

1. 初始位置 = 所驻机巢坐标（x, y, z）；`nest_id = home_nest`；
2. `remaining_range = max_range_m`（满电出巢）；`max_range = max_range_m`；
3. `max_time`：显式 `max_time_s` 优先，否则派生 `max_range_m / speed_mps`
   （与 exp01 同构回退 `build_uavs` 的派生规则完全相同）；
4. **同构等价回归锚点**：清单全机同性能时，产物与同构回退逐字段一致
   （唯一差异：`max_range` 字段在回退口径是 `None`＝跟随全局缺省、清单是
   显式值，**解析语义相等**，见
   `tests/test_fleet_spec.py::test_exp01_build_uavs_homogeneous_equivalence`）。

## 5. 接线与使用

```yaml
# config.yaml
problem:
    fleet_file: fleet_guangzhou.json   # 相对实验目录（config.yaml 所在目录）
    # n_nests / nest_capacity 照旧（场景事实）；
    # n_uavs / uav_speed / uav_max_range / uav_max_time 降为 metrics 回显缺省
```

- 路径解析：相对路径按**实验目录**解析（机队是实验设定，与
  `data.path`（机巢 CSV，按场景数据目录解析）刻意区分）；绝对路径直用。
- 不设 `fleet_file` ⇒ 同构回退，行为逐位不变（既有实验零破坏，与 WP-B
  「全局缺省回退」同一模式）。
- 下游**零改动**：`solution.json` 的 `uavs[]` 逐机落盘 `speed` / `max_range` /
  `max_time` / `nest_id`（2026-11-11 补齐性能字段，并把审计 D/J/J2 per-UAV 化：
  逐机取值、缺省回退全局）；绘图、指标按逐机字段消费。
- 已知边界：`metrics.problem` 的 `uav_speed` / `uav_max_range_km` 两个回显
  字段在 fleet_file 模式下取 yaml 缺省值（heterogeneous 明细以
  `solution.json` 的 `uavs[]` 为准）；如需精确回显，保留同名 yaml 键作台账摘要。

## 6. D3 异构域扫描的预期用法

异构度 δ = 航程极差比（min/max）。扫参脚本按 δ 逐档**生成**清单文件
（数量、总预算、巢分布固定，只改航程分布），每档一个 JSON：

```python
# 伪代码：δ=0.4 → 6 机、均值 10 km、极差比 0.4
{"defaults": {"speed_mps": 15.0},
 "uavs": [{"home_nest": i % 3, "max_range_m": r_i} for i, r_i in enumerate(ranges)]}
```

config 侧只需 `--set problem.fleet_file=fleet_d04.json` 切档，
config_fingerprint 随档变化、可回溯。

## 7. 与既有口径的衔接（勿踩坑）

1. **geo 永久不可达判定用机队最强航程**（WP-B 设计点 1）：换电回满 ⇒
   「永久不可达」只在最强机也够不着时成立。fleet spec 不改变该口径；
   短机写进清单不会误杀长机可服务任务。
2. `HorizonConfig.uav_max_range`（全局）语义 = 同构缺省满电航程；
   逐机 `max_range` 优先、缺省回退——清单模式下该回退**不会**被触发
   （每机必有 max_range_m）。
3. 机巢容量（`Nest.capacity`）仍是场景事实（`problem.nest_capacity`），
   清单只决定**谁驻哪**；初始超容在装配期即报错。
4. 续航绑定：`max_time_s` 显式值是独立绑定约束（如电池/法规限制），
   否则派生值与航程预算严格自洽（time 约束是 range 约束的等价重述）。

## 8. 测试锚点

`task_allocation/tests/test_fleet_spec.py`：

- 三层继承 / id 缺省与重复 / 严格校验 10 组拒绝用例 / max_time 派生；
- 装配：巢位起点、满电、未知巢、初始超容（含 `check_capacity=False`）；
- exp01 接线：`fleet_file` 逐机装配、**同构等价逐字段**、同构回退不变。
