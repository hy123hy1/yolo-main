# 聚集类规则设计

## 目标

为任务算法中的聚集类规则补齐真正的事件处理能力，首批覆盖：

- 人员聚集 `rule_type=gathering`
- 车辆拥堵识别 `rule_type=congestion`

规则目标统一为：先按算法区域过滤，再统计区域内目标数量，只有当数量连续达到阈值一段时间后才生成报警事件。

## 现状

当前项目已经在模型推荐映射中声明了 `gathering` 和 `congestion`，但分析服务里尚未把它们当成独立规则处理。它们最终仍会退回普通 detection 式输出，缺少：

- 独立数量阈值
- 连续持续时间判断
- 针对业务的事件文案
- 面向前端的参数输入入口

## 方案对比

### 方案 A：阈值写入每个任务算法的 `postprocess_config`（采用）

- 每个任务算法单独配置 `count_threshold`、`hold_seconds`
- 分析服务直接读取该算法自己的 `postprocess_config`
- 前端只在聚集类规则下展示对应字段

优点：

- 和现有 `absence_seconds` 的承载方式一致
- 不需要新增表结构
- 一个模型在不同任务里可以配置不同阈值

缺点：

- 需要前端算法编辑弹窗按规则类型做条件展示

### 方案 B：写入全局配置

- 所有人员聚集共用一个阈值，所有车辆拥堵共用一个阈值

缺点：

- 不同场景无法独立调整
- 与当前任务级算法配置思路不一致

## 最终设计

### 1. 配置落点

聚集类阈值写入任务算法自己的 `postprocess_config`：

- `count_threshold`
- `hold_seconds`

默认值：

- `gathering`：`count_threshold=3`
- `congestion`：`count_threshold=5`
- 两者默认 `hold_seconds=2`

### 2. 事件触发逻辑

分析服务按如下规则处理：

1. 先完成模型检测
2. 再完成算法级类别过滤和区域过滤
3. 按 `zone_id` 分组统计当前区域内目标数量
4. 当区域数量首次达到阈值时记录 `active_since`
5. 只有连续满足 `hold_seconds` 后才生成事件
6. 事件发出后标记 `emitted=true`
7. 当数量跌破阈值时，清空该区域状态，允许下次重新触发

无区域时，按全局 `global` 作为统计范围。

### 3. 事件内容

事件仍写入现有事件表，不新增事件类型枚举，沿用各自规则类型：

- `gathering`
- `congestion`

检测结果中补充：

- `zone_id`
- `zone_name`
- `object_count`
- `annotation_label`
- `label_anchor`

文案示例：

- `人员聚集 4`
- `车辆拥堵识别 6`

### 4. 图像表现

聚集类事件图沿用现有标注链路：

- 保留检测框
- 保留红色标签
- 若命中区域，则同时绘制该区域框

不改报警视频逻辑，继续保留原始视频。

### 5. 前端配置

在算法新增/编辑弹窗中：

- 当 `rule_type` 为 `gathering` 或 `congestion` 时，显示：
  - 数量阈值
  - 持续秒数
- 其他规则类型不显示这两个字段

字段值直接写入 `draft.postprocess_config`，不新增新的顶层字段。

## 影响范围

- `backend/src/services/analysis_service.py`
- `backend/tests/test_analysis_service.py`
- `frontend/src/components/channels/AlgorithmEditorModal.vue`

## 验证点

1. `gathering` 在区域内人数连续达到阈值后报警
2. `congestion` 在区域内车辆数连续达到阈值后报警
3. 数量不足时不报警
4. 数量跌破阈值后状态重置
5. 前端保存后，`postprocess_config` 中能正确带出阈值参数
