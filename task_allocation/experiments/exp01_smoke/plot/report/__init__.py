# -*- coding: utf-8 -*-
"""exp01_smoke 图组：**report 层——结论与论文插图**。

放进本层的判断标准：**可以直接放进论文/汇报**，画的是「实验说明什么」，
而不是「实验长什么样（scene）」或「这一轮怎么回事（process）」。

现有图：``outcome_summary``（结果总览：完成率轨迹 / 未完成终局 / 双目标）。
2026-10-26 图件裁剪后仅保留此图（seed_summary / ab_compare 已移除，
需要时从 git 历史找回）。
"""

from .outcome_summary import plot_outcome_summary

__all__ = ["plot_outcome_summary"]
