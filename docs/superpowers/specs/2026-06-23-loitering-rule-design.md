# Loitering 规则设计

## 目标

为 `rule_type=loitering` 补齐真正的后处理规则，使“车辆滞留识别”不再退回普通 detection，而是按区域内目标持续停留时长来触发报警。

## 规则口径

本次采用最简且稳定的业务口径：

- 先按算法区域过滤
- 只要区域内存在命中的车类目标
- 且这种“区域被占用”的状态连续持续超过阈值秒数
- 就触发一条 `loitering` 事件

不引入轨迹跟踪，不要求识别同一辆具体车，只按区域占用持续时长判断。

## 配置落点

继续沿用任务算法自己的 `postprocess_config`：

- `linger_seconds`

默认值：

- `linger_seconds=10`

## 事件输出

事件类型继续使用：

- `loitering`

事件检测对象中补充：

- `zone_id`
- `zone_name`
- `loiter_seconds`
- `annotation_label`
- `label_anchor`
- `snapshot_detections`
- `snapshot_regions`

标签文案示例：

- `车辆滞留识别 12s`

## 状态机

每个算法、每个区域单独维护状态：

- `active_since`
- `emitted`

行为：

1. 区域内没有命中目标：清空状态
2. 首次出现命中目标：记录 `active_since`
3. 连续持续不足阈值：不报警
4. 连续持续达到阈值：报警，并将 `emitted=true`
5. 若后续区域清空：重置状态，允许下一次再次触发

无区域时按 `global` 处理。

## 前端配置

在算法新增/编辑弹窗中：

- 当 `rule_type=loitering` 时显示 `滞留秒数`
- 保存到 `draft.postprocess_config.linger_seconds`

## 影响范围

- `backend/src/services/analysis_service.py`
- `backend/tests/test_analysis_service.py`
- `frontend/src/components/channels/AlgorithmEditorModal.vue`

## 验证点

1. 区域内有车但持续时间不足时不报警
2. 达到阈值时生成 `loitering` 事件
3. 区域清空后状态会重置
4. 前端可配置 `linger_seconds`
