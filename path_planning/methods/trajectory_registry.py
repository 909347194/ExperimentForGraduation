# -*- coding: utf-8 -*-
"""跨周期航迹注册表：谁是"已发布的时空障碍"，谁还能被改。

职责：按 ``uav_id`` 索引已发布航迹（含本轮新规划与跨周期遗留），提供"某时刻哪些
      航迹在飞""某机的占用到何时"两类查询，并在落地后注销。
边界：不规划、不检测、不让路；只维护状态。冲突检测与协调都从这里取输入。
相关：``methods/conflict/conflict_detector.py``（消费者）、``methods/coordination/
      global_recheck.py``（消费者）、``docs/architecture.md`` §1（数据流）。

**为什么需要它**（Q4 = C）：冲突检测的输入不只是本轮新规划的航迹，还包括上一决策
周期已经发布、此刻仍在飞的旧航迹——那才是"既有航迹约束"里的"既有"。没有注册表，
上层每次都得自己拼一份"当前在飞集合"，而拼错一次就是漏检一对冲突。

**同一机号不得有两条未结束航迹**：这不是洁癖。一架机同时存在两条在飞航迹，意味着
它的位置有两个答案，检测层的每一对距离都不再有物理含义。故 ``register`` 一旦发现
旧航迹尚未结束（``old.t_end > new.t0``）就抛错，让上层显式决定是"等它落地"还是
"它其实已经结束了但没结算"。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from path_planning.common.plan_types import Trajectory

__all__ = ["RegisteredTrajectory", "TrajectoryRegistry"]

#: 允许的时间重合容差（秒）。同一航次被重复注册时通常 t_end == 新 t0。
_TOL_S = 1e-6


@dataclass(frozen=True)
class RegisteredTrajectory:
    """一条已注册航迹 + 它的来源标签。

    Attributes:
        trajectory: 航迹本体（含 ``t0`` / ``t_end``）
        source:     来源标签：``planned``（本轮新规划）/ ``legacy``（跨周期遗留）/
                    ``estimate``（降级估算时间）
    """

    trajectory: Trajectory
    source: str = "planned"

    @property
    def uav_id(self) -> int:
        return self.trajectory.uav_id

    @property
    def t_end(self) -> float:
        return self.trajectory.t_end

    def busy_until(self) -> float:
        return self.trajectory.t_end


class TrajectoryRegistry:
    """按机号索引的航迹表（可原地更新，生命周期由 ``orchestration`` 驱动）。"""

    def __init__(self) -> None:
        self._items: dict[int, RegisteredTrajectory] = {}

    # --- 查询 ---------------------------------------------------------

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, uav_id: object) -> bool:
        return uav_id in self._items

    def ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._items))

    def get(self, uav_id: int) -> Trajectory | None:
        item = self._items.get(int(uav_id))
        return None if item is None else item.trajectory

    def source_of(self, uav_id: int) -> str | None:
        item = self._items.get(int(uav_id))
        return None if item is None else item.source

    def trajectories(self) -> tuple[Trajectory, ...]:
        return tuple(self._items[k].trajectory for k in sorted(self._items))

    def busy_until(self, uav_id: int) -> float:
        """该机被占用到何时；未注册返回 0.0（与上层 ``busy_until=0`` 判空闲同口径）。"""
        item = self._items.get(int(uav_id))
        return 0.0 if item is None else item.t_end

    def active_at(self, t: float) -> tuple[Trajectory, ...]:
        """``t`` 时刻在飞的航迹（含端点；半开区间 ``[t0, t_end)`` 在端点处会漏）。"""
        now = float(t)
        return tuple(
            self._items[k].trajectory
            for k in sorted(self._items)
            if self._items[k].trajectory.t0 - _TOL_S <= now <= self._items[k].trajectory.t_end + _TOL_S
        )

    # --- 写入 ---------------------------------------------------------

    def register(self, trajectory: Trajectory, *, source: str = "planned") -> None:
        """注册一条航迹；旧航迹未结束时拒绝（见模块说明）。

        Raises:
            ValueError: 同机旧航迹尚未结束（``old.t_end > new.t0``）。
        """
        uid = int(trajectory.uav_id)
        old = self._items.get(uid)
        if old is not None and old.t_end > float(trajectory.t0) + _TOL_S:
            raise ValueError(
                f"机 {uid} 已有未结束航迹（t_end={old.t_end:.3f}），"
                f"不能注册 t0={trajectory.t0:.3f} 的新航迹"
            )
        self._items[uid] = RegisteredTrajectory(trajectory=trajectory, source=str(source))

    def replace(self, trajectory: Trajectory, *, source: str | None = None) -> None:
        """协调层让路后原地替换（同机、同 ``t0``、同来源），不触发"未结束"检查。

        为什么单独开一个口子：让路产物的 ``t_end`` 会因为时长重解而变长，若走
        ``register`` 会被自己的上一条挡下来；而"替换正在被协调的那条"本来就是它的
        语义，不需要上层额外注销。
        """
        uid = int(trajectory.uav_id)
        old = self._items.get(uid)
        src = (old.source if old is not None else "planned") if source is None else source
        self._items[uid] = RegisteredTrajectory(trajectory=trajectory, source=src)

    def unregister(self, uav_id: int) -> Trajectory | None:
        item = self._items.pop(int(uav_id), None)
        return None if item is None else item.trajectory

    def prune(self, now: float) -> tuple[int, ...]:
        """注销 ``t_end <= now`` 的航迹，返回被清掉的机号（落地结算）。"""
        expired = tuple(
            uid for uid in sorted(self._items) if self._items[uid].t_end <= float(now) + _TOL_S
        )
        for uid in expired:
            del self._items[uid]
        return expired

    # --- 导出 ---------------------------------------------------------

    def snapshot(self) -> tuple[RegisteredTrajectory, ...]:
        """按机号排序的只读快照（写日志 / 跨进程同步用）。"""
        return tuple(self._items[k] for k in sorted(self._items))

    def with_shift(self, delta_s: float) -> tuple[Trajectory, ...]:
        """整体平移后的航迹（时间基准切换用；几何不变，只改 ``t0/t1``）。"""
        shift = float(delta_s)
        if shift == 0.0:
            return self.trajectories()
        return tuple(_shift_trajectory(t, shift) for t in self.trajectories())


def _shift_trajectory(traj: Trajectory, delta_s: float) -> Trajectory:
    segments = tuple(
        replace(seg, t0=seg.t0 + delta_s, t1=seg.t1 + delta_s) for seg in traj.segments
    )
    return replace(traj, segments=segments)
