# -*- coding: utf-8 -*-
"""第三方地面 TPR 风险模型（README §4.3.2）。

拆分：crash_model（坠毁概率）→ ground_exposure（地面暴露/后果）→ risk_field（时变风险场与链路风险）。
命名由原 TPR/ 调整为 tpr/，与仓库包命名惯例（task_allocation/methods/muas）一致。

**阶段口径**：当前阶段只处理禁飞可行性，本包不启用（λ_r = 0），属未来拓展
（design_nofly_zones.md §10）。
"""
