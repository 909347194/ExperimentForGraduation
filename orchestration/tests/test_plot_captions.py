# -*- coding: utf-8 -*-
"""图注文档的守门测试：每张图都要有图注/读法/待澄清，且文档不漏图。"""

from __future__ import annotations

from orchestration.experiments.exp01_smoke.plot import captions, registry


def test_every_figure_declares_caption_reading_clarify():
    for f in registry.FIGURES:
        assert f.caption, f"{f.id} 缺图注"
        assert len(f.reading) >= 2, f"{f.id} 读法要点不足 2 条"
        assert f.clarify, f"{f.id} 缺「需要澄清」"


def test_captions_markdown_covers_all_figures(tmp_path):
    p = captions.write(tmp_path / "CAPTIONS.md")
    text = p.read_text(encoding="utf-8")
    assert "| 图 id | 文件 | 层 | 图题 | 图注 |" in text
    for f in registry.FIGURES:
        assert f"### {f.id}" in text
        assert f.title in text
        assert f.caption in text
    # 术语口径：不使用「票」
    assert "票" not in text
