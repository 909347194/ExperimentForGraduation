# -*- coding: utf-8 -*-
"""确保无论以哪种方式调用 pytest，都能 import 顶层 path_planning 包。

pytest 的 prepend 导入模式只会把测试文件所在目录加进 sys.path，而不会加仓库根目录；
本文件在收集测试前把仓库根插入，使 `from path_planning...` 的绝对导入可用。
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
