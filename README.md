# 视频 AI 与电子封条平台

一套面向矿区、园区和工业现场的视频分析平台，覆盖视频流接入、AI 检测、电子封条、事件告警、录像、数据集、模型训练和模型管理。

当前项目已经从单纯的 YOLO 训练工具，演进为由视频 AI 主后端和电子封条独立 Worker 共同组成的视频分析平台。

## 当前能力

- 视频 AI：通道管理、分析任务、算法配置、电子围栏区域、事件告警、录像和实时检测
- 电子封条：车辆驶出、空岗检测、摄像头遮挡、摄像头扭转角度识别
- 摄像头健康：在线状态采样、遮挡/偏移状态、健康事件和基准图校准
- 媒体播放：RTSP/RTMP 接入，ZLMediaKit 转发，WebRTC 优先播放，HLS-fMP4 作为回退
- 事件中心：事件分页、筛选、抓拍图、报警视频和处置状态
- 事件内容：视频 AI `events.content` 在事件生成时固化为“时间，地点检测到算法，请核实处理”
- 数据与模型：数据集上传、标注、训练、模型上传、模型类别读取和模型管理
- 第三方对接：通道、事件、媒体和电子封条接口可供 Java 后端或其他平台调用
- NVIDIA 多卡：视频 AI 持续检测任务支持[自适应 GPU 分配](docs/nvidia-adaptive-gpu.md)，统一通过 `backend/configs/config.toml` 的 `[AI]` 配置，当前已启用四卡候选

## 系统架构

```text
摄像头 / RTSP / RTMP
          │
          ├── ZLMediaKit ── WebRTC / HLS / HTTP-FLV ── 前端播放
          │
          ├── 视频 AI 主后端 ── YOLO 推理 ── events / recordings
          │
          └── 电子封条 Worker ── 独立通道 Worker ── electronic_seal_* 表
```

### 服务组成

| 服务 | 作用 | 默认端口 |
|---|---|---:|
| `frontend` / `frontend-dev` | Vue 3 前端页面 | 3000 |
| `backend` | 视频 AI 主后端和通用业务接口 | 8000 |
| `electronic-seal-worker` | 电子封条独立检测、报警和上传 Worker | 9001 |
| `zlm-server` | ZLMediaKit 流代理和浏览器播放 | 8010 |
| MySQL | 当前部署使用的元数据库 | 以 DSN 配置为准 |

`backend` 和 `electronic-seal-worker` 当前通过 Docker Compose 使用本地 `docker-backend` 镜像。两个服务的检测职责和事件表相互独立。

## 核心流程

### 视频 AI

1. 通道信息保存到 `channels` 表。
2. 分析任务绑定通道、模型、算法和区域。
3. 任务从通道的 `source_url` 读取检测流，使用 FFmpeg 解码视频帧。
4. YOLO 模型执行推理，算法规则和电子围栏区域约束检测结果。
5. 命中规则后写入 `events`，按配置生成报警图片和报警视频。
6. 事件接口返回结构化字段以及可直接展示的 `content`。

### 电子封条

1. 电子封条通道保存到 `electronic_seal_channels` 表。
2. 通道通过 WVP 的 `stream` 标识进行同步和绑定。
3. 每个已绑定通道由独立 Worker 长期读取实时流。
4. 车辆入口类通道运行车辆驶出检测，调度室类通道运行空岗检测。
5. 摄像头遮挡和摄像头扭转角度算法按通道健康状态运行。
6. 电子封条事件写入 `electronic_seal_event_index` 和对应算法明细表。

视频 AI 使用 `channels`，电子封条使用 `electronic_seal_channels`。电子封条通道优先占用对应视频源，避免同一通道同时被两个模块重复绑定。

## 目录结构

```text
yolo-main/
├─ backend/
│  ├─ configs/              # 主后端、电子封条和健康算法配置
│  ├─ data/                 # 数据库文件、事件、录像、快照和电子封条证据
│  ├─ migrations/           # MySQL 数据库迁移脚本
│  ├─ models/               # 模型文件和模型注册目录
│  ├─ src/                  # FastAPI、视频 AI 和电子封条源码
│  └─ tests/                # 后端测试
├─ frontend/                # Vue 3 + Vite 前端
├─ docker/                  # Dockerfile、Compose、ZLMediaKit 配置
├─ docs/                    # 架构、接口和运维文档
├─ API_DOCUMENTATION.md     # 接口文档
└─ README.md
```

## 运行方式

### 开发模式

```powershell
docker compose -f docker/docker-compose.yml up -d `
  zlm-server backend electronic-seal-worker frontend-dev
```

前端开发页：`http://localhost:3000`

### 生产预览模式

```powershell
docker compose -f docker/docker-compose.yml up -d --build `
  zlm-server backend electronic-seal-worker frontend
```

### 常用命令

```powershell
# 查看服务状态
docker compose -f docker/docker-compose.yml ps

# 查看视频 AI 主后端日志
docker compose -f docker/docker-compose.yml logs -f backend

# 查看电子封条 Worker 日志
docker compose -f docker/docker-compose.yml logs -f electronic-seal-worker

# 重启主后端
docker compose -f docker/docker-compose.yml restart backend

# 重启电子封条 Worker
docker compose -f docker/docker-compose.yml restart electronic-seal-worker
```

## 配置说明

### 主后端

配置文件：`backend/configs/config.toml`

关键配置项：

- `Server.HTTP.Port`：主后端端口，当前为 `8000`
- `Data.Database.Dsn`：元数据库连接配置
- `AI.InferenceDevice`：AI 推理设备，当前配置为 `cuda:0`
- `Media.APIBase`：后端访问 ZLMediaKit 的地址
- `Media.PlayBase`：浏览器播放地址基址
- `Media.WebRTCBase`：WebRTC 播放地址基址
- `WVP.APIBase`：WVP 服务地址
- `WVP.PublicFLVBase`：WVP 对外 HTTP-FLV 地址基址

当前代码支持 SQLite 和 MySQL，当前部署配置使用 MySQL。数据库 DSN 示例：

```toml
[Data]
  MetadataDBEnabled = true
  MetadataDBStrict = false

  [Data.Database]
    Dsn = "mysql+pymysql://root:password@host:3306/sp-ai-yolo?charset=utf8mb4"
```

### 电子封条 Worker

配置文件：

- `backend/configs/electronic_seal.toml`
- `backend/configs/electronic_seal_defaults.toml`
- `backend/configs/electronic_seal_thresholds.toml`

关键环境变量：

- `ELECTRONIC_SEAL_DEVICE`：推理设备，当前 Compose 默认 `cuda:0`
- `ELECTRONIC_SEAL_MAX_CHANNEL_WORKERS`：最大通道 Worker 数量
- `ELECTRONIC_SEAL_ONLINE_STATUS_INTERVAL_SECONDS`：在线状态统计间隔
- `ELECTRONIC_SEAL_BASELINE_WARMUP_SECONDS`：摄像头健康基准图预热时间
- `ELECTRONIC_SEAL_SUPERVISION_BASE_URL`：监管平台上传地址，未配置时不自动上传

## 主要接口入口

视频 AI 主后端：

- `/channels`：通道、播放、快照和区域
- `/tasks`：分析任务和算法配置
- `/events`：视频 AI 事件查询和处置
- `/recordings`：录像查询
- `/models`：模型管理
- `/datasets`：数据集和标注
- `/train`：训练任务
- `/camera-health`：摄像头健康算法

电子封条 Worker：

- `/electronic-seal/channels`：电子封条通道和角色绑定
- `/electronic-seal/channels/{channel_id}/configs`：虚拟线、空岗区域和健康配置
- `/electronic-seal/events`：电子封条事件查询和处置
- `/electronic-seal/channels/{channel_id}/counts`：车辆驶出累计统计
- `/health`：服务健康检查

完整接口参数和响应结构见 [API_DOCUMENTATION.md](API_DOCUMENTATION.md)。

## 数据表说明

视频 AI 主要使用：

- `channels`
- `tasks`
- `task_algorithms`
- `algorithm_regions`
- `events`
- `recordings`
- `models`

电子封条主要使用：

- `electronic_seal_channels`
- `electronic_seal_channel_profiles`
- `electronic_seal_configs`
- `electronic_seal_baselines`
- `electronic_seal_states`
- `electronic_seal_event_index`
- `electronic_seal_truck_departure_events`
- `electronic_seal_room_empty_events`
- `electronic_seal_camera_occlusion_events`
- `electronic_seal_camera_view_shift_events`
- `electronic_seal_upload_jobs`

电子封条事件已经按算法拆分明细表，旧的 `electronic_seal_events` 仅用于历史兼容和迁移审计。

## 相关文档

- [API_DOCUMENTATION.md](API_DOCUMENTATION.md)：主后端接口说明
- [docs/electronic-seal-operations.md](docs/electronic-seal-operations.md)：电子封条部署、通道归属、事件和上传运维说明
- [docs/superpowers/specs/2026-08-17-video-ai-event-content-design.md](docs/superpowers/specs/2026-08-17-video-ai-event-content-design.md)：视频 AI 事件 `content` 设计
- [backend/migrations](backend/migrations)：数据库迁移脚本
- [backend/tests/README.md](backend/tests/README.md)：隔离测试、关键链路回归及视频回放命令
- [docs/ascend-310p-deployment.md](docs/ascend-310p-deployment.md)：310P OM 推理配置、部署前核验和线上验证步骤

## 当前限制

- WebRTC 播放恢复、网络抖动和长时间播放稳定性仍依赖摄像头、ZLMediaKit 和网络环境。
- 视频 AI 与电子封条虽然共用模型和媒体基础设施，但事件表、通道归属和 Worker 生命周期是分开的。
- `events.content` 只对新生成事件自动写入，历史事件不会自动回填。
- 监管平台上传为后台任务，上传失败会保留本地事件和重试记录。
- 多模型联合规则、跨通道联合判断和完整录像回放能力仍可继续扩展。
