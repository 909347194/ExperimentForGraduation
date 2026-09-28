# ============================================================
# .latexmkrc —— 配合 latexmk 一键编译
# 用法：latexmk -xelatex main.tex
# ============================================================
$pdf_mode  = 5;          # 5 = xelatex 生成 PDF
$bib_program = 'biber';  # 参考文献后端
$clean_ext = 'bbl bcf run.xml nav snm out synctex.gz';
$recorder  = 1;
