# exp12_literature_baselines —— 文献基线并排台

**status:** 2026-11-20 从零搭建（骨架复用 exp05_baselines，独立性原则允许重复）
**论文锚点:** §4.5 / 相关工作对照——文献代表基线的描述性并排表
**不负责:** 推断结论（exp05 E1）、机制归因（ab_*）、选择层规模（exp02）、消融（exp04）

---

## 1. 目的

在**同一场景、同一任务到达序列、同一评价尺、同一求解预算**下，把本文完整
方法（M0）与 **5 个文献代表基线**并排跑，给论文补一张「文献基线并排表」：

- **演化/智能优化族**（engine swap，同解码修复链 + 同交付后处理 + 同预算）：
  - `GA`（`baselines/genetic.py`）：锦标赛 + 保序交叉 + 变异 + 精英保留；
  - `PMXDE`（`baselines/pmx_de.py`）：置换编码 + PMX 交叉 + DE/rand/1 贪婪替换；
  - `SORTDE`（`baselines/sort_de.py`）：随机键排序解码 + 标准 DE/rand/1/bin
    （兼「标准 DE」文献对照）。
- **运筹/构造式族**（预构建解通道，构造策略即方法本身）：
  - `VRPTW`（`baselines/vrptw.py`）：Solomon 插入式启发（硬时间窗）；
  - `GREEDY`（`baselines/greedy.py`）：收益/代价比贪心插入。

**这是描述性并排表**：给量级、给梯度，不做推断、不出 verdict / p 值、
不写「优于 / 性能提升 / SOTA」。推断主张仍以 exp05 E1 为唯一出口。

## 2. 公平性口径（表格的灵魂，必须原样进论文表注）

1. **engine swap**：GA / PMX-DE / Sort-DE 与 M0 的差异**只有搜索引擎**
   （DMDE ↔ GA / DE）；初始解构造（随机分组 + 最近邻排序）、解码修复链
   （覆盖装载 / 合并 / 终点巢 MCF / `_best_order`）、交付后 polish、
   评价器与标量 f 全部逐位同一条链路（`baselines/evolution.py` 共享构件）。
2. **同预算**：演化族一律 pop50 × gen100 = 5050 次评价/周期，与 DMDE
   `solver:` 段（population 50 / max_iter 100）同值；改预算须同步两处。
3. **同尺**：唯一比较尺 `path_m_audit`（DEM 有向三维航程逐腿几何重算），
   与求解端解耦；每完成航程 = `path_m_audit / n_completed`。
4. **同到达**：同 seed 各方法共享同一任务到达序列（逐任务可比）；
   求解器随机种子 = 实验 seed × 100000 + 周期序（run.py 注入，可复现）。
5. **构造式通道**（VRPTW / GREEDY）走 baselines 既有预构建解通道
   （不走修复链/polish，「构造策略即方法本身」，与 exp05 B2 同口径）——
   与演化族的通道差异在表中单列「通道」列，**不得**把通道差异读成算法差异。

## 3. 与其它实验的边界

| 实验 | 关系 |
|------|------|
| exp05_baselines | E1 推断（M0 vs 随机）是主张出口；本表是量级并排，不产推断 |
| exp04_ablation | 内部机制开关消融；本表是外部方法对照 |
| ab_* 机制诊断 | 解释「为什么有效」；本表只报「和文献方法比在什么量级」 |

## 4. 红线（诚实口径）

- **禁写**：优于 / 性能提升 / SOTA / 显著；3 seed（df=2）只报 mean±std；
- 演化族「engine swap」不是文献算法的原教旨复现（如 GA 文献常用专属编码/算子），
  是**同编码同解码下的引擎对照**——表注必须声明，不得写成「复现了文献 [x]」；
- VRPTW 为硬时间窗口径（赶不到不排班）、GREEDY 为「迟到不是废飞」口径，
  两者完成率口径不同，**不得**只看完成率排序；
- 墙钟只作观测（单进程、缓存跨格共享先后偏倚）；不与其它实验横比；
- 每完成航程的分母是各方法自己的交付数——高完成率方法分母大，
  该指标须与完成率/按时率并读，禁单列排名。

## 5. 产物与复现

```bash
# 仓库根；求解期不出图、严格串行
.venv/bin/python task_allocation/experiments/exp12_literature_baselines/run.py
# 出表（单源：results_lit/ 落盘结果）
.venv/bin/python task_allocation/experiments/exp12_literature_baselines/build_tables.py
```

产物（`results_lit/`）：`<ID>/seed_<s>/{metrics,run_meta,solution,audit}.json`、
`<ID>/summary.json`、`summary_primary.csv`、`tables/literature_baselines.{csv,tex,md}`。
`claims.enabled=false` ⇒ 不生成 claims_report / paired_report（设计使然）。

## 6. 结果（2026-11-20 正式档，18/18 格全产出）

跑批：6 方法 × 3 seed（42/43/44），全格 pool_empty 自然收敛（周期 4），
claims 按设计跳过。表格见 `results_lit/tables/literature_baselines.{csv,tex,md}`，
本节只给结论不抄表。

1. **演化族（engine swap）四方法量级重叠**：M0 / GA / PMX-DE / Sort-DE 总航程
   41.2~41.6 km（std 1.7~1.9），每完成航程 1.37~1.39 km，完成率全 1.000、
   硬违约全 0——搜索引擎换血不改变结果量级，与 §2 公平性预期一致，也与
   ab_* 诊断（主要收益来自修复链+精修而非引擎）互相印证。
2. **构造式族有可见差距**：Greedy 44.5 km（+7.8%）、VRPTW 54.2 km（+31%、
   std 8.4）；VRPTW 为硬时间窗口径（完成率 0.933±0.115，赶不到不排班），
   且出现硬违约 2.7±4.6（需逐格归因后才能引用）。
3. **M0 与 PMX-DE/SORTDE/GA 的微小差异（≲1%）在 std 之内**：禁写
   「M0 更优」或任何显著性语言；本表只支撑「量级相当、构造式基线偏弱」。
4. 代价观察：演化族墙钟 20~97 s（M0 最高，评价次数 21k vs GA 4.7k，
   与探索多样性/缓存命中差异有关），构造式 <1 s；墙钟只作观测。

**写作口径**：① 表注必须写 engine swap（同编码同解码同预算，非文献算法原教旨
复现）；② VRPTW/GREEDY 为预构建通道且口径不同（硬 TW vs 迟到不废飞），
不得只看完成率排序；③ 每完成航程须与完成率/按时率并读。
