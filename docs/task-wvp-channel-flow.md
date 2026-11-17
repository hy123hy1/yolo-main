# WVP 通道到任务/区域配置的当前接口流程

本文档描述当前代码里实际在用的主流程，覆盖：

- 同步 WVP 通道
- 新建任务
- 新增算法
- 配置算法区域
- 启动/停止任务
- 电子围栏播放

同时列出已经清理掉的旧区域接口，避免前后端继续混用。

## 1. 同步 WVP 通道

前端入口：

- 页面：[frontend/src/pages/Channels.vue](G:\docker\yolo-main\frontend\src\pages\Channels.vue)
- 调用封装：[frontend/src/api/channels.ts](G:\docker\yolo-main\frontend\src\api\channels.ts)

接口：

- `POST /channels/sync-wvp?page=1&count=200&query=...`

后端路由：

- [backend/src/api/routes/channels.py](G:\docker\yolo-main\backend\src\api\routes\channels.py)

后端实现：

- `WVPService.sync_channels()`
- [backend/src/services/wvp_service.py](G:\docker\yolo-main\backend\src\services\wvp_service.py)

处理逻辑：

- 调用 WVP 列表接口读取远端通道
- 针对每一路通道调用 WVP 播放信息接口
- 提取 `source_url/app/stream`
- 以 WVP `streamId` 作为本地通道 `id`
- 落盘到 `backend/data/channels/*.json`

当前关键约束：

- 本系统不查 WVP 数据库
- 只依赖 WVP HTTP 接口同步通道
- 本地通道表是自己的副本，后续任务都绑定本地 `channel_id`

## 2. 新建任务

前端会先取“未绑定任务的通道”：

- `GET /channels/available-for-task`

后端实现：

- `ChannelService.list_available_for_task_channels()`
- 它会读取本地 `channels` 和 `tasks`，过滤掉已经绑定任务的通道

然后创建任务：

- `POST /tasks`

请求核心字段：

- `name`
- `channel_id`
- `source_url`
- `source_type`
- `frame_interval`

后端实现：

- `TaskService.create_task()`
- [backend/src/services/task_service.py](G:\docker\yolo-main\backend\src\services\task_service.py)

处理逻辑：

- 根据 `channel_id` 回填/校准任务视频源
- 生成任务基础结构
- 初始不要求带算法，但当前页面会引导用户继续新增算法

## 3. 新增算法

前端先取当前任务下还没绑定的模型：

- `GET /tasks/{task_id}/available-models?prefer_metadata=true`

后端实现：

- `TaskService.list_task_available_models()`

然后新增算法：

- `POST /tasks/{task_id}/algorithms`

编辑算法：

- `PUT /tasks/{task_id}/algorithms/{algorithm_id}`

删除算法：

- `DELETE /tasks/{task_id}/algorithms/{algorithm_id}`

当前算法配置的核心字段：

- `model_id`
- `algorithm_type`
- `scene_key`
- `scene_name`
- `rule_type`
- `labels`
- `detect_all_classes`
- `confidence`
- `alert_interval`
- `need_snapshot`
- `need_video_clip`
- `overlay_color`
- `postprocess_config`
- `regions`

当前重要规则：

- 区域是否走电子围栏画框，不再依赖 `need_overlay/requires_region`
- 只要算法下存在 `regions`，任务启动后该通道电子围栏预热即走 `zone`

## 4. 配置算法区域

当前页面真实调用的接口只有 3 个：

1. `GET /tasks/{task_id}/algorithms/{algorithm_id}/regions`
2. `POST /channels/{channel_id}/snapshot`
3. `PUT /tasks/{task_id}/algorithms/{algorithm_id}/regions`

前端组件：

- [frontend/src/components/channels/TaskRegionEditorModal.vue](G:\docker\yolo-main\frontend\src\components\channels\TaskRegionEditorModal.vue)

实际流程：

1. 打开区域配置弹窗
2. 调 `GET /tasks/{task_id}/algorithms/{algorithm_id}/regions` 拉当前算法已有区域
3. 调 `POST /channels/{channel_id}/snapshot` 获取底图
4. 用户本地编辑多个区域草稿
5. 最后一次性调用 `PUT /tasks/{task_id}/algorithms/{algorithm_id}/regions`

批量保存接口返回：

- `items`
- `playback_ready`
- `play_url`

含义：

- `items`：保存后的算法区域列表
- `playback_ready`：当前任务是否处于运行态，运行态才表示围栏播放可直接使用
- `play_url`：当前任务对应的浏览器播放地址，如果任务未启动，也只是“已生成地址”

## 5. 启动任务

接口：

- `POST /tasks/{task_id}/start`

后端实现：

- `TaskService.start_task()`

启动过程里的关键动作：

1. 校验当前通道是否已有其它运行中任务
2. 根据 `channel_id` 刷新任务最新 `source_url`
3. 启动 AI 分析线程
4. 调 `TaskService._warmup_task_preview()`

`_warmup_task_preview()` 当前职责：

- 先确保原始通道流可播放
- 收集任务下所有“启用 + 有模型 + regions 非空”的算法区域
- 如果存在区域：
  - 启动 `zone_preview`
  - 把最终 `webrtc_api/hls` 写回 `channels`
  - `task_preview.mode = zone`
- 如果不存在区域：
  - 只刷新原始浏览器播放流
  - `task_preview.mode = channel`

## 6. 停止任务

接口：

- `POST /tasks/{task_id}/stop`

后端实现：

- `TaskService.stop_task()`

停止逻辑：

- 停止 AI 分析线程
- 如果该任务当前是区域流任务，则停止 `zone_preview`
- 清理 `channels.ext.task_preview`

## 7. 电子围栏播放

当前页面调用顺序：

1. `GET /channels/{channel_id}/zone-preview/status`
2. 如果未运行，调用 `POST /channels/{channel_id}/zone-preview/start`
3. `GET /channels/{channel_id}/fence/browser-play-url`
4. 前端拿 `webrtc_api/hls` 建立 WebRTC 或回退 HLS 播放

当前后端实现原则：

- `POST /channels/{channel_id}/zone-preview/start` 不再扫描任务文件决定走哪种模式
- 它只读取通道里已经准备好的 `task_preview`
- 真正的“是原始流还是区域流”判断，已经前置到任务启动/区域保存后的预热阶段

关键接口返回：

- `GET /channels/{channel_id}/fence/browser-play-url`

返回核心结构：

- `resolved_mode`
- `status`
- `play_url.webrtc_api`
- `play_url.hls`

使用规则：

- `resolved_mode = channel`：电子围栏实际播原始浏览器流
- `resolved_mode = zone`：电子围栏实际播带框区域流

## 8. 当前保留的区域相关接口

当前建议只使用这些：

- `GET /tasks/{task_id}/algorithms/{algorithm_id}/regions`
- `PUT /tasks/{task_id}/algorithms/{algorithm_id}/regions`

配套只读/任务接口：

- `GET /tasks`
- `GET /tasks/{task_id}`
- `GET /tasks/{task_id}/algorithms`
- `GET /tasks/{task_id}/available-models`
- `POST /tasks`
- `PUT /tasks/{task_id}`
- `POST /tasks/{task_id}/start`
- `POST /tasks/{task_id}/stop`
- `GET /tasks/{task_id}/status`

## 9. 已清理的旧区域接口

以下接口已不再属于当前流程，已经从代码中清理：

- `GET /tasks/{task_id}/regions`
- `POST /tasks/{task_id}/regions`
- `PUT /tasks/{task_id}/regions/{region_id}`
- `DELETE /tasks/{task_id}/regions/{region_id}`
- `POST /tasks/{task_id}/algorithms/{algorithm_id}/region-links`
- `DELETE /tasks/{task_id}/algorithms/{algorithm_id}/region-links/{region_id}`
- `GET /tasks/{task_id}/algorithms/{algorithm_id}/regions/{region_id}`
- `POST /tasks/{task_id}/algorithms/{algorithm_id}/regions`
- `PUT /tasks/{task_id}/algorithms/{algorithm_id}/regions/{region_id}`
- `DELETE /tasks/{task_id}/algorithms/{algorithm_id}/regions/{region_id}`

清理原因：

- 任务共享区域机制已经废弃
- 单条区域增删改已被“批量替换区域”草稿保存模式替代
- 当前前端不再依赖这些接口

## 10. 代码关键位置

任务链路：

- [backend/src/services/task_service.py](G:\docker\yolo-main\backend\src\services\task_service.py)

围栏播放链路：

- [backend/src/services/channel_service.py](G:\docker\yolo-main\backend\src\services\channel_service.py)

WVP 同步链路：

- [backend/src/services/wvp_service.py](G:\docker\yolo-main\backend\src\services\wvp_service.py)

前端任务页：

- [frontend/src/pages/Channels.vue](G:\docker\yolo-main\frontend\src\pages\Channels.vue)

前端区域配置弹窗：

- [frontend/src/components/channels/TaskRegionEditorModal.vue](G:\docker\yolo-main\frontend\src\components\channels\TaskRegionEditorModal.vue)
