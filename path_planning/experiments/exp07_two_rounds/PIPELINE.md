# exp07_two_rounds：单机两轮链式航迹

## 跑什么

一架 M30T（挂巢0），**两轮**分两次独立求解，串接只发生在请求参数上：

| 轮 | 起点 | 停靠点 | 结束状态 |
| --- | --- | --- | --- |
| 1 | 巢0 上方（地面 + `takeoff_agl_m`=8 m → z≈21.1 m），`t0=0` | 任务 201 `(-587.8,-756.5,60)`，服务 20 s | **停在 201**（末速度 0） |
| 2 | 轮1 的末位置、末时刻（`t0=轮1.t_end`）、速度 0 | 任务 202 `(-769.6,507.6,40)`，服务 20 s | **停在 202** |

两轮都没有"回巢/下行"状态机：`PlanRequest` 只要求 `stops` 非空（`common/plan_types.py`），
末项不必是 nest，所以"终点停在任务点"在类型层就合法，这里把它跑实。

## 看什么

1. 上一轮的输出（末位置 / 末速度 / 末时刻）能不能原样当下一轮的输入——**这是核心判据**；
2. 两轮各自都要过完整流水（走廊 → RRT → NLP → 复核闸），不是"接着上一轮的解继续优化"；
3. 停在任务点而不是巢面，末端质量账（净空、裕度）是否仍然成立。

## 记什么

`results/seed_0/`：

- `round_trace.json`：逐轮请求（起点、时刻、停靠点）、票（`t_end`/时长/弧长/失败码/闸）；
- `round_trajectories.json`：键 `"轮:机号"`（`"1:1"`、`"2:1"`），可直接喂本实验 `support/scene_3d.py`；
- `run_meta.json`：轮次表、`effective_config`、装配与运行耗时；
- `figures/P01_two_rounds_3d.{png,pdf,html}`：三维图（`build_figures.py` 生成）；
- `tables/exp07_main_results.{csv,tex}`：逐轮指标表；`tables/exp07_chain_results.{csv,tex}`：链式三项接口量。

## 审计判据（16 项）

| 组 | 判据 |
| --- | --- |
| 逐轮（×2 轮） | 票自洽（trace 时刻 == 航迹 `t0/t_end`）、时间来源是真实排（非降级）、分段边界落在停靠点（≤1 mm）、运动学裕度、稠密净空 0.2 m ≥ 6 m、端点豁免区 ≥ 2 m |
| 链式 | 轮2 起点 == 轮1 终点（位置差、时刻差、起飞速度三项全 0） |
| 停靠点 | 每个任务点自身 SDF ≥ 6 m（**不受端点豁免**，防"终点贴楼被豁免球盖掉"） |
| 全局 | 两轮时间窗不重叠（除衔接瞬时）；检测器若在衔接点读到一对，CPA 时刻必须落在衔接时刻——那是航迹接缝，不是两架机的共时冲突；两轮里程/末刻在机动上限内 |

## 数字（种子 0）

| 轮 | 时长 | 弧长 | 直线 | 绕行比 | 最差净空 | 交付闸 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 182.17 s（飞 162.17 + 悬停 20） | 2141.1 m | 2128.8 m | 1.0058 | 9.84 m | 通过 |
| 2 | 119.11 s（飞 99.11 + 悬停 20） | 1291.8 m | 1277.3 m | 1.0113 | 12.18 m | 通过 |

合计 3432.9 m / 301.27 s（末刻 301.27 s），审计 16/16。

**与 exp05 的对照**：exp05 的 leg0/leg1 是 `巢0→201`、`201→202` 两段（一次请求内多段解），
本实验把同样的两段拆成两个**独立请求**，弧长 2141.12 / 1291.75 m 与 exp05 的
2141.1 / 1291.6 m 逐位一致，飞行段时长同样 162.17 / 99.11 s。
即"分轮独立求解"没有引入额外绕行或额外时间——链式分解本身不损失最优性（同一走廊规则、
同一 RRT 种子下）。

## 与"拍"的区别

本实验**不接上层决策周期**：没有 `cycle_length_s`、没有 `busy_until`、没有 `prune`。
"轮"只是一次离线的规划调用，轮间靠 `t0 = 前轮 t_end` 手动对齐时间轴；
把这一层交给 `closed_loop` 才是 exp06 的"拍"。

## 怎么跑

```powershell
$env:PYTHONPATH="."
& ".\.venv\Scripts\python.exe" -m path_planning.experiments.exp07_two_rounds.run
& ".\.venv\Scripts\python.exe" -m path_planning.experiments.exp07_two_rounds.audit
& ".\.venv\Scripts\python.exe" -m path_planning.experiments.exp07_two_rounds.build_figures
& ".\.venv\Scripts\python.exe" -m path_planning.experiments.exp07_two_rounds.build_tables
```

后两个只读 `results/seed_0/` 的落盘航迹，不重跑规划；图写 `results/seed_0/figures/`、
表写 `results/seed_0/tables/`。`build_figures.py` 用 `split_by="stage"`：两轮各一块面板
（单机多轮时按段拆），否则长条场景里航迹会被压成细线。
