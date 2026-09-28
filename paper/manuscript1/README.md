# 硕士学位论文 LaTeX 工程（manuscript1）

多无人机智能调度中的任务分配与航迹规划方法研究 —— 论文写作工程模板。

> **排版规范来源**：《中国地质大学（武汉）研究生学位论文写作规范》（研究生院，2025 年 5 月）。
> 所有标题/段落/图表/目录/页眉页码/封面题名页等格式均按该规范实现，详见 `setup/format.tex`。

## 目录结构

```text
manuscript1/
├── main.tex                  # 主文档入口（论文基本信息、章节装配）
├── Makefile                  # 编译脚本（xelatex + biber）
├── .latexmkrc                # latexmk 配置
├── setup/                    # 全局设置（与内容解耦，便于维护）
│   ├── preamble.tex          #   宏包与依赖
│   ├── fonts.tex             #   字体（英文 = Times New Roman）
│   ├── commands.tex          #   自定义命令 / 定理环境 / 算法汉化
│   └── format.tex            #   排版规范（页面/页眉页脚/标题/段落/图表题/公式编号/目录/摘要/图表目录）
├── frontmatter/              # 前置部分（按学位论文组成顺序装配）
│   ├── cover.tex                     #   1. 中文封面
│   ├── titlepage_cn.tex              #   2. 中文题名页（含中图分类号/密级/UDC/单位代码）
│   ├── titlepage_en.tex              #   3. 英文题名页
│   ├── declaration.tex               #   4. 学位论文原创性声明
│   ├── supervisor_commitment.tex     #   5. 研究生学位论文导师承诺书
│   ├── authorization.tex             #   6. 学位论文使用授权书
│   ├── author_bio.tex                #   7. 作者简介
│   ├── defense_committee.tex         #   8. 学位论文答辩委员会名单
│   └── abstract.tex                  #   9. 中文摘要 / 10. Abstract
├── chapters/                 # 正文（第13项）：每章一个文件（命名 = 序号_主题）
│   ├── ch01_introduction.tex          # 第1章 绪论
│   ├── ch02_related_work.tex          # 第2章 相关理论与技术
│   ├── ch03_task_allocation.tex       # 第3章 动态滚动的多机巢多无人机协同任务分配方法
│   ├── ch04_path_planning.tex         # 第4章 顾及风险时空异质性的航迹规划方法
│   ├── ch05_system_implementation.tex # 第5章 智能调度系统实现与应用验证
│   └── ch06_conclusion.tex            # 第6章 总结与展望
├── backmatter/               # 后置部分
│   ├── acknowledgements.tex  #   14. 致谢
│   ├── appendices.tex        #   16. 附录
│   └── publications.tex      #   攻读学位期间成果（可选，置于附录之后）
├── bib/
│   └── references.bib        # 参考文献库（GB/T 7714-2005，顺序编码制）
├── figures/                  # 图片资源（.pdf/.png/.eps）
├── tables/                   # 表格资源（如有）
└── build/                    # 编译中间产物（已被 .gitignore 忽略）
```

## 论文基本信息（在 `main.tex` 顶部修改）

| 变量 | 当前值 |
|---|---|
| 作者 `\thesisAuthor` | 张佳齐 |
| 导师 `\thesisAdvisor` | 郑坤 教授 |
| 学科专业 `\thesisMajor` | 地理学 |
| 培养单位 `\thesisOrg` | 地理与信息工程学院 |
| 学位类别 `\thesisCategory` | 硕士学位论文（学术型硕士） |
| 学校代码 `\thesisSchoolCode` | 10491 |
| 完成时间 `\thesisDate` | 二〇二六年六月（汉字，不用阿拉伯数字） |

> 封面含分类号/UDC/密级/学校代码著录项；题名页含学校代码/研究生学号等，均按规范 3.2/3.4。

## 字体说明

- **英文字体 = Times New Roman**（新罗马）：由 `setup/fonts.tex` 中的
  `\setmainfont{Times New Roman}` 设置；封面"学号"等阿拉伯数字用 `\tnr{}` 强制 TNR。
- **中文字体**：沿用 `ctex` 按操作系统自动选择（Windows 宋体/黑体、macOS 华文、
  Linux Fandol）。如需手动指定，见 `setup/fonts.tex` 末尾注释。
- 请确认本机已安装 **Times New Roman**（Windows 默认具备；Linux/macOS 需另行安装）。

## 参考文献

- 标准：**GB/T 7714-2005**（规范 2.6 明确要求，顺序编码制）。
- 行内引用用 `\cite{key}`；**上标方括号**用 `\supercite{key}`（规范 2.6：序号置于引用处右上角）。
- 依赖：`biblatex-gb7714-2015` 宏包（同时提供 `gb7714-2005` 样式）与 `biber`。

## 编译方式

依赖：TeX Live（含 `xelatex`、`biber`）以及 `ctex`、`biblatex-gb7714-2015`。

```bash
# 方式一：Makefile
make              # 生成 main.pdf
make clean        # 清理中间文件（保留 PDF）
make cleanall     # 清理全部（含 PDF）

# 方式二：latexmk（推荐，自动处理多轮编译与 biber）
latexmk -xelatex main.tex
latexmk -c                    # 清理
```

若在 Linux 上缺少中文字体，可改用 Fandol 字体集（`tlmgr install fandol`）或
将 `main.tex` 的 `fontset=auto` 改为 `fontset=fandol`。

## 规范符合性（对照《中国地质大学（武汉）研究生学位论文写作规范》2025.5）

| 规范条目 | 本工程处理 |
|---|---|
| 1.1 内容组成（16 项） | `main.tex` 已按序装配，覆盖封面→附录全部 16 项 |
| 2.3 章、节 | `ctexset`：章=「第一章」（黑体三号）、节=「1.1」、小节=「1.1.1」 |
| 2.4 页眉页码 | `format.tex` §7：奇页「中国地质大学硕士学位论文」/偶页「作者：题目」，外侧页码，5 号宋体，页眉下划线 |
| 2.5 图/表/表达式 | `format.tex` §3–4：图下表上、五号宋体居中；公式右对齐 `(n)` 式 TNR 五号 |
| 2.6 参考文献 | GB/T 7714-2005（顺序编码制），`\supercite` 上标方括号 |
| 2.7 量和单位 | 已引入 `siunitx`，用 `\qty{值}{单位}` 自动正/斜体与间隙 |
| 3.1 纸张页面 | A4；上/下/左/右 3cm；页眉距顶 2.5cm；页脚距底 2.0cm；装订线 0 |
| 3.2 中文封面 | `cover.tex`：题目黑体二号、学校名/类型宋体一号、学号 TNR 三号、日期汉字 |
| 3.3 书脊 | **印刷阶段要求**（仿宋，上题目/中作者/下学校），非 PDF 模板内容，装订时按此处理 |
| 3.4 中文题名页 | `titlepage_cn.tex`：学校代码/学号/题目（黑体二号）/作者/导师/专业/单位，均宋体三号 |
| 3.5 英文题名页 | `titlepage_en.tex`：A Dissertation Submitted…、Title 二号加粗，全 TNR |
| 3.6 中英文摘要 | `abstract.tex` + `format.tex` §6：摘要/Abstract 小二加粗居中；关键词加粗 |
| 3.7 目录 | `format.tex` §5（titletoc）：章宋体四号、节/小节宋体小四，均 20 磅、页码右对齐 |
| 3.8 正文 | `format.tex` §1–2：标题字号/间距、正文宋体小四 TNR、首行缩进 2 字、固定 20 磅 |
| 3.9 其他部分 | 致谢/参考文献/附录章节样式已随正文机制统一 |
| 3.10 印刷装订 | 文档类 `ctexbook` 默认 `twoside`（双面）+ `openright`（章节起奇数页）；**自中文摘要起双面印刷，之前部分单面印刷**为出片/装订阶段指令，PDF 模板按 twoside 输出即可 |

## 使用建议（最佳实践）

1. **每章一个文件**：在 `chapters/` 下新增/编辑，主文档用 `\input` 装配，互不干扰。
2. **先骨架后填充**：各章已给出 `\section` 大纲，直接补正文即可。
3. **图片放 `figures/`**，用 `\graphicspath` 已配置，引用时只写文件名。
4. **参考文献放 `bib/references.bib`**，正文用 `\cite{key}` 引用。
5. **全局格式只改 `setup/`**：字体、宏包、命令集中管理，避免散落。
6. **版本控制**：编译产物已在 `.gitignore` 忽略，仅提交源文件。
