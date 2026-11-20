# -*- coding: utf-8 -*-
"""config.py — exp04 yaml 加载、深合并与 CLI 覆盖解析。

职责：读 config.yaml → 深合并 --set 覆盖 → 校验四臂定义 → 返回 raw_cfg。
边界：只搬运配置，不发明数值、不构建对象（归 run.py）。
相关：exp02_selection_scale/config.py（同语义移植）。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from paths import EXP_DIR


def load_config(path: Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else (EXP_DIR / "config.yaml")
    if yaml is None:
        raise RuntimeError("PyYAML 未安装，无法读取配置")
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError(f"配置文件不是映射：{p}")
    return cfg


def _coerce(v: str):
    s = v.strip()
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def apply_dotted_set(cfg: dict, items) -> None:
    """把 ``--set dotted.path=value`` 深合并进 cfg（与 exp02 同语义）。"""
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"--set 需要 dotted.path=value 形式：{it!r}")
        path, raw = it.split("=", 1)
        parts = [x for x in path.strip().split(".") if x]
        if not parts:
            raise ValueError(f"--set 路径为空：{it!r}")
        node = cfg
        for k in parts[:-1]:
            nxt = node.get(k)
            if not isinstance(nxt, dict):
                nxt = {}
                node[k] = nxt
            node = nxt
        node[parts[-1]] = _coerce(raw)


def seed_list(cfg: dict) -> list[int]:
    raw = cfg.get("experiment", {}).get("seeds", [cfg.get("experiment", {}).get("seed", 42)])
    if isinstance(raw, int):
        return [raw]
    out = []
    for v in raw or []:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out or [42]


def enabled_arms(cfg: dict) -> list[dict]:
    arms = cfg.get("arms") or []
    return [a for a in arms if a.get("enabled", True)]


def validate(cfg: dict) -> None:
    arms = enabled_arms(cfg)
    if not arms:
        raise ValueError("没有任何启用的消融臂（arms）")
    names = [str(a.get("name", "")) for a in arms]
    if len(names) != len(set(names)):
        raise ValueError(f"消融臂名称重复：{names}")
    # 每个 override 必须是完整嵌套路径（血泪教训：死键静默不生效）
    valid_roots = {"stage", "problem", "solver", "tasks", "experiment", "data"}
    for a in arms:
        for k in (a.get("overrides") or {}):
            root = str(k).split(".", 1)[0]
            if root not in valid_roots:
                raise ValueError(f"臂 {a.get('name')} 的 override 根非法：{k!r}")
    stage = cfg.get("stage", {}) or {}
    if stage.get("decode_mode") not in ("repair_chain", "constructive"):
        raise ValueError(f"decode_mode 非法：{stage.get('decode_mode')!r}")
    if stage.get("participating_policy") not in (
        "cheapest", "first", "marginal", "demand", "demand_marginal"
    ):
        raise ValueError(f"participating_policy 非法：{stage.get('participating_policy')!r}")


def load_with_overrides(path=None, set_items=None) -> dict[str, Any]:
    cfg = load_config(path)
    apply_dotted_set(cfg, set_items)
    validate(cfg)
    return cfg
