# -*- coding: utf-8 -*-
"""文本坐标解析与生成（``29.6472N 91.136532E`` 一类写法）。

职责：把机巢 CSV / 配置文件里的坐标文本解析成十进制度，或反向生成。
边界：只处理"度分秒 + 半球字母"这一层，不做投影、不碰椭球。
相关：``geodesy.py``（椭球/UTM 换算）、``coordinates.py``（局部 ENU 平面）。

为什么单独成文件：这一层是纯字符串处理，零数值依赖；与椭球换算混在一个文件里
会让"改解析容错"和"改投影级数"两种改动互相踩，也让文件越过 330 行预算。
"""

from __future__ import annotations

import re

__all__ = ["format_coordinate", "parse_coordinate", "parse_coordinates"]

# 去掉度分秒符号两侧空白，便于按空白/逗号切分
_SPACE_RE = re.compile(r"\s*([°'′\"″])\s*")

_ANGLE_RE = re.compile(
    r"^(?:(?P<h_pre>[NSEW]))?\s*"
    r"(?P<sgn>[+-])?\s*"
    r"(?P<deg>\d{1,3}(?:\.\d+)?)\s*"
    r"(?:[°]\s*(?:(?P<min>\d{1,2}(?:\.\d+)?)\s*"
    r"(?:['′]\s*(?:(?P<sec>\d{1,2}(?:\.\d+)?)\s*[\"″]?)?)?)?)?\s*"
    r"(?:(?P<h_post>[NSEW]))?$",
    re.IGNORECASE,
)

_FIELD_SPLIT_RE = re.compile(r"[\s,;]+")


def _parse_angle(token: str) -> tuple[float, str]:
    """把单个角度片段解析为（绝对值, 半球字母）。

    支持：``29.6472N`` / ``N29.6472`` / ``29.6472`` /
    ``29°38'49.9"N`` / ``29°38'49.9N``。
    """
    m = _ANGLE_RE.match(token.strip())
    if not m:
        raise ValueError(f"无法解析角度片段：{token!r}")
    deg = float(m.group("deg"))
    minutes = float(m.group("min") or 0.0)
    seconds = float(m.group("sec") or 0.0)
    if minutes >= 60.0 or seconds >= 60.0:
        raise ValueError(f"分/秒必须小于 60：{token!r}")
    value = deg + minutes / 60.0 + seconds / 3600.0
    hemi = (m.group("h_pre") or m.group("h_post") or "").upper()
    if m.group("sgn") == "-":
        value = -value
    elif hemi in ("S", "W"):
        value = -abs(value)
    return value, hemi


def parse_coordinate(text: str) -> tuple[float, float]:
    """解析一条坐标文本，返回 ``(纬度, 经度)``（十进制度）。

    支持写法::

        "29.6472N 91.136532E"   # 机巢数据 CSV 的格式
        "29.6472, 91.136532"
        "N29.6472 E91.136532"
        "29.6472 91.136532"     # 默认前纬后经
        "29°38'49.9\"N 91°8'11.5\"E"

    负值或 ``S`` / ``W`` 表示南纬 / 西经。
    """
    if not isinstance(text, str):
        raise TypeError(f"期望字符串，得到 {type(text).__name__}")
    cleaned = _SPACE_RE.sub(r"\1", text.strip())
    tokens = [t for t in _FIELD_SPLIT_RE.split(cleaned) if t]
    if len(tokens) != 2:
        raise ValueError(f"期望恰好两个角度片段，得到 {len(tokens)} 个：{text!r}")

    parsed = [_parse_angle(t) for t in tokens]
    lat = lon = None
    for (value, hemi), token in zip(parsed, tokens):
        if hemi in ("N", "S"):
            if lat is not None:
                raise ValueError(f"纬度重复：{text!r}")
            lat = value
        elif hemi in ("E", "W"):
            if lon is not None:
                raise ValueError(f"经度重复：{text!r}")
            lon = value

    if lat is None and lon is None:  # 无半球：前纬后经
        lat, lon = parsed[0][0], parsed[1][0]
    elif lat is None:
        lon_val = lon
        lat = parsed[0][0] if parsed[1][1] in ("E", "W") else parsed[1][0]
        lon = lon_val
    elif lon is None:
        lon = parsed[0][0] if parsed[1][1] in ("N", "S") else parsed[1][0]

    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"纬度超出 [-90, 90]：{lat}（{text!r}）")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"经度超出 [-180, 180]：{lon}（{text!r}）")
    return lat, lon


def parse_coordinates(text: str) -> list[tuple[float, float]]:
    """按行解析多条坐标，跳过空行，返回 ``[(lat, lon), ...]``。"""
    result: list[tuple[float, float]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        result.append(parse_coordinate(line))
    return result


def format_coordinate(lat: float, lon: float, ndigits: int = 6) -> str:
    """``parse_coordinate`` 的逆操作，生成 "29.647200N 91.136532E"。"""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.{ndigits}f}{ns} {abs(lon):.{ndigits}f}{ew}"
