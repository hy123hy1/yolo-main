# Metric ID 重构设计

日期：2026-06-24

## 背景

当前计数实时表 `task_algorithm_metrics` 的主键 `id` 由：

- `task_id`
- `algorithm_id`
- `region_id/global`

拼接而成。

历史表 `task_algorithm_metric_history` 的主键 `id` 由：

- 实时表业务键
- 时间戳

继续拼接而成。

这导致两个问题：

1. `id` 过长，可读性差，不适合作为前后端展示或排查字段。
2. 实时表当前把 `id` 同时当作主键和业务唯一键使用，语义耦合，不利于后续维护。

## 目标

本次重构目标：

1. `task_algorithm_metrics.id` 改为数据库自增主键。
2. `task_algorithm_metric_history.id` 改为数据库自增主键。
3. 保持“同一任务 + 同一算法 + 同一统计类型 + 同一区域，只保留一条实时记录”的现有业务语义不变。
4. 不影响现有计数实时写入、历史采样、前端实时看板展示。

## 采用方案

采用方案二：

- `task_algorithm_metrics.id` 使用自增主键。
- 新增 `scope_key` 字段，统一表示统计范围：
  - 有区域时：`scope_key = region_id`
  - 无区域时：`scope_key = "global"`
- 对实时表增加唯一索引：
  - `(task_id, algorithm_id, metric_type, scope_key)`
- `task_algorithm_metric_history.id` 使用自增主键，不再拼接时间戳。

## 表结构调整

### 1. task_algorithm_metrics

现状：

- `id VARCHAR(128) PRIMARY KEY`

调整后：

- `id BIGINT AUTO_INCREMENT PRIMARY KEY`
- 新增 `scope_key VARCHAR(64) NOT NULL`
- 新增唯一索引：
  - `uk_task_algorithm_metrics_scope(task_id, algorithm_id, metric_type, scope_key)`

说明：

- `region_id` 允许为空，继续保留，用于表达真实区域。
- `scope_key` 仅用于唯一定位实时记录，避免 MySQL 对 `NULL` 唯一索引不生效的问题。

### 2. task_algorithm_metric_history

现状：

- `id VARCHAR(128) PRIMARY KEY`

调整后：

- `id BIGINT AUTO_INCREMENT PRIMARY KEY`

说明：

- 历史表天然是流水表，不再承担业务唯一定位职责。

## 服务层改造

### 1. MetricService 实时写入

当前逻辑：

- 先拼 `metric_id`
- 再按 `id` 查 `task_algorithm_metrics`

调整后：

- 先计算 `scope_key`
- 按 `(task_id, algorithm_id, metric_type, scope_key)` 查询实时记录
- 查到则更新
- 查不到则新增

返回结构仍然保留 `id` 字段，但此时返回的是数据库自增值。

### 2. MetricService 历史写入

当前逻辑：

- 通过 `_history_id(metric_id, now)` 生成历史主键

调整后：

- 不再生成历史字符串主键
- 直接插入，让数据库分配自增 `id`

### 3. 前端影响

前端当前主要使用：

- `metric_type`
- `metric_value`
- `raw_json.class_counts`
- `updated_at`

不依赖当前长字符串 `id` 的业务语义，因此无需联动改展示逻辑。

## 数据迁移

本次需要对现有 MySQL 数据做结构迁移。

迁移步骤：

1. 为 `task_algorithm_metrics` 新增 `scope_key` 字段。
2. 回填：
   - `scope_key = region_id`，当 `region_id` 非空
   - `scope_key = 'global'`，当 `region_id` 为空
3. 重建 `task_algorithm_metrics` 主键为自增 `BIGINT`
4. 增加唯一索引 `uk_task_algorithm_metrics_scope`
5. 重建 `task_algorithm_metric_history` 主键为自增 `BIGINT`
6. 调整 ORM 模型与服务层查询逻辑

说明：

- 由于 MySQL 不适合原地把 `VARCHAR PK` 直接改成 `AUTO_INCREMENT BIGINT PK`，实现上采用“重建表/迁移数据”的方式完成。
- 迁移过程中保留原有业务数据，不做清表。

## 验证点

完成后需要验证：

1. 同一 `task_id + algorithm_id + metric_type + scope_key` 连续写入时，只更新一条实时记录。
2. 不同区域或 `global` 范围会生成各自独立的实时记录。
3. 历史表每次采样新增一条新记录，`id` 为递增数值。
4. `TaskDetailPanel` 实时计数展示不受影响。
5. 服务重启后，实时表和历史表查询正常。

## 风险与边界

风险点：

1. 如果迁移前历史存在重复实时记录，唯一索引创建前需要先做去重。
2. 如果有外部脚本直接依赖当前字符串 `id`，需要同步适配。

本次不处理：

1. `raw_json` 字段收口。
2. 计数类表额外新增聚合列。
3. 其他事件/录像表主键重构。
