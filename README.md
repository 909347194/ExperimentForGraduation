# ExperimentForGraduation — 多无人机智能调度中的任务分配与航迹规划方法研究

硕士学位论文实验代码库。第 3 章（动态滚动的多机巢多无人机协同任务分配）为主线。

## 论文章节

## 第1章 绪论

## 第2章 相关理论与技术

## 第3章 动态滚动的多机巢多无人机协同任务分配方法

## 第4章 顾及风险时空异质性的航迹规划方法

## 第5章 智能调度系统实现与应用验证

## 第6章 总结与展望

## 代码与文档导航

| 内容 | 位置 |
|---|---|
| 第 3 章方法文本（权威） | `task_allocation/Chapter3_README.md` |
| 整体方法指南 | `task_allocation/docs/guides/1整体方法.md` |
| 实施路线图 / 现状盘点 | `task_allocation/TODO.md` |
| 文献定位 / 故事线 / 全项目审查 | `task_allocation/docs/`（`research_positioning_*`、`ch03_storyline`、`full_review_*`） |
| 工作报告（2026-10） | `task_allocation/docs/work_report_20261012.md` |
| 实验（冒烟 / 选择规模） | `task_allocation/experiments/` |
| 测试 | `task_allocation/tests/`（pytest，当前 93 例） |

## 第 3 章方法要点（与代码对应）

- **两阶段分层**：动态任务选择（`methods/selection/`）→ 选择性多机巢 MUAS（`methods/muas/`），滚动闭环（`methods/rolling/`）
- **候选规模双重上界**：`min{⌈α·K_avail⌉, cap(t)}`，cap(t) 为能力预算可承载数（`selection/capability.py`）
- **双目标字典序**：先最大化任务收益、再最小化成本（big-M 标量化，保持 DMDE 单适应度框架，`muas/problem.py`）
- **方案 A 终点机巢**：给定任务路线后最小费用流精确求容量可行最优（`constraints/nest_capacity.py`），终点不进 DE 基因
- **选择性与闲置**：任务暂缓仅由可行性修复产生；闲置 = K 决策裁剪 + 解码端合并修复（`muas_stage._consolidate`）
- **测试**：`python3 -m pytest task_allocation/tests -q`
