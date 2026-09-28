# ============================================================
# Makefile —— 论文编译
# 用法：
#   make          编译（xelatex -> biber -> xelatex -> xelatex）
#   make clean    清除编译中间产物（保留 PDF）
#   make cleanall 清除全部产物（含 PDF）
# 依赖：TeX Live（含 xelatex、biber）+ ctex、biblatex-gb7714-2015
# ============================================================

TARGET  := main
TEX     := xelatex
BIB     := biber
FLAGS   := -interaction=nonstopmode -halt-on-error

.PHONY: all clean cleanall

all: $(TARGET).pdf

$(TARGET).pdf: $(TARGET).tex
	$(TEX) $(FLAGS) $(TARGET).tex
	$(BIB) $(TARGET)
	$(TEX) $(FLAGS) $(TARGET).tex
	$(TEX) $(FLAGS) $(TARGET).tex

clean:
	rm -f $(TARGET).aux $(TARGET).bbl $(TARGET).bcf $(TARGET).blg \
	      $(TARGET).lof $(TARGET).lot $(TARGET).toc $(TARGET).out \
	      $(TARGET).log $(TARGET).synctex.gz $(TARGET).run.xml \
	      $(TARGET).nav $(TARGET).snm $(TARGET)-blx.bib

cleanall: clean
	rm -f $(TARGET).pdf
