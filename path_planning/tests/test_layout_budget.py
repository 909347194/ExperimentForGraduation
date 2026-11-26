# -*- coding: utf-8 -*-
"""布局预算：库层单文件 ≤ 330 行（硬约束，不是建议）。

为什么用测试守：行数预算是"可读性/替换成本"的代理指标，人守必然松动——每加一个
特例都会显得合理，三个季度后就没人再看了。参考实现 pp1 用测试锁住 contract ≤120、
core ≤300；本项目只锁一条：库层没有超过 330 行的文件。

范围：``path_planning/common``、``path_planning/methods``、``path_planning/api.py``。
不含 ``tests``（测试按主题聚合，行数与职责边界无关），也不含 ``experiments``
（一实验一目录、彼此不共享代码，行数上限由各自目录的规范管；把它们纳进来会逼着
实验脚本为了数字而拆文件，反而降低可读性）。

超限时的处置不是"删注释"或"合并行"：按职责拆文件，把被拆出的部分在**同一个包**
内以新模块暴露，公共 API 通过原模块再导出，调用方不必改 import。
"""

from __future__ import annotations

import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LIB_ROOT = _PROJECT_ROOT / "path_planning"
_LIMIT = 330

#: 受约束的目录（严格只要库层）
_SCOPE_DIRS = (_LIB_ROOT / "common", _LIB_ROOT / "methods")
_SCOPE_FILES = (_LIB_ROOT / "api.py",)


def _sources() -> list[Path]:
    files: list[Path] = [p for d in _SCOPE_DIRS for p in sorted(d.rglob("*.py"))]
    files.extend(_SCOPE_FILES)
    return [p for p in files if p.is_file()]


def _line_count(path: Path) -> int:
    return sum(1 for _ in path.open(encoding="utf-8"))


class TestLineBudget(unittest.TestCase):
    def test_library_files_within_budget(self) -> None:
        offenders = [
            (p.relative_to(_PROJECT_ROOT).as_posix(), _line_count(p))
            for p in _sources()
            if _line_count(p) > _LIMIT
        ]
        self.assertEqual(
            offenders, [],
            f"以下文件超过 {_LIMIT} 行，请按职责拆分：{offenders}",
        )

    def test_scope_is_not_empty(self) -> None:
        """防止路径写错导致测试空转（空列表永远"通过"）。"""
        self.assertGreater(len(_sources()), 40)


if __name__ == "__main__":
    unittest.main()
