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
- `GET /channels/{channel_id}/person-count/status`
- `GET /channels/{channel_id}/person-count/records`
- `GET /person-count/records/{record_id}/snapshot`
- `PUT /channels/{channel_id}/person-analysis/config`
- `GET /channels/{channel_id}/person-analysis/config`
- `GET /channels/{channel_id}/person-analysis/status`
- `GET /person-analysis`
- `GET /channels/{channel_id}/person-entry/events`
- `GET /channels/{channel_id}/person-behavior/events`
- `GET /person-behavior/events/{event_id}/snapshot`
- `GET /channels/{channel_id}/person-entry/summary`
- `GET /person-entry/events/{event_id}/snapshot`
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
- `person_count.analysis_type` 按摄像头配置的统计方向写入：`01` 表示人员计数（进），
  `02` 表示人员计数（出）；`analysis_case` 是四位当前人数编码，例如 1 人为 `0001`。
- 有人时每次成功检测写一条 `person_count` 记录；人数变化时才保存带 person 框、置信度和
  `Person Count` 的标注图片。
- 标注图片文件名为 `{camera_code}_01_{yyyyMMddHHmmss}.jpg`，其中 `camera_code` 必须为
  20 位数字；煤矿编码不重复保存到计数记录，监管上传时从 `.env` 读取。

人员分析对用户表现为一个统一开关，启用后默认使用 `/model/yolo11s.pt` 和 Ultralytics
ByteTrack。Worker 复用同一次 YOLO 推理结果，内部仍分别产出当前画面人数和单向进入事件。
有四点防区时，用户绘制的第一条边表示人员通行的目标侧，系统自动生成距第一条边 1/3 的
单条判定线。目标从对侧稳定出现、朝第一条边穿线并在目标侧稳定后才写入
`person_entry_events`；反方向不计数。区域坐标默认是归一化比例，稳定帧、超时、锁定和线编号
使用服务默认值。自定义检测器
或模型未返回轨迹 ID 时，才使用最近质心跟踪作为兜底。
每次确认通行新增一条事件记录，`analysis_type=01` 表示人员进入，`analysis_type=02`
表示人员出去；朝第一条边通行属于人员出去。`analysis_case` 表示该通道当天同方向累计通行
人数，按 `0001`、`0002` 递增，次日重新从 `0001` 开始。
当天或固定时段人数通过 `/person-entry/summary` 聚合，不按帧写入。
人员进出事件抓拍图与人员计数抓拍图使用同一命名格式：
`{20位camera_code}_01_{yyyyMMddHHmmss}.jpg`，文件名不区分进出方向。

WVP 同步不会自动启用人员分析。生产环境首次使用统一配置前，执行
`sql/2026-09-15-unified-person-analysis.sql`。人员分析只使用统一配置接口；历史旧字段保留
用于迁移和已有数据兼容，不再提供旧配置接口。可以打开 `GET /person-analysis` 进行摄像头
绑定、在最新抓拍图上绘制虚拟线/区域和状态验证。

算法定义与摄像头算法配置：生产环境执行
`sql/2026-09-18-algorithm-configuration.sql`。当前注册已实现的 `person_count`、
`tailgating` 和 `loitering`，三者默认复用 `person_yolo11s_bytetrack` 推理组合。尾随算法按同一通行区域内
第二个不同轨迹目标与前一人的通行时间和归一化距离判定，默认阈值分别为 2 秒和 0.25，
同一报警冷却 5 秒。事件写入 `person_behavior_events`，并可通过行为事件接口查询和取图。
算法定义中的
`default_interval` 是默认分析周期，摄像头配置传入 `analysis_interval` 后覆盖该默认值；
未创建新的摄像头算法配置时，系统继续使用旧的 `person_analysis_*` 配置。

```powershell
Invoke-RestMethod -Method Get `
  -Uri http://localhost:9002/algorithms

Invoke-RestMethod -Method Put `
  -Uri http://localhost:9002/channels/0001/algorithms/person_count `
  -ContentType 'application/json' `
  -Body '{"enabled":true,"analysis_interval":1.0,"config":{"direction":"inside_to_outside"}}'

Invoke-RestMethod -Method Put `
  -Uri http://localhost:9002/channels/0001/algorithms/tailgating `
  -ContentType 'application/json' `
  -Body '{"enabled":true,"analysis_interval":0.2,"config":{"region_config":{"points":[[0.20,0.25],[0.80,0.25],[0.75,0.85],[0.25,0.85]]},"max_follow_seconds":2.0,"max_person_gap":0.25,"cooldown_seconds":5.0}}'

Invoke-RestMethod -Method Put `
  -Uri http://localhost:9002/channels/0001/algorithms/loitering `
  -ContentType 'application/json' `
  -Body '{"enabled":true,"analysis_interval":0.5,"config":{"region_config":{"points":[[0.20,0.25],[0.80,0.25],[0.75,0.85],[0.25,0.85]]},"dwell_seconds":30}}'
```

井口滞留按同一 ByteTrack 轨迹的脚底中心点在区域内连续停留时间判断，默认 `dwell_seconds=30`。
短暂漏检容忍和持续离开确认均固定为 2 秒，由服务统一管理，接口不接收这两个参数。一次进入
区域只报警一次，持续离开区域 2 秒后重新布防。既有生产库需额外执行
`sql/2026-09-20-person-behavior-metrics.sql`，用于保存实际滞留时长。

跨越闸机、翻越围栏、反向逆行、井口滞留和多人挤卡闯卡等场景，
需要对应检测器实现后再加入算法目录，不会仅通过配置表创建出未实现的算法。

生产环境首次启用人员进入前，执行 `sql/2026-09-15-person-entry.sql`。推荐只配置四点防区，
第一、第二个点组成的第一条边表示人员通行的目标侧，系统自动生成靠近该边 1/3 的判定线：

```json
{
  "enabled": true,
  "region_config": {
    "points": [[0.20, 0.25], [0.80, 0.25], [0.75, 0.85], [0.25, 0.85]]
  }
}
```

`P0 -> P1 -> P2 -> P3` 必须沿区域边界顺序闭合。系统只统计从 `P2-P3` 一侧走向
`P0-P1` 一侧的轨迹，不区分井内、井外，也不需要提交 `direction` 或 `line_config`。
`GET /channels/{channel_id}/person-analysis/config` 会返回只读的 `generated_line`。

## 人员分析启用

首次使用前必须手动执行 `sql/2026-08-26-person-count.sql`。已有 MySQL 表不会被
SQLAlchemy `create_all` 自动补字段。将既有 `person_count` 的 UUID 主键迁移为数字自增主键时，
还需先停止服务并备份数据，再执行 `sql/2026-08-26-person-count-auto-increment-id.sql`。
该迁移会保留计数记录，但会重新编号 `id`，依赖旧 UUID 的历史 URL 需要更新。

迁移后先绑定通道编码，再通过统一接口启用人员分析：

```sql
UPDATE channels
SET camera_code = '12345678901234567890'
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
自动生成、流地址变化重建和 `POST /channels/{channel_id}/snapshot/reload` 手工刷新会共用
基准图质量校验：要求分辨率匹配、曝光正常、清晰度合格，并通过相邻帧冻结/剧烈变化检查。
直接抓流校准默认先探测来源，再预热 5 秒，从最多 12 次采样中取得连续 5 张合格候选帧，
选择最清晰的一张；RTSP 失败后回退 FLV。校验失败不会覆盖旧基准图或更新数据库路径。
日志中的 `capture_elapsed_ms` 记录稳定抓图总耗时，`generation_elapsed_ms` 记录 Worker 自动
生成耗时，`request_elapsed_ms` 记录刷新接口的完整响应耗时；接口响应不返回耗时字段。
手工刷新优先从正在运行的 Worker 帧缓冲中按序号收集新帧，不启动额外 FFmpeg，也不执行
5 秒预热；缓冲不存在、帧已过期或质量候选不足时，才回退上述 RTSP/FLV 稳定抓流流程。
固定机位的连续帧可能几乎无变化，因此快路径以 Worker 持续运行的 `freeze` 状态为准：状态
正常时允许短时间静态候选帧，非正常时拒绝快路径并回退独立抓流校准。

## 数据表

服务启动时会创建缺失的 `camera_health_states` 和 `camera_health_events` 表。两张表只保存逻辑
`channel_id`，不建立指向 `channels` 的物理外键，便于独立部署和通道表软删除。

- `camera_health_states`：每个通道、每类算法的当前状态和最近一次指标。
- `camera_health_events`：报警开始、关闭、分析类型、业务分析码、指标、异常图片路径和持续时长；
  `analysis_type` 默认是 `03`。

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

`person_entry_events.id` 使用数据库 `BIGINT UNSIGNED AUTO_INCREMENT`。执行人员进入迁移脚本
后，已有 UUID 事件会按时间重新编号，旧的 UUID 事件 URL 不再有效；后续事件由数据库自动生成数字 ID。

已有 MySQL 表不会被 `create_all` 自动变更；生产环境如果表已存在但字段不同，需要单独执行迁移 SQL。

接口方式绑定人员分析：

```powershell
Invoke-RestMethod -Method Put `
  -Uri http://localhost:9002/channels/0001/camera-code `
  -ContentType 'application/json' `
  -Body '{"camera_code":"12345678901234567890"}'

Invoke-RestMethod -Method Put `
  -Uri http://localhost:9002/channels/0001/person-analysis/config `
  -ContentType 'application/json' `
  -Body '{"enabled":true,"region_config":{"points":[[0.20,0.25],[0.80,0.25],[0.75,0.85],[0.25,0.85]]}}'
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
