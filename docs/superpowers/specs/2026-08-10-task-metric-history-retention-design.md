# Task Metric History Retention Design

## Goal

保留 `task_algorithm_metric_history` 的历史指标数据，不因所属视频 AI 任务被删除而删除；历史行保留原始 `task_id`，用于追溯和查询。

## Current Behavior

- `task_algorithm_metric_history.task_id` 当前通过外键关联 `tasks.id`，并使用 `ON DELETE CASCADE`。
- 任务删除和元数据孤儿清理流程还会主动删除对应的历史指标行。
- `task_algorithm_metrics` 是实时指标表，仍然属于任务运行态数据，继续随任务删除。

## Design

### Data Model

将 `task_algorithm_metric_history.task_id` 改为普通的 `VARCHAR(64)` 字段，不再建立到 `tasks` 的外键。字段保持非空，已有和新增历史数据继续保存原始任务 ID。

任务删除后，历史表中的 `task_id` 可能无法关联到任务详情，这是预期的“已删除任务历史”状态；历史查询仍可按任务 ID、通道、算法、指标类型和时间范围过滤。

### Runtime Cleanup

从以下清理流程中移除对 `task_algorithm_metric_history` 的删除：

- 视频 AI 任务删除。
- 元数据孤儿任务清理。
- 删除任务 JSON 时的数据库同步清理。

实时表 `task_algorithm_metrics` 的现有清理逻辑不变。

### MySQL Migration

增加一次性迁移，删除现有外键 `task_algorithm_metric_history_ibfk_1`。迁移必须只影响历史指标表，不重建或清空历史数据。

模型定义和初始化/重建逻辑同步更新，防止后续启动时重新创建带 `CASCADE` 的外键。

### Compatibility

- 不改变历史指标 API 的响应字段。
- 不改变实时指标 API 和实时指标生命周期。
- 不删除已有历史数据。
- 任务详情接口对已删除任务返回不存在，不影响历史指标接口按原 `task_id` 查询。

## Verification

增加测试覆盖：

1. 删除一个同时拥有实时指标和历史指标的任务后，历史指标行仍存在且 `task_id` 不变。
2. 同一场景下实时指标行仍按原逻辑删除。
3. 元数据孤儿清理不删除历史指标。
4. MySQL 迁移后历史表不存在指向 `tasks` 的外键。

## Scope

本次不改变历史数据的保留周期、归档策略、分区策略或 API 结构。后续如需按时间清理，应另行设计数据生命周期策略。
