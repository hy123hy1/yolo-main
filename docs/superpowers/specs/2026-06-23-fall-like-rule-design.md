# Fall Like 规则设计

## 目标

为 `rule_type=fall_like` 增加一层轻量级后处理规则，用于在不更换模型的前提下，快速实现“人员倒地”能力。

本次采用快速版方案 2：

- 先检测到 `person`
- 再根据人体框形态判断是否“疑似倒地”
- 连续保持一段时间后才报警

不引入姿态关键点模型，不做复杂轨迹跟踪。

## 已确认业务口径

- 配了区域：只对区域内的人做倒地判断
- 没配区域：按全屏判断

## 方案对比

### 方案 A：最简宽高判断

- `person` 检测命中
- 仅判断 `width > height`
- 持续若干秒后报警

优点：

- 实现最快

缺点：

- 误报偏多
- 弯腰、蹲下、坐地容易误判

### 方案 B：增强快速版（采用）

- `person` 检测命中
- 判断 `width / height >= fall_ratio_threshold`
- 过滤过小目标框
- 连续保持 `hold_seconds` 后报警

优点：

- 改动小
- 规则简单清晰
- 比“宽大于高”更稳

缺点：

- 仍属于启发式规则
- 无法完全替代 pose / 专用倒地模型

### 方案 C：姿态关键点版

- `person` + pose/keypoints
- 根据头、肩、髋、膝等关键点关系判断倒地

优点：

- 准确率更高

缺点：

- 模型和推理链路都要扩展
- 当前阶段实现成本偏高

## 最终设计

### 1. 输入前提

`fall_like` 只基于经过现有链路过滤后的 `person` 检测框判断：

1. 模型先完成检测
2. 先过 `confidence`
3. 再过标签过滤
4. 再过区域过滤
5. 最终只对剩余的 `person` 框做倒地规则判断

这意味着：

- 配置了区域时，只会看区域内的人
- 没有配置区域时，会看全屏的人

### 2. 判定规则

对每个 `person` 检测框，计算：

- `box_width = x2 - x1`
- `box_height = y2 - y1`
- `ratio = box_width / box_height`

当同时满足以下条件时，判为“疑似倒地”：

- `box_height >= min_box_height`
- `ratio >= fall_ratio_threshold`

### 3. 时间阈值

疑似倒地不能一帧即报警，需要持续一段时间：

- 首次命中疑似倒地：记录 `active_since`
- 连续命中但未达到阈值：继续等待
- 连续达到 `hold_seconds`：触发 `fall_like` 事件
- 中途只要没有疑似倒地目标：重置状态

### 4. 状态粒度

本次不做目标级跟踪，不要求精确识别“同一个人”。

状态按区域粒度维护：

- 配了区域：按 `algorithm_id + zone_id`
- 没配区域：按 `algorithm_id + global`

也就是说，本次规则表达的是：

“该区域内持续存在疑似倒地的人”

而不是：

“某一个具体人的轨迹持续倒地”

这样更符合当前系统的复杂度和实现成本。

### 5. 默认参数

参数写入任务算法自己的 `postprocess_config`：

- `fall_ratio_threshold`
- `hold_seconds`
- `min_box_height`

默认值：

- `fall_ratio_threshold = 1.2`
- `hold_seconds = 2`
- `min_box_height = 40`

含义：

- 只有当人体框明显偏横向时才算可疑
- 连续 2 秒才报警
- 太远、太小的人框先不参与判断

### 6. 事件输出

事件类型继续沿用：

- `fall_like`

事件对象中补充：

- `fall_ratio`
- `fall_seconds`
- `zone_id`
- `zone_name`
- `annotation_label`
- `label_anchor`
- `snapshot_detections`
- `snapshot_regions`

标签文案示例：

- `人员倒地 2s`

### 7. 事件图表现

保留触发的人框。

若配置了区域，则事件图同时绘制该区域框。

不改报警视频逻辑，仍沿用当前“原始视频，不叠框”的策略。

### 8. 前端配置

在算法新增/编辑弹窗中：

- 当 `rule_type=fall_like` 时显示：
  - `倒地宽高比`
  - `持续秒数`
  - `最小框高`

字段写入：

- `postprocess_config.fall_ratio_threshold`
- `postprocess_config.hold_seconds`
- `postprocess_config.min_box_height`

## 影响范围

- `backend/src/services/analysis_service.py`
- `backend/tests/test_analysis_service.py`
- `frontend/src/components/channels/AlgorithmEditorModal.vue`

## 验证点

1. `person` 框宽高比不足时不报警
2. `person` 框高度过小时不报警
3. 满足宽高比且持续达到阈值后报警
4. 区域清空或不再满足条件后状态重置
5. 前端参数可正确写入 `postprocess_config`
