# -*- coding: utf-8 -*-
"""冲突层：弦化、CPA 几何、椭球度量、三级证书、检测器、连通域聚类。

职责：判断"这批带时间的航迹里谁与谁在何时何地过近"，并给出可审计的豁免依据。
边界：不重规划（``coordination``）、不写 Registry、不改轨迹。
相关：``docs/conflict_model.md``（模型与定理）、``docs/architecture.md``（数据流）。

文件分工：

* ``chord_sampling.py``       Hermite → 匀速弦（递归细分 + 实测偏差）
* ``cpa_geometry.py``         匀速直线对闭式 CPA、时间窗交集
* ``ellipsoid_metric.py``     椭球 → T 空间球（含包围盒余量与判定半径）
* ``pair_certificates.py``    三级证书 + 合并 CPA（阈值严格互补）
* ``conflict_detector.py``    全对检测、弦化缓存、证书统计
* ``conflict_clusters.py``    并查集连通域（决定单点改还是多点同时改）
"""

from path_planning.methods.conflict.chord_sampling import (
    MotionChord,
    chordify,
    chordify_many,
    max_initial_chord_s,
)
from path_planning.methods.conflict.conflict_clusters import (
    ConflictCluster,
    cluster_conflicts,
)
from path_planning.methods.conflict.conflict_detector import (
    ConflictPair,
    DetectionConfig,
    DetectionReport,
    detect_conflicts,
    detect_pair,
)
from path_planning.methods.conflict.cpa_geometry import (
    ClosestApproach,
    chord_pair_closest,
    chord_pair_distance,
    closest_approach_linear,
    overlap_window,
)
from path_planning.methods.conflict.ellipsoid_metric import (
    Aabb,
    EllipsoidMetric,
    aabb_distance,
    aabb_of_chord,
)
from path_planning.methods.conflict.pair_certificates import (
    CertificateKind,
    PairVerdict,
    certify_pair,
    certify_pair_enu,
    unified_eps,
)

__all__ = [
    "MotionChord",
    "chordify",
    "chordify_many",
    "max_initial_chord_s",
    "ConflictCluster",
    "cluster_conflicts",
    "ConflictPair",
    "DetectionConfig",
    "DetectionReport",
    "detect_conflicts",
    "detect_pair",
    "ClosestApproach",
    "chord_pair_closest",
    "chord_pair_distance",
    "closest_approach_linear",
    "overlap_window",
    "Aabb",
    "EllipsoidMetric",
    "aabb_distance",
    "aabb_of_chord",
    "CertificateKind",
    "PairVerdict",
    "certify_pair",
    "certify_pair_enu",
    "unified_eps",
]
