# data/ 数据资产契约

## raw/ 原始输入（只读，由 common/scenario.py 解析）

| 资产 | 说明 | 对应建模 |
|------|------|----------|
| 建筑轮廓 + 高度 | 长方体化前的原始建筑数据 | Lin2021 §2.2.1（最小外包 cuboid） |
| DEM / 地形 | 三维地形（与 task_allocation 共用场景时口径一致） | 场景高程 |
| 人口密度栅格（昼/夜） | 地面第三方暴露输入 | README §4.3.2 时变风险场 |
| 静态永久禁飞区 | 固定管制空域 | README §4.3.1 |
| 动态临时禁飞区时间线 | 滚动窗口下发的临时禁飞（实验脚本生成） | README §4.3.1 |

## processed/ 离线产物（由 offline/pipeline.py 生成，禁止手改）

- 扩展控制网络：虚拟节点、虚拟链路、预计算飞行时间、昼/夜链路 TPR；
- 每份产物**内嵌参数指纹**（OfflineParams + UAVParams + RiskParams 的 hash），
  加载时指纹不匹配即报错（见 methods/control_network_planning/offline/network_io.py）。
