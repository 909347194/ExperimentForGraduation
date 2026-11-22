# -*- coding: utf-8 -*-
"""``AllocatorPort`` 的具体实现：目前只有"定点投放 + 最近空闲机"这一种。"""

from closed_loop.allocators.cyclic import CyclicTaskAllocator, LoopTask, TourAssignment

__all__ = ["CyclicTaskAllocator", "LoopTask", "TourAssignment"]
