# exp01_smoke 代码审查（2026-10-24）

审查对象：`task_allocation/experiments/exp01_smoke/` 全部 Python 代码
（run.py 419、metrics.py 713、config.py 331、build.py 267、audit_correctness.py 546、
render_from_json.py 68、plot/ 7573 行）。

与 2026-10-23 那次「配置对账」的区别：本次是**代码逻辑审查**，读全部实现、
对关键疑点做实证（跑数据验证而非仅读代码），找出会让结论失真或门禁失效的缺陷。

审查方法：
1. 逐文件通读 `run.py` / `metrics.py` / `config.py` / `build.py` / `audit_correctness.py`；
2. 追溯跨文件口径（metrics 的字段 → muas_stage 诊断字典 → problem 的 evaluate），
   确认指标与算法是否同源；
3. 对「疑似 bug」跑数据实证（路径反解 vs 直读、图分组解析），不靠推断定罪。

---

## 结论速览

| 级别 | 数量 | 一句话 |
|---|---|---|
| **P0 门禁失效** | 2 | 3 seed 只审计 1/3（且其余 seed 产物不足、事后补不了）；审计判据吃不到 `--set` |
| **P0 会崩溃** | 1 | `--group scene` 是无效值，且被写进文档 → 求解完成后崩溃、审计被跳过 |
| **P1 口径隐患** | 3 | 推迟率分子含废飞；多 seed 分母取代表 seed；路径走反解而非直读 |
| **P1 图会说谎** | 1 | setup_params 图仍读 yaml 原文标注口径（第二次出现同类分裂） |
| **P2 代码气味** | 4 | 墙钟事后回填；指纹含 seed；seed 解析裸异常；文档三处漂移 |

已实证**不是** bug 的疑点（避免误报）：见文末「排除项」。

---

## P0-1 ❗ 多种子运行只审计 1/3，且其余 seed 无法补审

**位置**：`run.py:374`（只写代表 seed 的 solution.json）、`run.py:412`（只调用一次审计）、
`audit_correctness.py:85-90`（只读顶层 `cycles`）。

**机制**：

- `final_metrics["cycles"]` = 代表 seed（seed 42）的周期明细；
  `write_json(out_dir / "solution.json", rep_solution)` 只落代表 seed 的 tours /
  fitness_weights / final_fitness；
- `final_metrics["per_seed"][seed]["cycles"]` 只含 `summary` + `cycles`（`run.py:360-363`），
  **不含 tours**；
- `audit_correctness.py` 全程只读 `m["cycles"]` 与 `sol["cycles"]`，
  grep 确认**从不出现 `per_seed`**。

**后果**：3 seed 运行，只有 seed 42 的 A~N 全部 23 项被验证；seed 43/44 的
**零项**被验证。而且这不是「跑一次审计就能补」——D（ETA 重算）、J（航程重算）、
K（起点链）、M（段长方向性）、N2（适应度复算）五项**依赖 solution.json 的 tours 与
fitness_weights**，非代表 seed 根本没有这些产物，事后无法补审。

**为什么这项严重**：exp01 的定位（脚本 docstring 首句）就是「方法正确性验证」实验，
审计是它的产出本身。3 seed × 10 分钟跑完，只有 1/3 的样本带正确性凭据，
而论文主表报的是三 seed 均值。

**修法**（二选一）：
- (a) 每个 seed 单独落盘 `results/seed{id}/solution.json`，审计遍历 per_seed 逐 seed 跑
  （改动小，产物体积 ×3）；
- (b) 审计只跑代表 seed，但在 metrics.json 顶层显式标注 `audited_seed: 42` +
  `audit_coverage: 1/3`，让「未审计」成为可见事实而不是隐性漏洞（改动最小，
  但不能作为正确性凭据用）。

---

## P0-2 ❗ 审计判据的三个参数吃不到 `--set`，与 run_meta 指纹构成口径分裂

**位置**：`audit_correctness.py:95`（`w_decay`）、`:112`（`max_ratio`）、`:113`（`service`），
均来自 `load_config(EXP_DIR / "config.yaml")` 的 **yaml 原文**。

**机制**：`--set muas.late_reward_decay_seconds=900` 这类覆盖只写进 `run.py` 的
`raw_cfg`，落进 `run_meta.config_fingerprint`；但审计脚本自己再读一遍 yaml 原文，
拿到的仍是 1800。

**后果**：
- E 项奖励守恒按错误折减常数判 ⇒ **误报 FAIL**（或更糟：真错时因常数碰巧接近而漏判）；
- G 项漏斗上限按旧 `max_ratio` 判；
- D/J 项仿真按旧 `default_service_time` 推进游标。

**为什么这项严重**：这正是 2026-10-23 已修过的同一类漏洞（当时门禁判据误报 FAIL，
修复方式是「改读 metrics 自描述字段」）。那次只修了门禁判据一处（`audit:99-111`），
**同一函数的另外三个参数没跟着修**——同源缺陷修了一半。

**修法**：`run.py` 把生效 `raw_cfg` 落盘 `out_dir/effective_config.json`；审计优先读它、
缺则回退 yaml（与门禁判据的「优先自描述、回退旧数据」同构）。

---

## P0-3 ❗ `--group` 的合法值与图集实际分组名不一致 → 求解后崩溃

**位置**：`run.py:276` `choices=("scene", "process", "report")`；
`plot/registry.py` 实际分组为 `setup / initial / process / report`（各 1 / 1 / 11 / 3 张，共 16）。

**实证**（不是推断）：

```
各 group 计数: {'initial': 1, 'process': 11, 'report': 3, 'setup': 1}
--group scene  -> ValueError: 未知分层 ['scene']；可用：['setup','initial','process','report']
```

`resolve()` 对未知分层是**抛异常**而非返回空集（`registry.py:341`），所以：

- `--group scene` → 出图阶段抛 `ValueError` 未捕获 → `run.py` 整体崩溃；
- 崩溃点在求解**之后**（求解 ~10 min/seed 已完成、json 已落盘），所以数据不丢，
  但**末尾的正确性审计被跳过**（`run.py:412` 在出图之后）；
- `--group initial`（正确名字）反而被 argparse 拒绝（不在 choices 里）；
- `setup` 组（setup_params 图）没有任何 CLI 入口，只能 `--figure` 逐张指定。

**更糟的是**：`PIPELINE.md:102` 把这个错误用法写成了官方示例：

```
# 或跑实验时限定：python run.py --group scene --figure geo_overview
```

照文档操作必然在求解完成后崩溃。

**修法**：`choices` 改为 `("setup", "initial", "process", "report")`；
`make_plots` 在 `resolve` 结果为空时打一行警告（当前 `return []` 静默）；
同步改 `PIPELINE.md:102` 与 `run.py:277` 的 help 文案。

---

## P1-1 推迟交付率分子含废飞（废飞非 0 时会虚高，甚至 >1）

**位置**：`metrics.py:244-247`。

```python
deliver_cycle_of = {tid: r.cycle for r in records for tid in r.executed_ids}
n_deferred_delivery = sum(1 for tid, dc in ... if arrive_cycle_of.get(tid, dc) < dc)
deferred_delivery_ratio = n_deferred_delivery / len(completed)
```

`executed_ids` ⊋ `completed`：审计 C 项判据本身就断言 `infeasible ⊆ executed`
（`audit:155`，且 horizon.py:801 走 `pool.release(reason="infeasible_delivery")` 回池）。
废飞任务进了分子、进不了分母 ⇒ 比率虚高，废飞多时可 >1。

当前数据废飞恒 0（本轮 3 seed `n_infeasible_delivery=0`），**未暴露**；
一旦跑门禁/积压域（废飞曾非零）就会污染该指标。

**修法**：分子限定为 `set(deliver_cycle_of) & {t.id for t in completed}`。

---

## P1-2 多 seed 汇总的分母取代表 seed 的批次大小

**位置**：`run.py:376` `n_arrived = len(rep_facts.batch.all_tasks())`，
传给 `print_summary(final_metrics, n_arrived, out_dir)` 作「完成 X±Y / N」的分母。

fixed 到达下各 seed 任务数相同（本轮 60/60/60），所以没错；
但 `arrivals.mode: poisson/uniform` 下各 seed 到达数不同 ⇒ 终端汇总的分母是 seed 42 的、
分子是三 seed 均值，**分子分母不同源**。

（只影响终端打印与 `print_summary`，不影响 metrics.json —— `summary.n_arrived` 是逐 seed 的，
`seed_summary` 也聚合了 `n_arrived`。属「报表说谎」而非「数据说谎」。）

**修法**：多 seed 时用 `seed_summary["n_arrived"]["mean"]`，或干脆在汇总行里
逐 seed 打印分母。

---

## P1-3 总路径走「反解」而非直读已有的 `final_path_m`

**位置**：`metrics.py:134-137`：

```python
total_cost = total_f2 - w_penalty * total_penalty   # 从 f2 里减去惩罚，反解纯路径
```

而 `record.muas` 里**已经落了** `final_path_m`（`muas_stage.py:2754`，
`problem.path_cost(final)`，纯路径、无惩罚），可直接累加。

**实证：当前三 seed 反解与直读逐位相等**（差 0.000000），所以**不是 bug**：

| seed | total_f2 | penalty | 反解路径 | final_path_m 直读 | 差 |
|---|---|---|---|---|---|
| 42 | 1614716.025 | 3395.346 | 1611320.679 | 1611320.679 | 0.000000 |
| 43 | — | 1496.724 | 1714690.368 | 1714690.368 | 0.000000 |
| 44 | — | 3655.574 | 1690172.116 | 1690172.116 | 0.000000 |

**为什么仍要改**：这个相等依赖一个未写进代码的隐含前提——终态 `final.f2_cost` 里的
penalty 恰好等于 `record.muas["penalty"]`（即最后一次 `evaluate` 是以 `penalty=None`
调用的，才会取 `feasibility["penalty"]`，见 `problem.py:345`）。
只要 gate 修复 / polish / coverage_first 任一路径传入**显式** penalty，
反解就静默偏，而报告仍标「总三维路径代价」。

**修法**：改为直读 `sum(r.muas.get("final_path_m"))`，把反解值作为交叉校验项
（|反解 − 直读| > 1e-6 时报警），口径从「间接推导」变「直读 + 自校验」。

---

## P1-4 setup_params 图仍按 yaml 原文标口径（同类分裂第二次出现）

**位置**：`plot/setup/setup_params.py:168`
`("双目标口径", "字典序" if muas.get("lexicographic") else "加权和")`，
`muas` 来自 `_load_config()` 读的 config.yaml 原文（`:61-67`）。

而 `metrics.json` 里**已有吃 `--set` 的自描述字段** `problem.scalarization`
（`metrics.py:324-327`，取自 `problem_model.lexicographic`）。

即：10-23 修的是「算法侧透传」，图侧没跟着改。后果与上次同类——
`--set muas.lexicographic=false` 跑完，算法按加权和跑、图仍标「字典序」。
同一张图还有 `lambda_cost / w_violation / w_time`（`:170`）也一样。

**修法**：图改读 `metrics["problem"]["scalarization"]`（并在 problem 段补全
lambda/w_violation 的自描述，现只有 scalarization 与 time_window）。

---

## P2 代码气味与文档漂移

| # | 位置 | 问题 |
|---|---|---|
| 1 | `run.py:339-341` | 墙钟事后回填：`build_metrics` 时 `f.wall_seconds` 恒 0，之后才覆盖 `summary["wall_seconds"]`。能工作但顺序一改就退回历史 bug（I7）。应把 elapsed 作为参数在 `build_metrics` 前注入。 |
| 2 | `metrics.py:456` | `config_fingerprint(f.bundle.raw)` 含 `experiment.seed` / `solver.seed`（raw 是 seed_cfg）⇒ 同口径换 seed 指纹不同，无法按指纹归类「同口径产物」。（exp05 同口径但它每 (arm,seed) 单独落盘，故无害；exp01 只在代表 seed 上落一个 meta，就有歧义。）建议指纹剔除 seed 类字段。 |
| 3 | `run.py:199-206` | `_parse_seed_list` 对 `1-3-5` 会 `int("3-5")` 抛裸 `ValueError`（非 argparse 错误，用户看到 traceback 而非友好报错）。 |
| 4 | `run.py:36,410` / `PIPELINE.md:120` | 文档称「23 项」判据，实际 22~24 随数据形态浮动（条件分支是否触发）。 |
| 5 | `run.py:9` | docstring 说图写到 `plot/figures/`，实际是 `results/figures`（yaml `experiment.figures_dir`）。 |
| 6 | `PIPELINE.md:101` | 示例用 `from plot import ...`（顶层包导入，依赖 cwd/sys.path）。改用绝对包路径 `from task_allocation.experiments.exp01_smoke.plot import ...` 更稳。*（更正：`render_from_results` 本身是有效对外 API——`plot/__init__.py:254`，读目录；`make_plots` 是读 dict 的底层函数，两者并存，本条仅涉及导入写法。）* |

---

## 排除项：已实证**不是** bug 的疑点

审查时怀疑、跑数据后排掉的，留档避免重复排查：

1. **`f2_cost` 与 `penalty` 不同源？** —— 否。`problem.evaluate` 在 `penalty=None` 时取
   `details["penalty"]`（`problem.py:345`），与 `horizon.py:518` 记的
   `feas["penalty"]` 同源。故 P1-3 的反解当前正确（三 seed 差 0.000000）。
2. **`on_time_rate` 会不会把「既迟到又废飞」的任务双扣？**
   `on_time_rate = (n_exec − n_late − n_infeasible)/n_exec`（`metrics.py:293-296`）。
   不会：审计 C 项断言 `late ⊆ completed` 且 `infeasible ∩ completed = ∅`，
   两个集合互斥，逐周期互斥则累加后仍互斥。
3. **`w_violation` 解耦后 F2 组成变了，反解会错？** —— 不会。`w_violation` 只作用于
   适应度层（T2 软违约计价），`f2_cost` 恒为 `path + w_penalty·penalty`
   （`problem.py:271-273`），与适应度口径解耦，反解不受影响。
4. **路径反解与直读不符？** —— 见 P1-3 表格，三 seed 全等。

---

## 建议修复顺序

1. **P0-3**（一行 choices + 文档）—— 5 分钟，且是唯一会让运行崩溃的项。
2. **P0-2**（落盘 effective_config.json + 审计优先读它）—— 30 分钟，
   护住 `--set` 消融这条常用路径上的所有判据。
3. **P0-1**（per-seed solution 落盘 + 审计遍历）—— 改动最大，但决定
   「3 seed 结果能否自称经过正确性验证」；若暂不做，至少先落
   `audited_seed` 自描述，别让漏洞隐性存在。
4. P1 四项 + P2 六项可批量收尾。

---

## 附：本次审查的实证命令（可复跑）

```bash
# 图分组解析（暴露 P0-3）
python -c "from task_allocation.experiments.exp01_smoke.plot.registry import resolve, FIGURES; \
print({g: sum(1 for f in FIGURES if f.group==g) for g in sorted({f.group for f in FIGURES})});
resolve(groups=['scene'])"

# 路径反解 vs 直读（暴露/排除 P1-3）
python -c "
import json; m=json.load(open('task_allocation/experiments/exp01_smoke/results/metrics.json',encoding='utf-8'))
def scan(cs):
    f2=sum(float(c['muas'].get('f2_cost',0) or 0) for c in cs)
    pen=sum(float(c['muas'].get('penalty',0) or 0) for c in cs)
    fp=sum(float(c['muas'].get('final_path_m',0) or 0) for c in cs)
    return f2-pen, fp
print(scan(m['cycles']))
for sd,v in sorted(m.get('per_seed',{}).items()): print(sd, scan(v['cycles']))"

# 审计覆盖面（暴露 P0-1）
grep -n "per_seed" task_allocation/experiments/exp01_smoke/audit_correctness.py   # 应为空
```
