# exp04_closed_loop — 小规模闭环事件触发功能验证（README §4.5.3）

验证链路：三类触发事件（动态禁飞阻断 / TPR 超阈值 / 滚动周期）→ feedback 回填
→ 上层重调度（换巢/改派/推迟）→ 新 OD 重规划。
对照：开环一次性分配（欧氏预估）。需与 `task_allocation/methods/rolling/` 及根 `closed_loop/` 联调。
