# Task/Channel DB-First Consolidation Design

## Goal

收敛 WVP 通道同步、任务创建、算法新增、区域配置这条主流程的接口与数据源，避免前后端继续同时依赖 JSON/DB 双轨读取和过渡期兼容参数。

本次设计的重点不是重写整套存储，而是明确：

- 主流程到底走哪些接口
- 哪些接口只是兼容/调试用途
- 哪些列表接口必须切成 DB 主读
- 哪些前端 `prefer_metadata` 分支和旧路径需要删除

## Main Flow

主流程固定为：

1. 同步 WVP 通道
   - `POST /channels/sync-wvp`
2. 新建任务时获取可选通道
   - `GET /channels/available-for-task`
3. 新建任务
   - `POST /tasks`
4. 任务列表/详情
   - `GET /tasks`
   - `GET /tasks/{task_id}`
5. 获取当前任务可绑定模型
   - `GET /tasks/{task_id}/available-models`
6. 获取任务算法列表
   - `GET /tasks/{task_id}/algorithms`
7. 新增/编辑/删除算法
   - `POST /tasks/{task_id}/algorithms`
   - `PUT /tasks/{task_id}/algorithms/{algorithm_id}`
   - `DELETE /tasks/{task_id}/algorithms/{algorithm_id}`
8. 获取/保存算法私有区域
   - `GET /tasks/{task_id}/algorithms/{algorithm_id}/regions`
   - `PUT /tasks/{task_id}/algorithms/{algorithm_id}/regions`

## Data Source Policy

### Tasks

- `tasks`、`task_algorithms`、`algorithm_regions` 相关读取统一改为 DB 主读。
- JSON 仍保留为运行态文件和同步源，但不再作为前端主列表和详情的默认读取来源。
- 如果 DB 中无记录，再回退 JSON，作为兼容兜底。

### Channels

- `channels` 列表与“可用于新建任务的通道列表”改为 DB 主读。
- `raw_json` 继续作为扩展字段来源，用于保留 `ext.upstream`、`ext.zone_preview`、`ext.task_preview` 等运行信息。
- 通道在线状态探测和运行态更新仍可写回 JSON，并同步刷新 DB。

### Models

- `models` 列表与详情改为 DB 主读。
- 模型目录文件仍是上传/导出/训练产物的物理来源。
- `ModelMetaRecord.raw_json` 作为模型扩展元数据快照来源。

## Removal Scope

### Remove From Main Flow

- `prefer_metadata=true|false` 前端分支
- `GET /channels/{channel_id}/zone-preview/play-url` 的前端调用入口

### Keep As Debug/Compatibility Only

- `/tasks/*/metadata/*`
- metadata resync 相关接口

这些接口不是本次主流程的一部分，但暂时保留，避免影响已有调试能力和已有测试。

### Delete If Confirmed Unused

- `frontend/src/api/channels.ts` 中未被页面使用的 `getChannelZonePreviewPlayUrl`
- `backend/src/api/routes/channels.py` 中仅为旧前端保留的 `/{channel_id}/zone-preview/play-url`

## Route Contract Changes

### Tasks API

- `GET /tasks`
- `GET /tasks/{task_id}`
- `GET /tasks/{task_id}/algorithms`
- `GET /tasks/{task_id}/available-models`

以上接口默认 DB 主读，不再依赖 `prefer_metadata`。

### Channels API

- `GET /channels`
- `GET /channels/available-for-task`

以上接口默认 DB 主读，并在返回前结合运行态刷新在线/播放信息。

### Models API

- `GET /models`
- `GET /models/{model_id}`

默认 DB 主读。

## Frontend Changes

- `Channels.vue` 中任务相关加载逻辑去掉 `prefer_metadata` 分支。
- 新建任务、任务详情、算法列表、可选模型列表全部走统一 API。
- 删除未使用的区域流旧接口调用。
- 保持区域配置、电子围栏播放、任务启停现有交互不变。

## Risks

1. 由于 JSON 仍是运行态事实来源，DB 主读必须保证 `save_json -> sync metadata` 这一链路可靠。
2. `channels` 的在线状态是运行时计算值，DB 主读时不能直接返回过期缓存，仍需在列表读取阶段刷新。
3. `models` 详情页涉及训练曲线、文件大小、权重路径，DB 主读时仍要保留文件系统补充逻辑。

## Success Criteria

- 前端主流程不再传 `prefer_metadata`
- 同步 WVP 通道、新建任务、添加算法、配置区域只走一套明确的接口
- `tasks/channels/models` 列表默认从 DB 读取
- 删除至少一条确定未使用且冗余的旧接口路径
