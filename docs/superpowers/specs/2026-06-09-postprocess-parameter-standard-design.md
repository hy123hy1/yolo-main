# 后处理参数标准设计

## 背景

当前项目已经具备“模型驱动类别推荐”和“任务级算法配置”能力，但随着后续接入人员闯入、聚集、离岗、计数、倒地、车辆拥堵、滞留、烟雾、安全帽等多种业务算法，单纯依赖 `labels`、`need_overlay` 这类通用字段已经不够。

如果后处理参数没有统一标准，后面会出现这些问题：
- 每个算法都要单独定义一套参数名
- 前端配置项越来越乱，用户不知道哪些参数属于哪个场景
- 后端无法按统一方式分发后处理逻辑
- 同一任务内新增第二个算法时，参数难以复用

因此需要建立一套统一的“后处理参数标准”，把能力边界拆清楚：
- 模型层负责推荐场景和类别
- 任务层负责保存具体的后处理参数
- 运行层负责按场景执行对应后处理器

## 设计目标

1. 所有算法后处理都能用统一字段表达。
2. 每个任务可单独覆盖默认参数。
3. 同一模型推荐的场景能复用到多个任务。
4. 前端只展示当前场景需要的参数，减少用户认知负担。
5. 后端可以通过 `scene_key + rule_type + postprocess_config` 直接路由到对应处理器。

## 总体方案

采用三层结构：

### 1. 场景模板层

放在代码或系统配置中，描述“这种算法天生支持什么参数”。

职责：
- 定义场景键值
- 定义默认参数
- 定义是否需要区域
- 定义默认是否画框、是否保存抓拍、是否保存视频

示例：
- `person_intrusion`
- `person_gathering`
- `person_absence`
- `person_count`
- `person_fall`
- `vehicle_count`
- `vehicle_congestion`
- `vehicle_loitering`
- `mining_truck_detect`
- `helmet_detection`
- `sleep_on_duty`
- `smoke_alarm`

### 2. 任务实例层

放在数据库和任务 JSON 中，保存某个任务真实使用的参数。

职责：
- 保存用户最终确认的参数
- 支持任务级覆盖
- 支持同一任务下多个算法各自不同参数

建议字段：
- `scene_key`
- `scene_name`
- `rule_type`
- `detect_all_classes`
- `label_source`
- `requires_region`
- `postprocess_config`

### 3. 运行执行层

后端分析服务按场景分发到对应后处理器。

职责：
- 读取任务算法配置
- 选择后处理器
- 根据参数判断是否触发事件
- 输出事件、抓拍图、报警视频

## 参数标准

### 通用字段

所有算法统一具备：
- `scene_key`
- `scene_name`
- `rule_type`
- `labels`
- `detect_all_classes`
- `label_source`
- `need_overlay`
- `need_snapshot`
- `need_video_clip`
- `requires_region`
- `postprocess_config`

### `postprocess_config`

这是所有后处理参数的统一承载字段，建议 JSON 结构保存。

示例：
```json
{
  "hold_seconds": 10,
  "cooldown_seconds": 30,
  "min_count": 3,
  "dwell_seconds": 60,
  "empty_seconds": 30
}
```

原则：
- 通用字段不要重复散落在多个字段里
- 场景特殊参数全部放进 `postprocess_config`
- 只保留少量高频字段做顶层字段，方便前端直接展示

## 场景参数建议

### 人员闯入

建议参数：
- `hold_seconds`
- `cooldown_seconds`
- `enter_min_seconds`

默认：
- 需要区域
- 需要画框
- 保存抓拍图
- 保存报警视频

### 人员聚集

建议参数：
- `min_count`
- `hold_seconds`
- `cooldown_seconds`

默认：
- 需要区域
- 需要画框

### 人员离岗

建议参数：
- `empty_seconds`
- `cooldown_seconds`

默认：
- 需要区域
- 一般不依赖复杂画框

### 人员计数

建议参数：
- `count_window_seconds`
- `deduplicate_by_track`

默认：
- 可全屏
- 不一定需要画框

### 人员倒地

建议参数：
- `hold_seconds`
- `cooldown_seconds`

默认：
- 需要抓拍
- 保存报警视频

### 车辆计数

建议参数：
- `count_window_seconds`
- `deduplicate_by_track`

### 车辆拥堵识别

建议参数：
- `min_count`
- `avg_speed_threshold`
- `hold_seconds`

### 车辆滞留识别

建议参数：
- `dwell_seconds`
- `cooldown_seconds`

### 矿车识别

建议参数：
- `cooldown_seconds`

### 安全帽检测

建议参数：
- `require_person_overlap`
- `cooldown_seconds`

### 睡岗检测

建议参数：
- `empty_seconds`
- `cooldown_seconds`

### 烟雾报警

建议参数：
- `hold_seconds`
- `confidence_boost_frames`
- `cooldown_seconds`

## 前端交互

算法配置弹窗中，前端只展示当前场景需要的参数。

展示策略：
- 场景模板里定义了什么参数，前端就展示什么
- 通用字段固定展示
- `postprocess_config` 中的参数按场景折叠展示

推荐交互：
- 选模型后自动带出推荐场景
- 选场景后自动带出默认参数
- 用户可覆盖默认值
- 保存时把参数写入 `postprocess_config`

## 后端处理

后端分析服务处理流程建议为：

1. 读取任务与算法配置
2. 根据 `scene_key` 找到对应后处理器
3. 合并场景默认参数与任务实例参数
4. 进入检测、跟踪、过滤、判定流程
5. 生成事件、抓拍图、报警视频

后端判断原则：
- `detect_all_classes=true` 时，不做类别过滤
- `detect_all_classes=false` 且 `labels` 非空时，只处理指定类别
- `requires_region=true` 时，必须校验区域
- `requires_region=false` 时，可允许全屏检测

## 数据存储

建议：
- 模型推荐信息继续保存在模型元数据中
- 任务算法参数保存在任务 JSON 和数据库中
- `postprocess_config` 在数据库中使用 JSON 字段保存

这样做的好处：
- 默认值可演进
- 单任务可覆盖
- 未来新算法只需新增场景模板，不用重构表结构

## 兼容策略

已有任务如果没有这些新字段：
- `labels` 非空，视为 `detect_all_classes=false`
- `labels` 为空，视为 `detect_all_classes=true`
- 没有 `scene_key` 时，按旧逻辑走 `detection`
- 没有 `postprocess_config` 时，走场景默认参数

## 最终效果

用户配置任务时会变成：
1. 选模型
2. 系统自动给出推荐场景和推荐类别
3. 用户只需要补少量参数
4. 保存后，后端按场景模板执行后处理
5. 以后新增一种算法，只需要补一个场景模板和处理器

## 结论

这套标准的核心是：
- 场景模板负责“能做什么”
- 任务实例负责“这次怎么做”
- 后端处理器负责“怎么执行”

这样后续扩展人员、车辆、烟雾、倒地、离岗、计数等能力时，不会把配置越做越散。
