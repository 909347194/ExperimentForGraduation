# 中文核心期刊论文模板(manuscript1_journal_article)

多无人机**任务分配**方向的中文核心期刊论文写作工程(内容均为占位,待填充)。

> **样式参考**：《系统工程与电子技术》《信息与控制》等中文核心期刊的投稿版式
> （通栏排版、五号宋体正文、黑体标题、GB/T 7714 顺序编码制参考文献、
> 文末收稿日期/作者简介、首页页脚基金/通信作者）。
> **投稿前务必以目标期刊官网的“稿件模板/征稿简则”逐项核对**，格式细节各刊不同。

## 两刊要求对照（2026-11 核对）

| 项目 | 《系统工程与电子技术》 | 《信息与控制》 | 本模板默认 |
| --- | --- | --- | --- |
| 中文题名 | ≤ 20 字 | 简洁、避免缩略语/公式 | ≤ 20 字 |
| 英文题名 | 实词 ≤ 10 个 | 与中文对应 | 实词 ≤ 10 个 |
| 中文摘要 | 150～250 字 | ≤ 500 字 | 150～250 字（取严） |
| 关键词 | 4～8 个 | 3～6 个（建议 EI 主题词） | 4～6 个（取严） |
| 基金项目 | **首页页脚**（标准名称+编号） | — | 首页脚注（\blfootnote） |
| 通信作者 | — | **首页页脚**（姓名，E-mail） | 首页脚注（\blfootnote） |
| 作者简介 | 文后，所有作者：出生年月、性别、职称、学位、研究方向、E-mail | 文后，体例类似 | 文末（backmatter/author_bio） |
| 图表题 | 中文 | 中英双语 | 中文（\caption* 加英文行） |
| 致谢 | — | 正文后、参考文献前 | 可选块（backmatter/acknowledgements） |
| 参考文献 | GB/T 7714（简则写 2005），按引用顺序 | 前 3 作者后“等”；中文文献附英文译文 | gb7714-2015 顺序编码制 |

信息来源：《系统工程与电子技术》官网投稿须知（sys-ele.com，2025-09）；
《信息与控制》投稿格式要求（期刊VIP 1002-0411 核对页，2026-11）。

⚠️ 投稿提醒：

- 《系统工程与电子技术》需**保密审查证明/投稿承诺书**（第一单位保密部门盖章），提前办理；
- 《信息与控制》投稿载体为 **Word 双栏**（有官方 word 模板），本 LaTeX 工程用于写作与定稿内容，
  投稿前按其 word 模板转换排版；
- 物理量/单位遵循 GB 3100～3102-1993：变量斜体、矢量矩阵黑斜体（\bm）、集合黑正体、
  转置符号 T 正体（用 \transpose）；
- 投稿前重新对照两刊官网最新“征稿简则”，本对照表可能滞后。

## 论文核心

**只写任务分配(task allocation)**。航迹规划仅作为任务分配的输入假设
(如"航程/飞行时间由预估模块给出")出现,不单独成章展开。

## 目录结构

```text
manuscript1_journal_article/
├── main.tex                  # 主文档入口(元信息、章节装配)
├── Makefile                  # 编译脚本(xelatex + biber)
├── .latexmkrc                # latexmk 配置
├── README.md                 # 本说明
├── setup/                    # 全局设置(与内容解耦)
│   ├── preamble.tex          #   宏包与依赖(含 biblatex-gb7714-2015)
│   ├── fonts.tex             #   字体(英文 = Times New Roman)
│   ├── commands.tex          #   \placeholder 占位命令 / 定理环境 / 算法汉化
│   └── format.tex            #   版面(页边距 / 标题 / 图表题 / 公式编号)
├── frontmatter/              # 首页信息
│   ├── title_info.tex        #   标题 / 作者 / 单位 / 中图分类号·文献标志码·DOI
│   ├── abstract_cn.tex       #   中文摘要 + 关键词
│   └── abstract_en.tex       #   英文题名 / Abstract / Keywords
├── sections/                 # 正文(按节拆分,一节一文件)
│   ├── s01_introduction.tex          # 1 引言
│   ├── s02_problem_formulation.tex   # 2 问题描述与系统模型
│   ├── s03_task_allocation.tex       # 3 多无人机任务分配方法(★核心)
│   ├── s04_experiments.tex           # 4 仿真实验与结果分析
│   └── s05_conclusion.tex            # 5 结论
├── backmatter/
│   ├── acknowledgements.tex  #   致谢（可选；正文后、参考文献前）
│   └── author_bio.tex        #   收稿日期 / 作者简介
├── bib/
│   └── references.bib        # 参考文献(GB/T 7714-2015)
├── figures/                  # 插图(PDF/PNG/EPS)
└── tables/                   # 表格源文件(三线表,\input 引入)
```

## 编译

```bash
make            # xelatex -> biber -> xelatex -> xelatex
# 或
latexmk -xelatex main.tex
```

依赖:TeX Live(xelatex、biber)+ `ctex` + `biblatex-gb7714-2015`。
Linux/macOS 无 Windows 字体时,临时把 `main.tex` 中 `fontset=windows`
改为 `fontset=fandol` 调试(**正式投稿保持 windows 并在 Windows/Overleaf 编译**)。

## 占位约定

- 所有待写内容统一用 `\placeholder{...}` 标出(灰色斜体,形如【占位:...】);
- 每个 `.tex` 文件头部的 `%` 注释块是**写作要点**,成稿后可保留或删除;
- 终稿前全文搜索 `【占位】` / `\placeholder` 清零。

## 写作顺序建议

1. `s02_problem_formulation`(模型先行,符号表定稿)
2. `s03_task_allocation`(核心方法,同步画框架图/伪代码)
3. `s04_experiments`(实验口径与模型约束一一对应)
4. `s01_introduction`(最后写,贡献点与正文对齐)
5. 摘要(中英)→ 结论 → 核对参考文献格式
