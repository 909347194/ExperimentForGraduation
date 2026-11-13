# -*- coding: utf-8 -*-
"""exp03_event_rolling 目录布局与导入引导。"""

from __future__ import annotations

import sys
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parents[2]
DATA_DIR = ROOT / "task_allocation" / "data"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

__all__ = ["EXP_DIR", "ROOT", "DATA_DIR"]
