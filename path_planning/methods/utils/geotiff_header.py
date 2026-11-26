# -*- coding: utf-8 -*-
"""GeoTIFF 头解析（纯 struct，不读栅格数据）：标签 + 仿射变换 + EPSG + 空洞值。

职责：读第一个 IFD 的标签，得到 ``GeoTransform``、宽高、EPSG、nodata。
边界：不解码像素、不采样高程（``dem_geotiff.GeoTIFFDEM`` 负责）；无 GDAL 依赖。
相关：``dem_geotiff.py``（本模块的唯一消费者）、``coordinates.py``（坐标约定）。

为什么单独成文件：标签读取是"字节级脏活"，与 DEM 的采样/重采样逻辑没有耦合；
混在一起会让单个文件越过 330 行预算，也让读代码的人先穿过 100 行 struct 才能
看到 DEM 的行为。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

__all__ = ["GeoTransform", "parse_header"]

# --- GeoTIFF 标签号 -------------------------------------------------------
_TAG_IMAGEWIDTH = 256
_TAG_IMAGELENGTH = 257
_TAG_BITSPERSAMPLE = 258
_TAG_SAMPLESPERPIXEL = 277
_TAG_SAMPLEFORMAT = 339
_TAG_MODELPIXELSCALE = 33550
_TAG_MODELTRANSFORMATION = 33551
_TAG_MODELTIEPOINT = 33922
_TAG_GEOKEYDIRECTORY = 34735
_TAG_GEODOUBLEPARAMS = 34736
_TAG_GEOASCIIPARAMS = 34737
_TAG_GDAL_NODATA = 42113

_GEOKEY_PROJECTED_CS = 3072
_GEOKEY_GEOGRAPHIC_CS = 2048

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
_TYPE_FMT = {3: "H", 4: "I", 5: "Q", 11: "f", 12: "d"}


@dataclass(frozen=True)
class GeoTransform:
    """与 GDAL ``GetGeoTransform`` 同口径的仿射参数。

    ``lon = c + col * xres``，``lat = f + row * yres``（col / row 为像素角点索引）。
    """

    c: float  # 左上角经度
    xres: float  # 每像素经度跨度（度）
    f: float  # 左上角纬度
    yres: float  # 每像素纬度跨度（度，通常为负值的相反数）

    def to_gdal(self) -> tuple[float, float, float, float, float, float]:
        """转成 GDAL 六元组 ``(c, xres, 0, f, 0, -yres)``。"""
        return (self.c, self.xres, 0.0, self.f, 0.0, -self.yres)

    @classmethod
    def from_gdal(cls, gt: Sequence[float]) -> "GeoTransform":
        return cls(c=float(gt[0]), xres=float(gt[1]), f=float(gt[3]), yres=abs(float(gt[5])))


def _read_tiff_tags(path: str | Path) -> dict[int, tuple[int, int, bytes, object]]:
    """读取第一个 IFD 的全部标签，返回 {tag: (type, count, value_bytes, 字节序)}。"""
    with open(path, "rb") as fh:
        header = fh.read(8)
        if len(header) < 8:
            raise ValueError(f"文件过短，不是 TIFF：{path}")
        if header[:2] == b"II":
            endian = "<"
        elif header[:2] == b"MM":
            endian = ">"
        else:
            raise ValueError(f"非法 TIFF 字节序标记：{header[:2]!r}")
        magic = struct.unpack(endian + "H", header[2:4])[0]
        if magic == 43:
            raise NotImplementedError("暂不支持 BigTIFF")
        if magic != 42:
            raise ValueError(f"非法 TIFF magic：{magic}")

        ifd_offset = struct.unpack(endian + "I", header[4:8])[0]
        fh.seek(ifd_offset)
        (count,) = struct.unpack(endian + "H", fh.read(2))
        tags: dict[int, tuple[int, int, bytes, object]] = {}
        for _ in range(count):
            entry = fh.read(12)
            if len(entry) < 12:
                raise ValueError("IFD 条目被截断")
            tag, typ, num = struct.unpack(endian + "HHI", entry[:8])
            tags[tag] = (typ, num, entry[8:12], endian)
        return tags


def _tag_values(tags: dict, tag: int, path: str | Path) -> tuple:
    """把某个标签的值读成 python 元组（自动处理内联/偏移）。"""
    if tag not in tags:
        raise KeyError(f"缺少 GeoTIFF 标签 {tag}")
    typ, num, value_bytes, endian = tags[tag]
    size = _TYPE_SIZE.get(typ)
    fmt = _TYPE_FMT.get(typ)
    if size is None or fmt is None:
        raise NotImplementedError(f"标签 {tag} 的类型 {typ} 暂不支持")
    nbytes = size * num
    if nbytes <= 4:
        raw = value_bytes[:nbytes]
    else:
        (offset,) = struct.unpack(endian + "I", value_bytes)
        with open(path, "rb") as fh:
            fh.seek(offset)
            raw = fh.read(nbytes)
    return struct.unpack(endian + f"{num}{fmt}", raw)


def _tag_ascii(tags: dict, tag: int, path: str | Path) -> str:
    if tag not in tags:
        raise KeyError(tag)
    typ, num, value_bytes, endian = tags[tag]
    nbytes = num  # ASCII 每字符 1 字节
    if nbytes <= 4:
        raw = value_bytes[:nbytes]
    else:
        (offset,) = struct.unpack(endian + "I", value_bytes)
        with open(path, "rb") as fh:
            fh.seek(offset)
            raw = fh.read(nbytes)
    return raw.split(b"\x00")[0].decode("ascii", errors="replace").strip()


def parse_header(
    path: str | Path,
) -> tuple[GeoTransform, int, int, int | None, float | None]:
    """读头：``(仿射变换, 宽, 高, EPSG, nodata)``。"""
    tags = _read_tiff_tags(path)

    (width,) = _tag_values(tags, _TAG_IMAGEWIDTH, path)
    (height,) = _tag_values(tags, _TAG_IMAGELENGTH, path)
    width, height = int(width), int(height)

    if _TAG_MODELTRANSFORMATION in tags:
        matrix = _tag_values(tags, _TAG_MODELTRANSFORMATION, path)
        transform = GeoTransform(
            c=matrix[3], xres=matrix[0], f=matrix[7], yres=abs(matrix[5])
        )
    elif _TAG_MODELPIXELSCALE in tags and _TAG_MODELTIEPOINT in tags:
        scale = _tag_values(tags, _TAG_MODELPIXELSCALE, path)
        tie = _tag_values(tags, _TAG_MODELTIEPOINT, path)
        if len(tie) < 6:
            raise ValueError(f"ModelTiepoint 长度异常：{len(tie)}")
        # tie = (i, j, k, X, Y, Z)：像素 (i, j) 对应地图坐标 (X, Y)
        sx, sy = float(scale[0]), float(scale[1])
        px, py, _, map_x, map_y, _ = (float(v) for v in tie[:6])
        transform = GeoTransform(
            c=map_x - px * sx,
            xres=sx,
            f=map_y + py * sy,
            yres=sy,
        )
    else:
        raise ValueError("缺少地理参考标签（ModelPixelScale / ModelTiepoint）")

    epsg: int | None = None
    if _TAG_GEOKEYDIRECTORY in tags:
        keys = _tag_values(tags, _TAG_GEOKEYDIRECTORY, path)
        num_keys = int(keys[3])
        for idx in range(num_keys):
            base = 4 + 4 * idx
            key_id, location, _count, value = (
                int(keys[base]),
                int(keys[base + 1]),
                int(keys[base + 2]),
                int(keys[base + 3]),
            )
            if location == 0 and key_id in (_GEOKEY_PROJECTED_CS, _GEOKEY_GEOGRAPHIC_CS):
                if value not in (32767, 0):  # 32767 = user-defined
                    epsg = value
                    break

    nodata: float | None = None
    if _TAG_GDAL_NODATA in tags:
        try:
            text = _tag_ascii(tags, _TAG_GDAL_NODATA, path)
            nodata = float(text) if text not in ("", "nan") else float("nan")
        except ValueError:
            nodata = None
    elif epsg in (4326, 4269, 4258):  # 地理坐标系 DEM 惯用 -9999 作空洞值
        nodata = -9999.0

    return transform, width, height, epsg, nodata
