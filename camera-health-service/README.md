# Camera Health Service

独立的摄像头健康服务，负责自有 `channels` 表、WVP 同步、持续取流和摄像头画面健康检测。
它不依赖视频 AI 任务，也不修改视频 AI 或电子围栏逻辑。

## 运行

本地调试：

```powershell
$env:PYTHONPATH = "G:\docker\yolo-main\camera-health-service"
uvicorn main:app --app-dir G:\docker\yolo-main\camera-health-service --host 0.0.0.0 --port 9002
```

Docker：

```powershell
docker network create yolo-network
cd camera-health-service
docker compose up -d
```

人员计数参数写入同目录 `.env`，可参考 `.env.example`。其中
`CAMERA_HEALTH_MINE_CODE` 是服务所属煤矿的固定编码；`camera_code` 是每个通道独立的
20 位摄像仪编码，不由 WVP 同步覆盖。

默认 WVP 配置：

- `WVP_API_BASE=http://192.168.1.36:18080`
- `WVP_CHANNEL_LIST_PATH=/api/common/channel/list`
- `WVP_CHANNEL_PLAY_PATH=/api/common/channel/play`

## 接口

- `POST /channels/sync-wvp`
- `GET /channels`
- `GET /channels/{channel_id}`
- `GET /channels/{channel_id}/health`
- `GET /channels/{channel_id}/health/events`
- `GET /channels/{channel_id}/worker`
- `PUT /channels/{channel_id}/camera-code`
- `PUT /channels/{channel_id}/person-count/config`
- `GET /channels/{channel_id}/person-count/status`
- `GET /channels/{channel_id}/person-count/records`
- `GET /person-count/records/{record_id}/snapshot`
- `GET /workers`
- `GET /channels/{channel_id}/snapshot/latest`
- `GET /channels/{channel_id}/snapshot/image`
- `POST /channels/{channel_id}/snapshot/reload`
- `GET /health`

## 约定

- `gb_id` 是唯一标识。
- `snapshot_path` 是唯一落库的基准图字段。
- `source_url` 变化、`stream` 变化或手动重置时重建基准图。
- 基准图默认使用 `rtsp`，失败后回退 `flv`。
- `sync-wvp` 只同步元数据，播放探测和抓图在后台执行。
- `channels.person_count_enabled=1` 且通道有视频地址时，才运行人员计数。
- 人员计数使用 `model/yolo11s.pt`，每通道每秒最多检测一次，只统计 `person`。
- `analysis_type` 固定为 `01`；`analysis_case` 是四位当前人数编码，例如 1 人为 `0001`。
- 有人时每次成功检测写一条 `person_count` 记录；人数变化时才保存带 person 框、置信度和
  `Person Count` 的标注图片。
- 标注图片文件名为 `{camera_code}_01_{yyyyMMddHHmmss}.jpg`，其中 `camera_code` 必须为
  20 位数字；煤矿编码不重复保存到计数记录，监管上传时从 `.env` 读取。

## 人员计数启用

首次使用前必须手动执行 `sql/2026-08-26-person-count.sql`。已有 MySQL 表不会被
SQLAlchemy `create_all` 自动补字段。

迁移后可以手动绑定通道编码并启用计数：

```sql
UPDATE channels
SET camera_code = '12345678901234567890',
    person_count_enabled = 1
WHERE id = '0001';
```

## 检测流程

每个通道最多运行一个 Worker。Worker 内部由两个独立循环组成：

1. FFmpeg 读取循环持续取帧并写入最新帧缓冲；RTSP 断开后尝试 FLV，不让检测或数据库写入阻塞取流。
2. 分析循环读取最新帧，执行 `occlusion`、`view_shift`、`blur`、`exposure`、`freeze`、
   `video_loss`、`shake` 和 `resolution` 八类检测。

异常状态必须持续达到 `CAMERA_HEALTH_ALARM_HOLD_SECONDS` 才创建事件，恢复状态必须持续达到
`CAMERA_HEALTH_RECOVERY_HOLD_SECONDS` 才关闭事件。无帧或帧超时只进入 `video_loss/offline`，不会把空帧传给其他检测器。

没有基准图时不会直接使用启动第一帧。Worker 先等待连续稳定帧，达到
`CAMERA_HEALTH_BASELINE_STABLE_FRAMES` 后再保存基准图到 `snapshot_path`。

## 数据表

服务启动时会创建缺失的 `camera_health_states` 和 `camera_health_events` 表。两张表只保存逻辑
`channel_id`，不建立指向 `channels` 的物理外键，便于独立部署和通道表软删除。

- `camera_health_states`：每个通道、每类算法的当前状态和最近一次指标。
- `camera_health_events`：报警开始、关闭、业务分析码、指标、异常图片路径和持续时长。

事件 `analysis_case` 映射：

| 编码 | 含义 | detector |
| --- | --- | --- |
| `0001` | 画面遮挡 | `occlusion` |
| `0002` | 摄像仪挪动 | `view_shift` |
| `0003` | 画面过暗 | `exposure` + `underexposed` |
| `0004` | 画面过曝 | `exposure` + `overexposed` |
| `0005` | 图像模糊 | `blur` |
| `0006` | 画面冻结 | `freeze` |
| `0007` | 视频丢失 | `video_loss` |
| `0008` | 画面抖动 | `shake` |
| `0009` | 分辨率异常 | `resolution` |

`algorithm_type` 表示 detector 大类，`analysis_case` 表示具体业务场景。
`event_path` 保存报警时抓取的异常图片路径；视频丢失没有可用帧时该字段为空。

已有 MySQL 表不会被 `create_all` 自动变更；生产环境如果表已存在但字段不同，需要单独执行迁移 SQL。

接口方式启用人员计数：

```powershell
Invoke-RestMethod -Method Put `
  -Uri http://localhost:9002/channels/0001/camera-code `
  -ContentType 'application/json' `
  -Body '{"camera_code":"12345678901234567890"}'

Invoke-RestMethod -Method Put `
  -Uri http://localhost:9002/channels/0001/person-count/config `
  -ContentType 'application/json' `
  -Body '{"enabled":true}'
```

仅重新创建摄像头健康容器：

```powershell
docker compose -f docker-compose.yml config
docker compose -f docker-compose.yml up -d --force-recreate camera-health-service
docker exec -w /app/camera-health-service camera-health-service python -c "import torch; print(torch.cuda.is_available())"
```

`torch.cuda.is_available()` 返回 `True` 才表示该容器实际获得 GPU。返回 `False` 时，健康检测
可以继续运行，但人员计数不能宣称使用 CUDA；需要检查宿主机 NVIDIA Container Toolkit、
Docker GPU runtime 和 Compose 配置。
