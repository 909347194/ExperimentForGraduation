# -*- coding: utf-8 -*-
r"""可见性图 / 稀疏路图规划器（methods/visibility_graph）测试。

重点覆盖验证期发现的两个真实 bug 的回归——它们都曾让结论完全错误，且不报错：

1. **plan() 就地污染 self.adj**：`aug = list(self.adj) + [...]` 只浅拷贝外层，
   `aug[j].append(...)` 改写原始图，使后续 OD 继承前次临时边，产生几何不可能的"捷径"
   （实测出现过 441 m 行程算出 66.93 m 的路径）。
2. **节点贴在障碍边界**：节点被放在建筑轮廓外 `node_buffer`，而障碍已外扩 `d_build`，
   于是节点恰好落在障碍边界上、出边净空≈0，机动保护区（净空<clearance）把**全部**
   建筑出边误删，路径膨胀被严重夸大。

其余为方法不变量：节点不在障碍内、边必视线可达且不穿静态禁飞、机动保护区只删边不增边、
动态禁飞按时间窗在查询期生效、A\* 与 Dijkstra 结果一致、配置未知键显式报错。

不负责：控制网络链路（另有规划）；真实广州大场景（本文件用合成小场景，保证秒级跑完）。
"""

from __future__ import annotations

import copy

import numpy as np
import pytest
from shapely.geometry import LineString, box

from path_planning.common.types import (
    Building,
    CityEnvironment,
    DynamicNoFly,
    Period,
    StaticNoFly,
)
from path_planning.methods.visibility_graph import (
    VisibilityGraphParams,
    build_edges,
    build_visibility_graph,
    shortest_path,
)

FLIGHT_Z = 30.0


# ── 合成场景 ──────────────────────────────────────────────────────────────────

def _make_env() -> CityEnvironment:
    """600×600 合成场景：2 个建筑 + 1 块静态禁飞 + 1 块横穿主走廊的动态禁飞。

    走廊 y=300 从 x=100 到 x=500 在静态层面完全通畅，只有动态区 D1 会阻断它，
    因此 idle/active 两个时刻的路径差异可干净归因于动态时间窗门。
    """
    buildings = [
        Building(bid=1, poly=box(120, 120, 180, 180), z0=0.0, z1=80.0,
                 bclass="residential", height=80.0),
        Building(bid=2, poly=box(420, 420, 480, 480), z0=0.0, z1=80.0,
                 bclass="residential", height=80.0),
    ]
    static = [
        StaticNoFly(zid="S1", poly=box(300, 450, 360, 510), z0=0.0, z1=100.0,
                    name="static-1", source="test"),
    ]
    dyn = [
        DynamicNoFly(
            zid="D1",
            periods=[Period(start=0.0, end=300.0,
                            poly=box(250, 270, 350, 330), z0=0.0, z1=100.0)],
            name="dyn-1", version=1,
        ),
    ]
    return CityEnvironment(
        buildings=buildings, static_nofly=static, dynamic_nofly=dyn,
        region=box(0, 0, 600, 600), dem=None, meta={},
    )


def _params(**kw) -> VisibilityGraphParams:
    base = dict(
        simplify_mode="simplify", simplify_tol=3.0, d_build=5.0,
        node_step=30.0, node_buffer=5.0,
        altitude_layers=[FLIGHT_Z], grid_step=100.0, grid_layers=[FLIGHT_Z],
        max_nodes_per_building=8, max_nodes=2000,
        max_edge_len=200.0, edge_margin=2.0, max_degree=40, connect_layers=False,
        protection_clearance=8.0, attach_radius=80.0,
        include_dynamic_as_static=False, cruise_speed_mps=15.0,
    )
    base.update(kw)
    return VisibilityGraphParams.from_dict(base)


@pytest.fixture(scope="module")
def built():
    """建好的图 + 场景 + 参数（模块级复用，避免每条用例重建）。"""
    env = _make_env()
    params = _params()
    graph, stats = build_visibility_graph(env, params, verbose=False)
    return graph, env, params, stats


def _nearest_node(graph, xy) -> int:
    d = np.linalg.norm(graph.nodes[:, :2] - np.asarray(xy, float), axis=1)
    return int(np.argmin(d))


def _crosses(pts: np.ndarray, poly) -> bool:
    """折线任一段是否在 2D 上与该多边形相交。"""
    for a, b in zip(pts[:-1], pts[1:]):
        if LineString([(a[0], a[1]), (b[0], b[1])]).intersects(poly):
            return True
    return False


# ── 建图基本不变量 ────────────────────────────────────────────────────────────

def test_build_graph_produces_nodes_and_edges(built):
    graph, env, params, stats = built
    assert len(graph.nodes) > 0, "应生成节点"
    n_edges = sum(len(a) for a in graph.adj)
    assert n_edges > 0, "应生成边"
    assert stats["nodes"]["n_grid"] > 0 and stats["nodes"]["n_building"] > 0, \
        "合成场景应同时有网格骨干与建筑轮廓节点"


def test_all_nodes_outside_obstacles(built):
    graph, env, params, stats = built
    bad = [i for i in range(len(graph.nodes))
           if not graph.obstacles.point_clear(graph.nodes[i])]
    assert not bad, f"有节点落在建筑障碍内：{bad[:5]}"


def test_building_nodes_have_positive_clearance(built):
    """回归 bug 2：建筑节点不得贴在障碍边界上（净空必须 > 0）。"""
    graph, env, params, stats = built
    idx = [i for i, k in enumerate(graph.meta["kinds"]) if k == "building"]
    assert idx, "合成场景应生成建筑轮廓节点"
    clears = [graph.obstacles.segment_clearance(graph.nodes[i], graph.nodes[i])
              for i in idx]
    worst = min(clears)
    # 修复前节点恰在已外扩障碍边界上 → 净空≈0；修复后应 ≈ node_buffer(5 m)。
    assert worst > 1e-6, f"建筑节点贴障碍边界（最小净空 {worst:.6f} m）"


def test_edges_respect_los_static_nofly_and_weight(built):
    """每条边都必须是视线可达、不穿静态禁飞，且权重等于三维欧氏距离。"""
    graph, env, params, stats = built
    checked = 0
    for u, adj_u in enumerate(graph.adj):
        for (v, w) in adj_u:
            if v < u:  # 无向边只查一次
                continue
            a, b = graph.nodes[u], graph.nodes[v]
            assert graph.obstacles.segment_clear(a, b, margin=0.0), f"边 {u}-{v} 穿越建筑"
            assert not graph.nofly.blocked(a, b), f"边 {u}-{v} 穿越静态禁飞区"
            assert abs(w - float(np.linalg.norm(b - a))) < 1e-6, f"边 {u}-{v} 权重≠欧氏距离"
            checked += 1
    assert checked > 0


def test_protection_only_removes_edges(built):
    """机动保护区：只能删边，不能新增边，且确实删掉了一部分。"""
    graph, env, params, stats = built

    def edge_set(adj):
        return {(min(u, v), max(u, v)) for u, a in enumerate(adj) for (v, _w) in a}

    on, _ = build_edges(graph.nodes, graph.obstacles, graph.params,
                        nofly_blocked=graph.nofly.blocked,
                        with_protection=True, verbose=False)
    off, _ = build_edges(graph.nodes, graph.obstacles, graph.params,
                         nofly_blocked=graph.nofly.blocked,
                         with_protection=False, verbose=False)
    s_on, s_off = edge_set(on), edge_set(off)
    assert s_on <= s_off, "机动保护区不应新增边"
    assert len(s_on) < len(s_off), "机动保护区应删掉一部分边"


# ── 在线规划：两个 bug 的回归 ─────────────────────────────────────────────────

def test_plan_does_not_mutate_adjacency_and_is_order_independent(built):
    """回归 bug 1：plan() 必须无副作用，且同一 OD 的结果与调用顺序无关。"""
    graph, env, params, stats = built
    A = graph.nodes[_nearest_node(graph, (100, 300))]
    B = graph.nodes[_nearest_node(graph, (500, 300))]
    C = graph.nodes[_nearest_node(graph, (100, 500))]

    snapshot = copy.deepcopy(graph.adj)
    r1 = graph.plan(A, B, smooth=False)
    assert r1.feasible, f"OD A→B 不可行：{r1.infeasible_reason}"
    c1 = r1.route.dist

    # 中间插一次别的 OD：旧 bug 下它的临时边会留在图里，污染后续查询。
    graph.plan(A, C, smooth=False)
    r2 = graph.plan(A, B, smooth=False)
    assert r2.feasible, f"第二次 OD A→B 不可行：{r2.infeasible_reason}"
    c2 = r2.route.dist

    assert abs(c1 - c2) < 1e-9, f"plan 结果顺序相关：{c1} vs {c2}"
    assert graph.adj == snapshot, "plan() 污染了 self.adj（临时边被写回原图）"


def test_plan_cost_never_below_straight_line(built):
    """代价必须 ≥ 两端直线距离（三角不等式）——旧 bug 会给出小于直线的值。"""
    graph, env, params, stats = built
    A = graph.nodes[_nearest_node(graph, (100, 300))]
    B = graph.nodes[_nearest_node(graph, (500, 300))]
    res = graph.plan(A, B, smooth=False)
    assert res.feasible, res.infeasible_reason
    cost = res.route.dist
    straight = float(np.linalg.norm(B - A))
    assert cost >= straight - 1e-6, \
        f"代价 {cost:.2f} 小于直线距离 {straight:.2f}，违反三角不等式"


# ── 动态禁飞：选项 A（查询期时间窗检查）────────────────────────────────────────

def test_dynamic_time_window_forces_detour(built):
    """同一 OD：动态区生效时必须绕开，不生效时可直穿；图本身不重建。"""
    graph, env, params, stats = built
    A = graph.nodes[_nearest_node(graph, (100, 300))]
    B = graph.nodes[_nearest_node(graph, (500, 300))]
    zone = env.dynamic_nofly[0].periods[0].poly

    res_idle = graph.plan(A, B, depart_time=500.0, smooth=False)   # 区已失效
    res_act = graph.plan(A, B, depart_time=50.0, smooth=False)       # 区生效中
    assert res_idle.feasible and res_act.feasible, (res_idle.infeasible_reason, res_act.infeasible_reason)

    pts_idle = np.array([np.asarray(p, float) for p in res_idle.path.pts])
    pts_act = np.array([np.asarray(p, float) for p in res_act.path.pts])
    c_idle = res_idle.route.dist
    c_act = res_act.route.dist

    assert _crosses(pts_idle, zone), "动态区未生效时应可直穿该区"
    assert not _crosses(pts_act, zone), "动态区生效时路径仍穿越该区（时间窗门失效）"
    assert c_act > c_idle * 1.02, f"生效时应绕行变长：active={c_act} vs idle={c_idle}"


# ── 搜索与参数 ────────────────────────────────────────────────────────────────

def test_astar_matches_dijkstra(built):
    graph, env, params, stats = built
    ia = _nearest_node(graph, (100, 300))
    ib = _nearest_node(graph, (500, 300))
    p_a, c_a = shortest_path(graph.adj, graph.nodes, ia, ib, astar=True)
    p_d, c_d = shortest_path(graph.adj, graph.nodes, ia, ib, astar=False)
    assert p_a is not None and p_d is not None
    assert abs(c_a - c_d) < 1e-6, f"A* 与 Dijkstra 代价不一致：{c_a} vs {c_d}"


def test_params_unknown_key_raises_instead_of_silent_fallback():
    """配置键拼错/层级写错必须报错，不能静默回退默认值。"""
    with pytest.raises(ValueError):
        VisibilityGraphParams.from_dict({"protection_clearance_": 8.0})
    p = VisibilityGraphParams.from_dict({"protection_clearance": 3.0})
    assert p.protection_clearance == 3.0
    assert p.d_build == 5.0, "未配置的项应走默认值"
