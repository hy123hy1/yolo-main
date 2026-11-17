# 电子封条模型表独立设计

## 目标

将当前 `models` 表中的电子封条专用模型 `903eb72b` 迁移到独立表
`electronic_seal_models`，使视频 AI 与电子封条拥有独立的模型元数据、查询接口和
引用约束，同时继续复用现有模型权重文件，避免复制大文件或改变推理路径。

## 已确认现状

- 目标模型为 `id=903eb72b`、`name=电子封条`。
- `task_algorithms` 中没有对该模型的引用。
- `electronic_seal_configs` 中有 4 条配置引用该模型。
- `backend` 与 `electronic-seal-worker` 当前均连接
  `192.168.1.200:3307/sp-ai-yolo`。
- 模型权重位于 `/app/models/registry/903eb72b/weights/yolo11s.pt`。
- 当前电子封条页面复用视频 AI 的 `GET /models`，Worker 默认模型身份来自
  registry 下的 `model.json` 文件。

## 数据模型

新增 SQLAlchemy 模型 `ElectronicSealModelMetaRecord`，表名为
`electronic_seal_models`。字段、类型、可空性和索引与 `models` 完全一致：

- `id`
- `name`
- `version`
- `source`
- `description`
- `tags_json`
- `classes_json`
- `weights_path`
- `file_size`
- `file_size_mb`
- `job_id`
- `confidence`
- `alert_interval`
- `created_at`
- `updated_at`
- `raw_json`

`electronic_seal_configs.model_id` 保持原字段不变，并增加外键：

```text
electronic_seal_configs.model_id
    -> electronic_seal_models.id ON DELETE RESTRICT
```

模型元数据表独立，但模型文件继续保存在共享 registry 目录中。目录已经按
`model_id` 隔离，因此不需要复制权重，也不会使视频 AI 运行时自动使用该模型。

## 数据迁移

迁移脚本必须针对 MySQL，并按以下顺序执行：

1. 使用 `CREATE TABLE electronic_seal_models LIKE models` 创建同结构表。
2. 前置校验 `models.id='903eb72b'` 恰好存在一条记录。
3. 前置校验 `task_algorithms.model_id='903eb72b'` 的引用数为 0。
4. 将目标记录完整写入 `electronic_seal_models`，重复执行时更新同 ID 记录。
5. 校验所有非空 `electronic_seal_configs.model_id` 均可在新表找到。
6. 为 `electronic_seal_configs.model_id` 增加指向新表的限制删除外键。
7. 确认新表记录存在后，从 `models` 删除 `903eb72b`。

MySQL DDL 会隐式提交，因此迁移不能假设整个过程由一个事务回滚。脚本必须幂等，
每一步都检查表、数据和约束是否已存在。任何前置校验失败时停止迁移，不删除原记录。

## 后端边界

电子封条模块新增模型仓储方法：

- `list_models()`：按创建时间倒序返回电子封条模型。
- `get_model(model_id)`：返回单个电子封条模型。
- `find_model_by_name(name)`：供默认配置初始化使用。
- `model_exists(model_id)`：供配置保存校验使用。

这些方法只操作 `electronic_seal_models`，不调用视频 AI 的 `ModelService`。

Worker 初始化默认配置时，从 `electronic_seal_models` 按名称查找唯一的
`电子封条` 模型。推理适配器通过电子封条模型仓储读取元数据，并继续使用
`weights_path` 加载共享 registry 中的文件。以下情况应明确失败并记录日志：

- 数据库不存在该模型；
- 同名模型不唯一；
- `weights_path` 为空；
- 权重文件不存在；
- 配置引用了新表中不存在的 `model_id`。

健康类算法不依赖 YOLO 模型，允许 `model_id` 为空。

## API

电子封条 Worker 增加只读接口：

```text
GET /electronic-seal/models
GET /electronic-seal/models/{model_id}
```

列表接口返回与当前前端兼容的 `{ "models": [...] }` 结构。模型字段使用现有命名，
例如数据库 `id` 对外仍返回为 `model_id`，`classes_json` 对外返回为 `classes`。

更新电子封条算法配置时，若请求包含非空 `model_id`，后端必须验证该 ID 存在于
`electronic_seal_models`。不存在时返回 HTTP 400，不允许保存悬空引用。

视频 AI 的 `GET /models` 保持接口格式不变。由于目标记录已从 `models` 删除，
它自然不再返回电子封条模型。

## 前端调用

电子封条页面不再导入视频 AI 的 `listModels()`，改为调用
`GET /electronic-seal/models`。视频 AI 的模型管理、任务创建和推理页面不修改接口。

这次只完成表、读取接口和运行时引用隔离，不新增电子封条模型上传、编辑或删除页面。

## 兼容与回滚

- `model_id` 保持 `903eb72b`，现有 4 条电子封条配置无需改值。
- registry 目录和文件不移动，避免部署时复制或丢失权重。
- 回滚时先把记录从 `electronic_seal_models` 写回 `models`，再删除配置外键，最后
  停用新接口和新表。不能先删除新表，否则配置外键会阻止操作。
- 迁移期间先停止 `electronic-seal-worker`，迁移完成并校验后再启动，避免 Worker
  在中间状态读取模型。

## 测试与验收

需要覆盖以下测试：

1. 新表字段与 `models` 一致，目标记录完整迁移且原表不再包含该记录。
2. `GET /models` 不返回 `903eb72b`。
3. `GET /electronic-seal/models` 只返回电子封条模型，并保持响应字段兼容。
4. 电子封条配置引用有效模型时保存成功，引用未知模型时返回 400。
5. 默认配置初始化从新表解析 `903eb72b`，不再扫描视频 AI 模型表。
6. Worker 能从新表读取元数据并加载现有权重文件。
7. 外键阻止删除仍被电子封条配置引用的模型。
8. MySQL 迁移脚本重复执行不会重复插入、重复建表或重复建约束。

验收完成后，视频 AI 与电子封条模型列表互不混用，现有电子封条 4 个算法仍能使用
`903eb72b` 正常启动和推理。
