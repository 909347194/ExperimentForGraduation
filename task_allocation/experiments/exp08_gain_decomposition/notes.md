# exp08_gain_decomposition —— 交付后精修「几何 vs 归属」收益拆分

定稿 §11.4「P4 / 实验层待办」第 1 项：用 P3-b 的三本账
（`l3a_geometric_actions` / `l3b_relocate_actions` /
`n_task_to_uav_changes`）配合同 seed 配对留档。实验自持
config/build/paths/metrics/plot，不建 `_shared/`、不抽公共绘图包。

## 1. 要回答什么

交付后精修的收益里，多少来自**几何**（组内 2-opt/Or-opt、终点巢 MCF、
position），多少来自**归属**（跨机改派 relocate/swap）？两者是否能分开
归因？重点不是让每个模块都取得收益，而是确认每个模块解决什么问题。

## 2. 2×2 同 seed 配对

|  | 归属关 B=0 | 归属开 B=1 |
|---|---|---|
| **几何关 G=0** | `g0b0` | `g0b1` |
| **几何开 G=1** | `g1b0` | `g1b1`（生产口径） |

四档唯一差异用 `dataclasses.replace(stage_model, ...)` 锁定，只允许
`cost_polish_enabled` / `l3b_enabled` / `polish_moves` /
`cost_polish_accept_mode` 四个字段不同；逐 seed 核验配置纯度。
采纳判据四档统一 `f`（定稿 §11.9）。同一 seed 跑完四档再进下一个 seed，
按 seed 交替档序抵消缓存预热偏差；**严格串行，不并行任何实验**。

报告口径：逐档质量指标、三本账、**主效应 + 交互项并列**
（`interaction = (f00−f01) − (f10−f11)`）、配对 t 检验
（精确 Student t，正则化不完全 β；零方差标 `zero_variance_no_test`）。
**禁写「独立收益」**：差值一律是同 seed 配对的条件差。

## 3. 口径限制（论文须如实声明）

1. `cost_polish_enabled=False` 会整段跳过 `post_cost_polish`，
   因此 **G=0 档的 B=1 只覆盖 `polish_assignment` 的 relocate/swap**，
   不含通道 B 的交付后跨机改派。交互项混有「算子可得性」差异，
   **不能解释成纯二阶交互**。
2. 若 `g0b1` 三本账显示零归属动作，说明 B 因子在 G=0 档未真正激活，
   此时交互项主要反映「G=1 档才拿得到通道 B」，不得写作「协同效应」。
3. 终点机巢 MCF 是**给定路径下的条件最优**（非全局最优）。
4. 冒烟 / mid 档结论**不可外推正式档**；正式档须单独跑批。
5. 单一场景（广州 3 机巢）、固定到达流、3 seed ⇒ df=2 效能极低；
   p 不显著须如实声明，也不得写成「无差异」。
6. 完成率 / 恒等式 / eliminating 为实验层硬断言；
   `independent_audit` 的几何重建重评仍是技术债
   （`RollingHorizon` 未暴露审计钩子）。

## 4. 目录与复现

```bash
python run.py --preset smoke --seeds 7            # 设计自检
python run.py --preset mid --seeds 7,11,23        # 中间档（不可外推）
python run.py --preset formal --seeds 7,11,23     # 正式档
python run.py --preset formal --seeds 7 --dry-run # 只看生效配置
```

产物 `results/<preset>/`：`summary.json`（2×2 报告）、`summary.csv`、
逐档逐 seed 的 `metrics.json` / `run_meta.json`。跑批不渲图；
图由 `plot/compare.py` 事后单独生成。`results/` 被 `.gitignore` 忽略。

## 5. 结果

（跑批后回填：冒烟 / mid / 正式三档的主效应、交互项、三本账与结论。）
