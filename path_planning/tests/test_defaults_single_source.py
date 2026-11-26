# -*- coding: utf-8 -*-
"""默认值唯一源：``PlanParams`` 是库层默认值的唯一定义处，实验配置只能"显式覆盖"。

为什么需要这条：默认值散在两处（dataclass 默认 + YAML 手填）时，改一处忘另一处
不会报错，只会让"默认"这个词失去意义——实验跑出来的到底是库默认还是配置里的旧值，
没人说得清。参考实现 pp1 的 ``contract/defaults.py`` 是唯一源，并有
``test_pp1_defaults`` 逐项比对；本项目沿用同一口径：

1. 实验 YAML 的 ``planning`` 段**键集必须等于** ``PlanParams`` 的字段集
   （不多不少——多了是错键，少了会悄悄落到库默认）。
2. 每个键的值要么**等于库默认**，要么出现在本文件的"有意覆盖"清单里；
   清单里多出一项（不再覆盖）同样报错，保证清单与现实同步。
"""

from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path

from path_planning.common.config_io import load_config_mapping
from path_planning.common.plan_params import PlanParams

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 各实验**有意**偏离库默认的参数（必须写明；清单一变测试就叫）
_INTENDED_OVERRIDES: dict[str, dict[str, str]] = {
    "exp01_smoke": {
        "n_nodes": "城市 km 级航段要 120 节点，库里 60 是保守默认",
        "max_iter": "本场景迭代需求高于库默认 200（实测 400 才稳）",
    },
    "exp07_two_rounds": {
        "n_nodes": "同上：km 级航段 120 节点",
        "max_iter": "同上：400",
        "dt_min_s": "该实验保留 0.3 的时间地板以复现旧口径（05 口径只在 exp01）",
    },
}


def _experiment_configs() -> dict[str, dict]:
    configs: dict[str, dict] = {}
    exp_dir = _PROJECT_ROOT / "path_planning" / "experiments"
    paths = sorted(
        p for ext in ("yaml", "yml", "json", "jsonc")
        for p in exp_dir.glob(f"*/config.{ext}")
    )
    for path in paths:
        raw = load_config_mapping(path)
        if "planning" in raw:
            configs[path.parent.name] = raw["planning"]
    return configs


def _defaults() -> dict[str, object]:
    base = PlanParams()
    return {f.name: getattr(base, f.name) for f in dataclasses.fields(PlanParams)}


class TestDefaultsSingleSource(unittest.TestCase):
    def test_plan_params_is_the_only_table(self) -> None:
        """库层默认值必须全部由 ``PlanParams`` 给出（无 None、无哨兵）。"""
        defaults = _defaults()
        self.assertGreater(len(defaults), 30)
        for name, value in defaults.items():
            self.assertIsNotNone(value, f"{name} 的库默认缺失")

    def test_experiments_cover_all_fields(self) -> None:
        configs = _experiment_configs()
        self.assertIn("exp01_smoke", configs, "没有读到 exp01_smoke 的 config.yaml")
        fields = set(_defaults())
        for name, planning in configs.items():
            self.assertEqual(sorted(fields - set(planning)), [],
                             f"{name} 的 planning 段缺字段（会悄悄落到库默认）")
            self.assertEqual(sorted(set(planning) - fields), [],
                             f"{name} 的 planning 段出现非 PlanParams 字段")

    def test_overrides_are_explicit_and_exact(self) -> None:
        configs = _experiment_configs()
        for name, planning in configs.items():
            allowed = _INTENDED_OVERRIDES.get(name, {})
            self.assertEqual(sorted(set(planning) - set(_defaults())), [], name)
            drifted = []
            for key, lib_default in _defaults().items():
                yaml_value = planning[key]
                if isinstance(lib_default, tuple):
                    yaml_value = tuple(yaml_value)
                same = (
                    abs(float(yaml_value) - float(lib_default)) < 1e-12
                    if isinstance(lib_default, float) and isinstance(yaml_value, (int, float))
                    else yaml_value == lib_default
                )
                if not same and key not in allowed:
                    drifted.append((key, lib_default, yaml_value))
            self.assertEqual(drifted, [], f"{name} 有未申报的默认值漂移：{drifted}")

    def test_override_list_not_stale(self) -> None:
        """清单里列了但配置其实等于库默认 → 清单过期，同样算错。"""
        configs = _experiment_configs()
        for name, allowed in _INTENDED_OVERRIDES.items():
            planning = configs.get(name)
            if planning is None:
                continue
            stale = [k for k in allowed if k in _defaults() and planning[k] == _defaults()[k]]
            self.assertEqual(stale, [], f"{name} 的覆盖清单已过期：{stale}")


if __name__ == "__main__":
    unittest.main()
