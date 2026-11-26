# -*- coding: utf-8 -*-
"""配置读取统一入口（task_allocation 侧）：YAML / JSON / JSONC。

职责：按扩展名读配置并统一返回顶层映射；JSON/JSONC 允许 ``//`` 行注释、
``/* */`` 块注释与尾逗号（字符串内的 ``//`` 不误删）。YAML 仍走 PyYAML，
已有实验配置不改一个字也能继续跑。
边界：只做「文本 → 映射」，不解释任何参数语义、不填默认值、不解析路径。
相关：各实验 ``config.py`` 是参数语义与缺省值的唯一处。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["load_config_mapping"]

_SUPPORTED = frozenset({".yaml", ".yml", ".json", ".jsonc"})


def load_config_mapping(path: str | Path) -> dict[str, Any]:
    """读配置文件并返回顶层映射；根节点不是映射或格式不支持都明确报错。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in _SUPPORTED:
        raise ValueError(
            f"不支持的配置格式 {suffix!r}（{p}）；可用 .yaml/.yml/.json/.jsonc"
        )
    text = p.read_text(encoding="utf-8-sig")
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - 环境缺依赖时才会走
            raise RuntimeError("读取 YAML 配置需要 PyYAML") from exc
        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(_strip_json_comments(text))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON/JSONC 解析失败（{p}）：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"配置根节点必须是映射：{p}")
    return data


def _strip_json_comments(text: str) -> str:
    """删 JSONC 注释但保留字符串内容与行号（块注释内换行补回）。"""
    out: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        c = text[i]
        if quote is not None:
            out.append(c)
            if c == "\\" and i + 1 < n:  # 转义：引号不闭合
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in {'"', "'"}:
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] in "\r\n":
                    out.append("\n")
                i += 1
            i = min(i + 2, n)
            continue
        out.append(c)
        i += 1
    if quote is not None:
        raise ValueError(f"JSON/JSONC 字符串未闭合（引号 {quote}）")
    return _strip_trailing_commas("".join(out))


def _strip_trailing_commas(text: str) -> str:
    """删对象/数组最后一个元素后的逗号；字符串内的逗号不动。"""
    out: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        c = text[i]
        if quote is not None:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in {'"', "'"}:
            quote = c
            out.append(c)
            i += 1
            continue
        if c == ",":
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)
