# 模型驱动的任务类别自动推荐设计

## 背景

当前任务新增算法时，类别配置存在两个明显问题：

1. 对 `yolov8n / yolov11n` 这类通用多类别模型，若用户不主动选择类别，就会等价于开启大量类别，带来较多误报与事件噪音。
2. 对只有 `2-5` 个类别的专用模型，再要求用户手动选择类别会增加不必要的操作负担。

用户提出的新方向是：

- 在模型管理层，根据模型名称自动匹配推荐类别
- 对通用多类别模型，新增任务算法时自动带出推荐类别
- 对小类别专用模型，默认全选即可

这比“在任务弹窗里每次重新猜类别”更稳定，因为推荐结果会沉淀在模型元数据中，后续所有任务都可复用。

## 目标

1. 让模型本身具备“推荐检测类别”的能力
2. 降低用户在任务新增算法时的类别选择成本
3. 避免通用模型默认全类别打开
4. 保持专用小模型的配置足够简单
5. 让推荐是默认值，而不是强制锁定，仍保留人工修改能力

## 核心结论

采用“**模型驱动推荐**”方案：

- 如果模型类别数 `<= 5`，默认全选
- 如果模型类别数 `> 5`，根据模型名称自动推断推荐类别
- 推荐结果写入模型元数据
- 新增任务算法选择模型时，自动带出模型的推荐类别

## 规则设计

### 一、模型分类规则

#### 1. 小类别专用模型

判定：

- `classes.length <= 5`

策略：

- `auto_label_strategy = "all"`
- `recommended_labels = classes`
- 任务新增算法时，默认全选

适用场景：

- 安全帽检测模型
- 烟火检测模型
- 只有几类目标的专用行业模型

#### 2. 多类别通用模型

判定：

- `classes.length > 5`

策略：

- `auto_label_strategy = "recommended"`
- 根据模型名称推断 `recommended_labels`
- 任务新增算法时，自动带出推荐类别，而不是全选

适用场景：

- `yolov8n.pt`
- `yolov8s.pt`
- `yolov11n.pt`
- 其他训练自 COCO 或类似大类集的通用模型

## 模型名称到类别的匹配规则

### 一、关键词映射

先提供一版内置映射表：

- `人员检测` / `人` / `person` -> `["person"]`
- `货车检测` / `货车` / `truck` -> `["truck"]`
- `车辆检测` / `车辆` / `vehicle` -> `["car", "bus", "truck", "motorcycle", "bicycle"]`
- `摩托车检测` / `motorcycle` -> `["motorcycle"]`
- `自行车检测` / `bicycle` -> `["bicycle"]`
- `安全帽检测` / `helmet` -> `["helmet"]`

### 二、过滤原则

推荐结果必须经过模型真实类别过滤：

- 模型名称匹配出候选类别
- 只保留 `model.classes` 中真实存在的类别

例如：

- 模型名：`人员检测`
- 模型类别：`["person", "car", "truck", ...]`
- 推荐结果：`["person"]`

如果：

- 模型名：`人员检测`
- 模型类别里没有 `person`

则：

- 不生成 `person`
- 回退到空推荐

### 三、推荐失败回退规则

对多类别模型：

- 如果根据模型名称没有匹配到有效类别
- 则不自动填充
- 任务新增算法时提示用户手动确认

这样比“瞎推荐”更稳。

## 模型元数据扩展

当前模型元数据已包含：

- `model_id`
- `name`
- `classes`
- `weights_path`

建议新增：

- `recommended_labels: string[]`
- `auto_label_strategy: "all" | "recommended" | "manual"`
- `label_recommendation_source: "model_name" | "small_class_auto" | "none"`

说明：

- `recommended_labels`
  - 用于任务新增算法时直接带出
- `auto_label_strategy`
  - 表示该模型应如何处理默认类别
- `label_recommendation_source`
  - 便于后续排障、管理页展示、人工理解

### 示例 1：通用模型

```json
{
  "model_id": "model_xxx",
  "name": "人员检测",
  "classes": ["person", "bicycle", "car", "motorcycle", "bus", "truck"],
  "recommended_labels": ["person"],
  "auto_label_strategy": "recommended",
  "label_recommendation_source": "model_name"
}
```

### 示例 2：专用小模型

```json
{
  "model_id": "model_helmet",
  "name": "安全帽检测",
  "classes": ["helmet", "person"],
  "recommended_labels": ["helmet", "person"],
  "auto_label_strategy": "all",
  "label_recommendation_source": "small_class_auto"
}
```

### 示例 3：无法推断

```json
{
  "model_id": "model_unknown",
  "name": "场景一",
  "classes": ["person", "car", "truck", "bus", "bicycle", "motorcycle"],
  "recommended_labels": [],
  "auto_label_strategy": "manual",
  "label_recommendation_source": "none"
}
```

## 模型管理页面设计

基于 [Models.vue](G:\docker\yolo-main\frontend\src\pages\Models.vue)。

### 一、模型列表增加展示

建议在模型列表或详情页增加：

- `类别数`
- `默认类别策略`
- `推荐类别`

展示示例：

- `默认类别策略：自动推荐`
- `推荐类别：person`

或：

- `默认类别策略：全选`
- `推荐类别：helmet, person`

### 二、触发时机

推荐类别的计算应发生在：

1. 上传模型后
2. 编辑模型名称后
3. 如果后续手动编辑 `classes`，也应重新计算

### 三、是否允许人工覆盖

建议第一阶段先不做复杂的人工覆盖面板，只做自动计算。

后续若需要，可在模型详情中增加：

- 手动编辑 `recommended_labels`
- 手动改 `auto_label_strategy`

第一阶段保持简单即可。

## 任务新增算法时的行为

基于 [AlgorithmEditorModal.vue](G:\docker\yolo-main\frontend\src\components\channels\AlgorithmEditorModal.vue)。

### 一、选中模型后的自动带出规则

当用户在新增算法时选择模型：

#### 情况 A：`auto_label_strategy = "all"`

- 自动将模型 `recommended_labels` 全部带入算法 `labels`
- UI 提示：`该模型类别较少，已默认全选`

#### 情况 B：`auto_label_strategy = "recommended"`

- 自动将 `recommended_labels` 带入算法 `labels`
- UI 提示：`已根据模型名称自动推荐类别`

#### 情况 C：`auto_label_strategy = "manual"`

- 不自动带任何类别
- UI 提示：`未识别到推荐类别，请手动选择`

### 二、是否允许用户修改

必须允许修改。

原因：

- 同一模型可能被不同任务复用
- 同一个“车辆检测”模型，某些任务只想看 `truck`
- 推荐只是默认值，不应锁死

因此最终策略是：

- 自动带出
- 允许覆盖

## 提交语义

任务算法提交时仍保留当前 `labels` 字段作为最终生效值。

也就是说：

- 模型管理负责提供默认推荐
- 任务算法负责保存最终选择

这样职责最清楚：

- 模型层 = 推荐源
- 任务层 = 实际配置

## 后端处理流程

### 一、模型上传/编辑时

后端在模型服务中：

1. 读取 `model.name`
2. 读取 `model.classes`
3. 根据类别数量决定策略
4. 若为多类别模型，按名称匹配推荐类别
5. 写回 `model.json`

### 二、任务新增算法时

后端不需要再重新猜模型名。

只需：

- 前端获取模型列表时拿到 `recommended_labels` 和 `auto_label_strategy`
- 前端选择模型后自动带出

后端仍然只接收最终的算法配置结果。

## 前端交互文案建议

### 通用模型命中推荐

- `已根据模型名称自动推荐类别：person`

### 小类别模型

- `该模型类别较少，已默认全选`

### 无推荐结果

- `未识别到推荐类别，请手动选择`

## 风险与边界

### 1. 模型名称不规范

例如：

- `模型1`
- `大门口算法`

这类名称可能无法推断推荐类别。

解决：

- 回退为 `manual`
- 不做错误推荐

### 2. 模型名称与类别不一致

例如：

- 名称叫“人员检测”
- 但模型类别里没有 `person`

解决：

- 只保留真实存在于 `classes` 的类别
- 过滤后为空则回退 `manual`

### 3. 专用模型类别虽然少，但不一定全部都想启用

例如 `["person", "helmet"]` 模型，有时用户只关心 `helmet`

解决：

- 默认全选
- 但仍允许用户手动修改

## 实施范围

第一阶段：

1. 模型元数据增加推荐字段
2. 模型上传/编辑后自动生成推荐字段
3. 模型管理页面展示推荐结果
4. 任务新增算法选中模型后自动带出默认类别

第二阶段：

1. 支持模型管理页人工调整推荐类别
2. 支持更丰富的行业词典
3. 支持中英文同义词扩展

## 最终效果

最终用户体验会变成：

1. 在模型管理中把模型命名为“人员检测”
2. 系统自动识别该模型推荐类别为 `person`
3. 新建任务 -> 新增算法 -> 选择该模型
4. 类别自动带出 `person`
5. 用户可直接保存，也可再自行修改

对于只有 `2-5` 类的专用模型：

1. 选中模型
2. 系统默认全选该模型所有类别
3. 用户通常无需额外干预

这样既满足你对通用模型的“自动带出”，也满足专用模型的“默认全选”。

## scene_candidates 精确结构

为了让模型层和任务层解耦，`scene_candidates` 需要是稳定结构，而不是临时拼接字段。

建议 JSON Schema 语义如下：

```json
{
  "scene_key": "person_intrusion",
  "scene_name": "人员闯入",
  "recommended_labels": ["person"],
  "rule_type": "intrusion",
  "default_need_overlay": true,
  "default_need_snapshot": true,
  "default_need_video_clip": true,
  "requires_region": true,
  "priority": 100,
  "source": "model_name_mapping"
}
```

字段说明：

- `scene_key`
  - 场景唯一标识
- `scene_name`
  - 中文展示名
- `recommended_labels`
  - 推荐类别
- `rule_type`
  - 后处理规则分发类型
- `default_need_overlay`
  - 默认是否画框
- `default_need_snapshot`
  - 默认是否保存抓拍
- `default_need_video_clip`
  - 默认是否保存报警视频
- `requires_region`
  - 该场景是否必须依赖区域
- `priority`
  - 推荐排序，越大越靠前
- `source`
  - 推荐来源，第一阶段固定可用 `model_name_mapping` / `small_class_auto`

### scene_key 枚举建议

第一阶段先固定这些：

- `person_intrusion`
- `person_gathering`
- `person_absence`
- `person_count`
- `person_fall`
- `vehicle_count`
- `vehicle_congestion`
- `vehicle_loitering`
- `mining_truck_detect`
- `sleep_on_duty`
- `helmet_detection`
- `smoke_alarm`

### rule_type 枚举建议

第一阶段统一使用：

- `detection`
- `intrusion`
- `gathering`
- `absence`
- `count`
- `loitering`
- `congestion`
- `fall_like`

后续规则引擎按这个字段分发即可。

## task_algorithms 扩展字段清单

当前任务算法对象已有：

- `model_id`
- `labels`
- `confidence`
- `need_snapshot`
- `need_video_clip`
- `need_overlay`
- `region_ids`

建议新增以下字段：

- `scene_key`
- `scene_name`
- `rule_type`
- `detect_all_classes`
- `label_source`
- `requires_region`
- `postprocess_config`

### 字段含义

#### 1. `scene_key`

例如：

- `person_intrusion`
- `vehicle_count`

用于标记这是哪一种业务场景。

#### 2. `scene_name`

例如：

- `人员闯入`
- `车辆计数`

用于前端直出显示，不必每次再从字典查。

#### 3. `rule_type`

例如：

- `intrusion`
- `count`
- `loitering`

后处理规则引擎只看这个字段即可。

#### 4. `detect_all_classes`

布尔值：

- `true`
  - 不做类别过滤
- `false`
  - 按 `labels` 过滤

这个字段用于消除当前“`labels=[]` 既可能表示空，也可能表示全部类别”的语义混乱。

#### 5. `label_source`

推荐枚举：

- `scene_recommended`
- `model_default_all`
- `manual`

含义：

- `scene_recommended`
  - 来自模型场景推荐
- `model_default_all`
  - 来自小类别模型默认全选
- `manual`
  - 用户手工修改过

#### 6. `requires_region`

布尔值：

- `true`
  - 该场景强依赖区域
- `false`
  - 可全画面工作

后续前端可以根据这个字段决定是否重点提示区域配置。

#### 7. `postprocess_config`

JSON 对象，第一阶段先允许为空结构。

建议保留基础字段：

```json
{
  "min_duration_seconds": null,
  "max_duration_seconds": null,
  "min_count": null,
  "max_count": null,
  "threshold": null,
  "cooldown_seconds": null
}
```

这会为后续：

- 聚集人数阈值
- 滞留时间
- 离岗时间
- 拥堵阈值

预留统一入口。

## 数据库表变更建议

当前 `task_algorithms` 表建议补字段：

- `scene_key VARCHAR(64)`
- `scene_name VARCHAR(255)`
- `rule_type VARCHAR(64)`
- `detect_all_classes BOOLEAN`
- `label_source VARCHAR(64)`
- `requires_region BOOLEAN`
- `postprocess_config_json JSON`

如果继续沿用 SQLite/MySQL 双兼容风格：

- SQLite 可先用 `TEXT` 存 `postprocess_config_json`
- MySQL 可直接 `JSON`

元数据模型 [models.py](G:\docker\yolo-main\backend\src\db\models.py) 中对应也要扩展。

## 任务算法保存语义

### 1. 通用模型推荐场景

例如用户选择“人员检测”模型，自动命中：

- `scene_key = person_intrusion`
- `labels = ["person"]`
- `detect_all_classes = false`
- `label_source = scene_recommended`

### 2. 小类别模型默认全选

例如“安全帽检测”模型只有 `helmet/person` 两类：

- `scene_key = helmet_detection`
- `labels = ["helmet", "person"]`
- `detect_all_classes = true` 或 `false`

建议这里统一为：

- `labels = ["helmet", "person"]`
- `detect_all_classes = false`
- `label_source = model_default_all`

原因：

- 小模型的“全选”本质仍然是“把全部已知类别写清楚”
- 不需要把它语义化为“检测全部类别”
- 只有通用模型显式选择“全部类别”时才设置 `detect_all_classes = true`

### 3. 用户手工改过

如果用户在前端修改了推荐类别：

- `label_source = manual`

这样后续就能知道当前配置是不是系统推荐结果。

## 旧数据兼容策略

这是必须做的一层，不然已有任务会乱。

### 模型兼容

历史模型没有：

- `recommended_labels`
- `auto_label_strategy`
- `scene_candidates`

处理方式：

- 模型详情读取时，如果缺失这些字段，则根据 `name + classes` 动态补齐内存视图
- 保存模型时再回写

这样无需一次性强制迁移全部模型文件。

### 任务算法兼容

历史任务算法没有：

- `scene_key`
- `rule_type`
- `detect_all_classes`
- `label_source`

兼容规则：

1. 若 `labels` 非空
   - `detect_all_classes = false`
   - `label_source = manual`

2. 若 `labels` 为空
   - `detect_all_classes = true`
   - `label_source = manual`

3. `scene_key / scene_name / rule_type`
   - 可先为空
   - 前端显示为“未指定场景”

也就是说：

- 旧任务先保证行为不变
- 新建任务再逐步进入新结构

### 元数据数据库兼容

任务元数据同步逻辑需要同步补齐这些新字段。

对于已存在的 `task_algorithms` 元数据行：

- 允许为空
- 通过 JSON 源文件重新 `resync` 时自动补全

## 前端任务新增算法交互顺序

最终推荐顺序建议为：

1. 用户选模型
2. 前端读取模型元数据
3. 判断 `auto_label_strategy`

### 情况 A：`all`

- 自动填充全部类别
- 展示提示：
  - `该模型类别较少，已默认全选`

### 情况 B：`recommended`

- 展示场景推荐卡片
- 默认高亮第一张推荐卡片，但不立即写入
- 用户点击卡片后写入：
  - `scene_key`
  - `scene_name`
  - `rule_type`
  - `labels`
  - `need_overlay`
  - `need_snapshot`
  - `need_video_clip`
  - `requires_region`

### 情况 C：`manual`

- 不自动填充
- 提示用户手工选择

## 第一阶段落地范围

为了尽快把“通用模型先跑顺”，第一阶段范围建议限定为：

1. 模型上传/编辑时自动生成：
   - `recommended_labels`
   - `auto_label_strategy`
   - `scene_candidates`

2. 模型管理页展示：
   - 推荐策略
   - 推荐类别
   - 推荐场景

3. 任务新增算法弹窗：
   - 选模型后自动读取推荐信息
   - 支持小模型默认全选
   - 支持通用模型场景推荐

4. 任务算法保存结构增加：
   - `scene_key`
   - `scene_name`
   - `rule_type`
   - `detect_all_classes`
   - `label_source`
   - `requires_region`

5. 分析服务兼容：
   - 识别 `detect_all_classes`
   - 老数据行为不变

## 第二阶段

在第一阶段稳定后，再接入：

1. 后处理规则引擎
2. 每类场景的阈值配置
3. 任务详情里的场景化展示
4. 事件中心按 `scene_key` 过滤
