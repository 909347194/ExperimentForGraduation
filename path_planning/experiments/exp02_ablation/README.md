# exp02_ablation — 单机消融实验（README §4.5.2）

对照臂：完整改进方法 vs 去禁飞约束 vs 去 TPR 风险项 vs 去二次碰撞校验 vs `baselines/lin_original`。
指标：飞行时间、总 TPR、可行性标记、偏移修正迭代次数。
运行入口 `run.py` 只做编排（待实现，沿用 exp01_smoke 的 config/metrics/plot 分离惯例）。
