# -*- coding: utf-8 -*-
"""第三方地面 TPR 风险模型（README §4.3.2）。

拆分：crash_model（坠毁概率）→ ground_exposure（地面暴露/后果）→ risk_field（时变风险场与链路风险）。
命名由原 TPR/ 调整为 tpr/，与仓库包命名惯例（task_allocation/methods/muas）一致。

**阶段口径**：当前阶段只处理禁飞可行性，本包不启用（λ_r = 0），属未来拓展
（design_nofly_zones.md §10）。论文第 4 章保留 TPR 建模与综合代价函数的章节位置，
代码侧仅保留此槽位（`tpr_enabled()` 恒 False），未来实现时在此包内补全三部分。
"""

LAMBDA_R = 0.0


def tpr_enabled() -> bool:
    """TPR 是否启用（本阶段恒 False：论文保留章节、代码不实现）。"""
    return False
