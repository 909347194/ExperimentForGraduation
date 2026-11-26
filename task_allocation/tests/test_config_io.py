# -*- coding: utf-8 -*-
"""配置读取统一入口：YAML 原样兼容、JSON/JSONC 注释与尾逗号可用。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from task_allocation.common.config_io import load_config_mapping


class TestConfigIo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, text: str) -> Path:
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_yaml_still_works(self) -> None:
        p = self._write("config.yaml", "experiment:\n  seed: 42\nmuas:\n  lambda_cost: 0.01\n")
        self.assertEqual(
            load_config_mapping(p), {"experiment": {"seed": 42}, "muas": {"lambda_cost": 0.01}}
        )

    def test_plain_json(self) -> None:
        p = self._write("config.json", '{"experiment": {"seed": 42}}')
        self.assertEqual(load_config_mapping(p), {"experiment": {"seed": 42}})

    def test_jsonc_comments_and_trailing_commas(self) -> None:
        text = """{
            // 行注释
            "problem": {
                /* 块注释
                   跨两行 */
                "n_tasks": 30,
                "note": "https://example.com//path", // 字符串内 // 不能删
            },
        }
        """
        p = self._write("config.jsonc", text)
        self.assertEqual(load_config_mapping(p), {
            "problem": {"n_tasks": 30, "note": "https://example.com//path"}
        })

    def test_bad_suffix_and_non_mapping(self) -> None:
        p = self._write("config.toml", "x = 1\n")
        with self.assertRaises(ValueError):
            load_config_mapping(p)
        q = self._write("list.jsonc", "// 顶层不是映射\n[1, 2,]\n")
        with self.assertRaises(ValueError):
            load_config_mapping(q)

    def test_malformed_jsonc_reports_path(self) -> None:
        p = self._write("bad.jsonc", '{"a": }')
        with self.assertRaises(ValueError) as ctx:
            load_config_mapping(p)
        self.assertIn(p.name, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
