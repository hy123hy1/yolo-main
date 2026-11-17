# 模型默认置信度与报警间隔设计

## 背景

当前系统中的检测参数存在两层：

- `models`：模型元数据，负责模型管理与前端候选展示
- `task_algorithms`：任务内算法实例，负责任务运行时的真实检测参数

现状是运行时检测逻辑已经主要读取 `task_algorithms.confidence` 与 `task_algorithms.alert_interval`，但 `models` 表尚未显式维护这两个模型级默认参数，导致：

- 模型管理页无法统一配置模型默认阈值
- 新增算法时默认值来源不清晰
- 前后端在“默认参数”和“运行参数”上容易混淆

## 目标

在 `models` 表中新增：

- `confidence`
- `alert_interval`

并将它们定义为“模型默认值”，用于前后端展示和新增算法时的默认灌入，不直接作为运行时唯一参数源。

## 核心原则

### 1. `models` 只负责默认值

`models.confidence` 与 `models.alert_interval` 仅表示该模型在新增任务算法时建议采用的默认值。

### 2. `task_algorithms` 仍负责运行时参数

任务启动、检测执行、事件冷却等运行逻辑继续读取：

- `task_algorithms.confidence`
- `task_algorithms.alert_interval`

不在运行时实时回查 `models`。

### 3. 新增算法时完成“默认值固化”

当用户在任务中新增算法时：

- 如果请求显式传入 `confidence` / `alert_interval`，则使用请求值
- 如果请求未传入，则自动使用 `models` 表中的默认值

一旦新增成功，参数固化到 `task_algorithms`，后续模型默认值变更不回溯影响历史任务。

## 推荐方案

### 方案 A：模型表作为默认值源头，任务算法表作为运行值

这是本次采用方案。

优点：

- 运行链路稳定，不增加检测时额外查库
- 历史任务参数不被模型配置变更意外影响
- 前端语义清晰：模型管理管默认值，任务算法管实例值
- 与现有检测逻辑兼容，改动范围可控

### 方案 B：运行时实时读取模型表

不采用。

原因：

- 一个模型可能被多个任务共用，修改模型会即时影响所有任务
- 运行链路耦合加重
- 排查问题时难以判断任务到底用了哪一套参数

## 数据结构调整

### `models` 表

新增字段：

- `confidence: Float | None`
- `alert_interval: Integer | None`

建议默认值：

- `confidence = 0.25`
- `alert_interval = 60`

说明：

- 数据库存量记录允许为空，但服务层对外返回时要补齐默认值
- 这样可以兼容旧数据，并避免迁移期间前端拿到空值

## 接口与服务层行为

### 模型管理接口

涉及：

- `GET /models`
- `GET /models/{model_id}`
- `PUT /models/{model_id}`

要求：

- 返回模型详情与列表时带上 `confidence`、`alert_interval`
- 更新模型时允许修改这两个字段
- 若未设置，则服务层返回默认值 `0.25` / `60`

### 任务新增算法候选接口

涉及：

- `GET /tasks/{task_id}/available-models`

要求：

- 将模型默认 `confidence`、`alert_interval` 一并返回给前端
- 前端新增算法弹窗可直接展示这些默认值

### 任务新增算法接口

涉及：

- `POST /tasks/{task_id}/algorithms`

要求：

- 如果前端传了 `confidence` / `alert_interval`，保留前端值
- 如果前端未传，则从模型默认值补齐
- 最终统一写入 `task_algorithms`

### 任务详情与算法列表接口

涉及：

- `GET /tasks/{task_id}`
- `GET /tasks/{task_id}/algorithms`

要求：

- 继续返回 `task_algorithms` 中已固化的 `confidence`、`alert_interval`
- 不再混读 `models`，防止任务展示值随模型变动而漂移

## 检测运行逻辑

运行时保持现状：

- `AnalysisService` 继续从任务算法配置读取 `confidence`
- 告警冷却继续读取任务算法配置中的 `alert_interval`

这样本次无需重构分析主链路，只需要确保任务算法在创建时已经拿到正确默认值。

## 兼容性要求

### 旧模型兼容

旧 `models` 数据可能没有这两个字段。处理方式：

- 数据库允许为空
- 服务层对外读取时补默认值

### 旧任务兼容

旧任务算法本身已经有 `confidence` 与 `alert_interval` 字段，运行逻辑不变，不需要回填。

## 实施范围

涉及文件预计包括：

- `backend/src/db/models.py`
- `backend/src/services/model_service.py`
- `backend/src/services/task_service.py`
- `backend/src/api/routes/models.py`
- `backend/src/api/routes/tasks.py`

如存在数据库初始化或迁移逻辑，也需同步补齐 `models` 表字段。

## 测试要点

需要覆盖以下行为：

1. 模型列表与详情能返回默认 `confidence`、`alert_interval`
2. 更新模型后，字段正确入库并可再次读出
3. 新增算法时，未显式传值会自动继承模型默认值
4. 新增算法时，显式传值会覆盖模型默认值
5. 检测运行时仍然读取 `task_algorithms`，而不是 `models`

## 结论

本次改动采用“模型默认值 + 任务实例值”双层结构：

- `models`：默认模板
- `task_algorithms`：运行实例

这是当前最简单、最稳、最不容易引入回归的方案。
