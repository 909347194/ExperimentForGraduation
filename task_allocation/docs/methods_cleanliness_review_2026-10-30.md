# 方法层（methods）整洁性审查报告

> 日期：2026-10-30　范围：`task_allocation/methods`（14,673 行 / 约 60 文件）
> 目标：**确保方法干净、约束口径正确、旋钮可配置**。

## 1. 审查方法

不是逐行通读（14k 行不可靠也不必要），而是四条可复现的机器审查：

| 手段 | 目的 |
|---|---|
| AST 数值常量扫描 | 抓埋在算法主体里的魔法数字（天然排除注释/文档字符串） |
| AST 死代码扫描 | 抓定义后全仓零引用的顶层符号 |
| 约束口径交叉核对 | 核 knockout/裕度类注释与代码是否真一致 |
| 全量回归 + bit-exact 差分 | 确保"改了结构、没改行为" |

## 2. 总体结论

**基线是健康的**：methods 层无任何 `TODO / FIXME / HACK / DEPRECATED` 遗留标记；GeoTIFF/WGS84 常数属规范值，非魔法数字；测试基线扎实（全量 **326 passed**）。

共查出 **6 项问题，全部已处置**。所有改造严守一条纪律：**默认值与原值逐位相同**，故缺省配置下行为完全等价（bit-exact），仅实验 yaml 显式给值才改变行为——符合本仓库"开关关闭时与历史行为逐位等价"的一贯约定。

## 3. 发现与处置

| # | 问题 | 位置 | 处置 | 改行为? |
|---|---|---|---|---|
| 1 | 边际收益邻近加权写死 `0.2` / `500 m`，且原注释自承"与 `priority.synergy_radius` **不同源**" | `selection/marginal_gain.py` | 提为 `MarginalConfig.proximity_boost` / `proximity_scale` | 否 |
| 2 | 温度→扰动概率系数写死，**两级还不一致**（指派级 0.4 / 巡游级 0.5），另有两个独立写死的温度门限 `0.1` | `muas/representation/inverse_mapper.py` | 新增 `InversePhiConfig`（4 个旋钮） | 否 |
| 3 | `nest_offset = 1_000_000` 默认值 **重复 6 处**，改一处漏一处会让"机巢键 = offset + id"键空间错位 | 5 个 cost 模块 + `stage_runner` + `rolling/horizon` | 归一到共享常量 `DEFAULT_NEST_OFFSET` | 否 |
| 4 | `demand_serve_margin` 注释声称与 `feasibility.range_margin`「对齐」，但**默认值本就不同**（0.95 vs 1.0）且代码无同步强制 | `solvers/stage_config.py` | 修正为诚实描述（不同源、需手动保持一致） | 否（注释） |
| 5 | 4 个全仓零引用的死函数 | `coverage_gate.arrival_at`、`crossover.mutate_with_strategy`、`mapper.phi_batch`、实验 `run.run_trial_offline` | 删除（共 113 行） | 否 |
| 6 | 哨兵值幅值散落（`1e6` 不可达 / `1e7` 航程 / `1e12` 空方案）；eps 口径不一（`1e-6`/`1e-9`/`1e-12`）；`coverage_gate` 静默 `speed=10.0` 兜底 | 多处 | **本轮未改**（改幅值即改行为），记录备查 | — |

### 关于 #3 的一个依赖陷阱（值得记录）

`nest_offset` 常量**不能**放进 `cost_matrix.py`：该文件本身会导入 `provider` 与 `lazy_pairwise`，反向引用即成循环导入（`provider.py` 里早有注释记了这个回环风险）。因此新建**叶子模块** `muas/cost/nest_keys.py`（不导入任何本地模块），各层均可安全引用。已做导入冒烟验证。

## 4. 验证证据

| 验证项 | 结果 |
|---|---|
| 全量回归测试 | **326 passed**（6.95s，零回归） |
| `inverse_mapper` bit-exact 差分 | 新/旧两版在 2 模型 × 3 规模 × 6 温度 × 30 种子 = **1080 组**，输出逐位一致（**0 不一致**） |
| `marginal_gain` 数值等价 | 默认配置与原硬编码公式结果相同（`5.670320046036`，差 < 1e-12） |
| 旋钮确实生效 | `InversePhiConfig` 改值后 40 组中 19 组序发生变化；`MarginalConfig` 改值后 ΔB 由 5.670→5.073 |
| 循环导入冒烟 | `cost_matrix`/`provider`/`lazy_pairwise`/`horizon` 均可正常导入，常量解析一致 |

> 注：差分时把 `git HEAD` 版本另存为包内临时模块同副本比对，用完即删，工作区无残留。

## 5. 遗留建议（下一轮可做）

1. **统一哨兵常量**：把 `1e6`（不可达代价）、`1e7`（默认航程）、`1e12`（空方案惩罚）收进同一常量模块并命名，消掉"同概念不同幅值"的隐患。改幅值会动行为，建议单独一次配套验证的 PR。
2. **统一浮点容差**：当前 `1e-6 / 1e-9 / 1e-12` 混用，建议按语义（航程米级 vs 适应度）收敛为两个命名常量。
3. **`coverage_gate` 的 `speed` 静默兜底**：`getattr(uav, "speed", 10.0)` 在 UAV 缺 speed 字段时静默取 10 m/s，可能掩盖数据缺失。建议改为显式参数或告警。
4. **`priority.synergy_radius` 与 `marginal_gain` 邻近尺度仍是两源头**：#1 只是把后者提为可配，并未消除双源头。若需真正统一，应让两者引用同一配置项。

## 6. 变更清单

修改 12 个文件（+287 / −162），新增 2 个文件（`muas/cost/nest_keys.py`、实验 `summarize_ab.py`）。全部改动保持默认行为 bit-exact。
