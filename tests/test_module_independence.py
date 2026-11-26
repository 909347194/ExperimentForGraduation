# -*- coding: utf-8 -*-
"""模块边界的守卫测试：两个方法包互不 import，且都不 import 编排层。

职责：把"task_allocation ✗ path_planning、path_planning ✗ task_allocation、
      两侧 ✗ orchestration"从文档约定变成可执行的断言。
边界：只做静态 import 扫描（AST），不 import 任何被测包；`orchestration` 允许
      import 两侧（那就是它的职责），不在此约束。
相关：README.md §5「代码规范」、`orchestration/protocol.py`。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: 包 → 不得 import 的顶层模块
_FORBIDDEN = {
    "task_allocation": ("path_planning", "orchestration"),
    "path_planning": ("task_allocation", "orchestration"),
}


def _imported_roots(path: Path) -> set[str]:
    """返回该文件 import 的顶层模块名（包内相对 import 不算）。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


class TestModuleIndependence(unittest.TestCase):
    def test_no_cross_package_imports(self) -> None:
        violations = []
        for pkg, forbidden in _FORBIDDEN.items():
            in_pkg = set(forbidden)
            for py in (_ROOT / pkg).rglob("*.py"):
                hits = _imported_roots(py) & in_pkg
                if hits:
                    violations.append(f"{py.relative_to(_ROOT)} → {sorted(hits)}")
        self.assertEqual(violations, [], "出现跨包 import：" + "; ".join(violations))

    def test_data_lives_at_repo_root(self) -> None:
        self.assertTrue((_ROOT / "data").is_dir(), "仓库根 data/ 不存在")
        self.assertTrue((_ROOT / "data" / "guangzhou").is_dir())
        for stale in ("task_allocation/data", "path_planning/data"):
            self.assertFalse((_ROOT / stale).exists(), f"数据目录仍未上提：{stale}")


if __name__ == "__main__":
    unittest.main()
