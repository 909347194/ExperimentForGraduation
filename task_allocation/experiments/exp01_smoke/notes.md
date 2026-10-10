# exp01_smoke

**status:** active(端到端已跑通)

## 目的

- 验证**完整链路**在真实地理场景上可跑:机巢 CSV + DEM → 任务/机队 → 两阶段求解 → 滚动闭环 → 指标落盘。
- 不再只是目录与配置联调:`methods/` 已落地,本实验调用真实求解器(DMDE)。
- **不**作为算法性能结论(规模小、迭代少、任务为合成);性能对比见后续 exp05。

## 运行

```bash
uv sync                                              # 首次:建 .venv 装 numpy / pillow / pyyaml
.venv/Scripts/python.exe task_allocation/experiments/exp01_smoke/run.py
```

## 当前结果(默认 config.yaml)

```
场景:8 机巢,容量 4,DEM 已接入,frame=(29.522202, 91.479331)
周期 1 @t= 600s  T_t=10(remain 0/new 10/release 0)  K_avail=24  T_sel=10 → T_exec=7(保留 3)
周期 2 @t=1200s  T_t=8 (remain 3/new  5/release 0)  K_avail=24  T_sel=8  → T_exec=5(保留 3)
周期 3 @t=1800s  T_t=3 (remain 3/new  0/release 0)  K_avail=24  T_sel=3  → T_exec=1(保留 2)

汇总:完成 13/20(65%),平均等待 692s,总代价 331 km,求解 2.1s,池内剩余 2
```

- 三个周期 `check_feasible` **均无 violation、penalty = 0**
- 机巢占用始终 ≤ capacity(4),无超容
- 8 周期长跑:完成率 84%–90%,池内剩余回落至 0(滞留任务被逐步消化)

## 参数放哪里

| 内容 | 位置 | 理由 |
|---|---|---|
| 机巢经纬度、DEM | `task_allocation/data/` | 实测事实,跨实验复用 |
| 机巢个数、容量、机队规模、航程、速度 | `config.yaml` 的 `problem:` | 实验设定 |
| 任务规模、散布、收益、时间窗 | `config.yaml` 的 `tasks:` | 任务当前为合成,后续换 CSV 时参数可平移 |
| 第一阶段权重与阈值 | `config.yaml` 的 `selection:` | 做 α / 五项权重灵敏度时只改这里 |
| 第二阶段模型与剔除策略 | `config.yaml` 的 `muas:` | λ、选择性剔除、等待补偿、终点进编码、Lamarckian 回写 |
| DMDE 超参 | `config.yaml` 的 `solver:` | 种群、代数、ζ、δ |
| 三维代价 / DEM 网格步长 | `config.yaml` 的 `cost:` | 网格步长直接决定一次性开销 |
| 滚动周期、到达、故障注入 | `config.yaml` 的 `rolling:` | 含 `strict_capacity` 硬校验开关 |

**原则**:`methods/` 不读配置、不写死数值;所有数值由 `run.py` 从 yaml 取出后传入。

## 成功标准

1. `python run.py` 跑完不抛异常,写出 `results/metrics.json`、`results/run_meta.json`。
2. 目录结构符合 `experiments/README.md`。
3. `metrics.json` 中记录 8 个机巢的米制坐标 (x, y) 与 DEM 采样高程 z。
4. 每个周期的 `check_feasible` 无 violation(当前满足)。
5. `T_t^exec ⊆ T_t^sel ⊆ T_t`、`U_t^exec ⊆ U_t^avail` 恒成立。

## 已知问题与待办

- 任务点是**合成**的;有实测数据后应补 `data/task_location_data.csv` 并替换
  `common.task_scenario.build_synthetic_tasks`(下游无需改动)。
- 事件触发(`rolling.events`)目前只**识别**事件并写进 metrics,尚未实现
  「仅对受影响 UAV 做局部重规划」,仍走全量重算。
- 故障注入链路(`rolling.faults`)已实现但未在默认配置启用;验证 `T_t^release`
  时填 `faults: {2: [3]}` 即可(第 2 周期让 UAV 3 故障)。
- `plot_dir` 目前为空,`make_plots` 未实现(见 TODO 阶段 6)。
- 环境需 `uv sync`:`pyproject.toml` 已补 `numpy` 与 `pillow`(DEM 栅格解码为惰性依赖),
  系统 Python 未装这些包。

## 下一步

- **容量灵敏度**:`problem.n_uavs` 调到 32(= 8 巢 × 容量 4,满泊位)即可压出容量约束;
  配合 `rolling.strict_capacity: true` 可验证落地期硬校验不误杀可行解。
- **λ 灵敏度**:`muas.lambda_cost` 决定收益与代价的折算比,直接体现为完成率。
- 对照方法(baselines)就位后,本实验可扩成 exp05 的对比入口。
