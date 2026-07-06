# 任务管理数据库规范化设计

## 1. 背景与目标

当前任务管理的数据虽然已经进入元数据库，但任务下的算法配置、区域配置、算法与区域关系，仍主要以 `tasks.raw_json` 的形式保存。现状可以工作，但存在几个明显问题：

- 不利于按算法、模型、类别、区域做结构化查询
- 不利于后续扩展多模型、多算法、多区域复用
- 不利于事件、录像、电子围栏和任务配置做数据库级关联
- 后续切换 MySQL 或做管理后台统计时，查询成本会越来越高

本次设计目标是：

- 在保留现有任务 JSON 结构兼容性的前提下，引入规范化表结构
- 为任务、算法、区域、多对多映射建立独立数据表
- 采用“双写平滑迁移”方式，避免一次性重构导致当前业务不稳定
- 为后续 AI 事件、录像、规则引擎、多任务统计查询打好基础

## 2. 设计原则

### 2.1 平滑迁移优先

本轮不直接废弃 `tasks.raw_json`，而是保留现有 JSON 结构作为兼容层和审计快照，同时把规范化数据同步写入新表。

### 2.2 结构化与兼容并存

高频查询字段拆成结构化列，完整对象继续保留 `raw_json`，既便于当前系统兼容，又便于后续逐步切换读路径。

### 2.3 任务为主，算法和区域为从

任务是主业务实体；算法、区域、区域绑定关系都围绕任务组织，避免把任务内关系散落到通道级配置里。

### 2.4 第一阶段只改写链路

先完成“保存即同步入新表”，暂不强制把分析服务、事件服务、电子围栏运行时全部切换到新表读取，降低联动风险。

## 3. 总体架构

采用“文件 JSON + SQLite 规范化表双写”的过渡架构：

- `data/tasks/*.json` 继续保留，作为当前运行态主源
- 元数据库中新增规范化表：
  - `tasks`
  - `task_algorithms`
  - `task_regions`
  - `task_algorithm_regions`
- 任务保存、算法编辑、区域编辑、区域绑定变化时：
  - 先更新任务 JSON
  - 再同步重建该任务对应的规范化表数据
- 第一阶段接口返回结构不变
- 第二阶段视稳定性再逐步把读路径切到数据库

## 4. 表结构设计

### 4.1 `tasks`

职责：保存任务主信息、运行态摘要和兼容快照。

建议字段：

- `id`
- `name`
- `source_url`
- `source_type`
- `channel_id`
- `frame_interval`
- `status`
- `algorithm_count`
- `region_count`
- `is_running`
- `started_at`
- `stopped_at`
- `last_analysis_at`
- `last_event_at`
- `last_error`
- `created_at`
- `updated_at`
- `raw_json`

说明：

- `status` 保存整体状态，如 `draft`、`running`、`stopped`、`error`
- `algorithm_count`、`region_count` 用于列表和统计
- `is_running` 是运行态摘要字段，便于筛选
- `raw_json` 保留当前完整任务对象

### 4.2 `task_algorithms`

职责：保存任务下每一个算法实例。

建议字段：

- `id`
- `task_id`
- `algorithm_type`
- `model_id`
- `enabled`
- `labels_json`
- `confidence`
- `alert_interval`
- `need_snapshot`
- `need_video_clip`
- `video_before_seconds`
- `video_after_seconds`
- `need_overlay`
- `overlay_color`
- `sort_order`
- `created_at`
- `updated_at`
- `raw_json`

说明：

- 一条算法配置对应一行
- `labels_json` 保存类别选择
- `sort_order` 用于前端按配置顺序展示
- `raw_json` 保留算法原始结构，兼容当前对象形态

### 4.3 `task_regions`

职责：保存任务共享区域池。

建议字段：

- `id`
- `task_id`
- `name`
- `type`
- `color`
- `coordinates_json`
- `enabled`
- `created_at`
- `updated_at`
- `raw_json`

说明：

- `type` 第一阶段默认 `polygon`
- `coordinates_json` 保存点位坐标
- `enabled` 支持区域临时停用而非删除

### 4.4 `task_algorithm_regions`

职责：保存算法与区域的多对多关系。

建议字段：

- `id`
- `task_id`
- `algorithm_id`
- `region_id`
- `created_at`

约束建议：

- 唯一约束：`(algorithm_id, region_id)`

说明：

- 一个算法可绑定多个区域
- 多个算法可复用同一个区域
- 冗余保留 `task_id`，便于按任务维度直接筛查和校验

## 5. 数据同步策略

### 5.1 第一阶段：双写

第一阶段保留任务 JSON 作为主源，保存任务时同步写入规范化表。

同步入口：

- 新建任务
- 编辑任务
- 新增算法
- 编辑算法
- 删除算法
- 新增区域
- 编辑区域
- 删除区域
- 区域与算法的绑定/解绑

### 5.2 单任务全量重建

针对单个任务，采用“先删后重建”的同步策略，而不是做局部 patch：

1. upsert `tasks`
2. 删除该任务旧的 `task_algorithm_regions`
3. 删除该任务旧的 `task_algorithms`
4. 删除该任务旧的 `task_regions`
5. 按当前任务 JSON 重建三张子表

这样做的优点：

- 逻辑简单
- 不容易残留脏关系
- 和当前任务对象结构天然一致

## 6. 读写链路改造

### 6.1 第一阶段

第一阶段只改写链路，不改前端返回结构。

保留：

- `TaskService` 继续基于现有任务对象结构工作
- `analysis_service` 继续从任务 JSON 读取运行配置
- 电子围栏、事件、录像链路继续按当前任务 JSON 运行

新增：

- `metadata_db_sync._sync_task()` 从“只写 `tasks.raw_json`”升级为同步四张表

### 6.2 第二阶段

待第一阶段稳定后，再逐步切换读路径：

- 任务列表优先从 `tasks` + `task_algorithms` 汇总
- 任务详情由规范化表拼装 DTO
- 分析服务改为直接加载结构化算法和区域配置
- 电子围栏直接按规范化表生成算法区域投影

## 7. 风险与应对

### 7.1 JSON 与数据库不一致

风险：

- JSON 保存成功，数据库同步失败

应对：

- 任务 JSON 仍为第一阶段主源
- 数据库同步失败时记录明确日志
- 后续可增加“任务元数据重建/重同步”入口

### 7.2 多对多关系残留

风险：

- 算法删除或区域删除后，映射关系残留

应对：

- 使用单任务“先删后重建”的同步策略

### 7.3 后续切读路径字段口径不一致

风险：

- JSON 和结构化表对字段语义的理解不一致

应对：

- 第一阶段不切运行时读路径
- 新表字段名和现有任务对象尽量保持一致

## 8. 校验方案

实施后至少验证以下场景：

1. 新建任务
   - `tasks` 有记录
   - `task_algorithms` 条数正确
   - `task_regions` 条数正确
   - `task_algorithm_regions` 关联正确

2. 新增第二个算法
   - `tasks.algorithm_count` 更新正确
   - 新算法落库
   - 原算法未丢失

3. 区域绑定与复用
   - 同一区域可被多个算法复用
   - 绑定关系写入正确

4. 删除算法/区域
   - 关联表无孤儿数据

5. 历史任务重同步
   - 旧的 `data/tasks/*.json` 可完整同步入新表

## 9. 改动范围

本次实施主要涉及：

- `backend/src/db/models.py`
  - 新增 `TaskAlgorithmMeta`
  - 新增 `TaskRegionMeta`
  - 新增 `TaskAlgorithmRegionMeta`
  - 扩展 `TaskMeta`

- `backend/src/services/metadata_db_sync.py`
  - 扩展 `_sync_task()`
  - 增加任务子表同步逻辑

- `backend/src/services/task_service.py`
  - 保持当前任务对象结构稳定
  - 保存后触发同步

- `backend/tests/test_task_service.py`
  - 增加规范化表同步验证

## 10. 结论

本次推荐方案是“规范化表 + 任务 JSON 双写”的平滑迁移路径。

它能在不打断当前任务、分析、事件、电子围栏功能的前提下，把任务算法与区域关系沉淀到数据库，支撑后续：

- 多算法任务
- 区域复用
- AI 事件归因
- 录像归因
- 后台统计查询
- MySQL 迁移

这是当前阶段风险最低、收益最高、最适合持续演进的一条路径。
