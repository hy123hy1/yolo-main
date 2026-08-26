# 人员计数识别实施方案

## 1. 背景与目标

在独立摄像头健康服务 `camera-health-service` 中增加人员计数能力，使用
`model/yolo11s.pt` 对实时视频帧执行目标检测，并将当前画面中的人员数量写入
MySQL `person_count` 表。

本功能必须满足以下目标：

- 仅对明确启用人员计数的通道运行 YOLO 推理。
- 复用现有 FFmpeg Reader 和 FrameBuffer，不为人员计数重复拉取视频流。
- YOLO 每个通道每 1 秒最多检测一次。
- 只统计模型输出中类别名称为 `person` 的目标。
- 检测人数大于 0 时，每次成功检测生成一条计数记录。
- 仅在人数相对上一次成功检测发生变化时保存标注图片。
- 人员计数推理、落库或图片保存失败时，不影响现有摄像头健康检测。
- 支持 Java 后端查询计数记录、计数运行状态和标注图片。

本期不实现人员跟踪、人员去重、进出方向判断和累计独立人数。`person_count`
保存的是当前画面人数的时间序列采样，不能把连续记录直接求和解释为累计人数。

## 2. 已确认的业务规则

### 2.1 通道启停

`channels` 表增加 `person_count_enabled` 字段：

```sql
ALTER TABLE channels
ADD COLUMN person_count_enabled TINYINT(1) NOT NULL DEFAULT 0
COMMENT '是否启用人员计数：0关闭，1启用';
```

只有同时满足以下条件的通道才运行人员计数：

1. `person_count_enabled = 1`；
2. 通道存在可用的 `source_url` 或 `flv`；
3. 通道 Worker 已经获得有效视频帧。

开关修改后热更新人员计数任务，不重启容器，也不重启该通道的 FFmpeg Reader。

### 2.2 计数编码

- `analysis_type` 固定为字符串 `01`，表示人员计数。
- `analysis_case` 是四位人数编码，不是健康异常类型。
- 1 人保存为 `0001`，12 人保存为 `0012`。
- `analysis_case` 始终按字符串处理，保留前导零。

`analysis_case` 最大只能表达 9999 人。该限制符合当前摄像头场景；如果后续业务需要
表达更大人数，应增加独立整数人数列，不应扩大编码字段后继续混用。

### 2.3 落库与图片规则

每次成功推理后按以下规则处理：

| 检测结果 | 是否写入 `person_count` | 是否保存标注图片 |
| --- | --- | --- |
| 首次成功检测到 N 人，N > 0 | 是 | 是 |
| 与上一次成功检测人数相同，N > 0 | 是 | 否 |
| 与上一次成功检测人数不同，N > 0 | 是 | 是 |
| 成功检测到 0 人 | 否 | 否，但内存人数更新为 0 |
| 从 0 人再次检测到 N 人 | 是 | 是 |
| 视频断流、帧过期或推理失败 | 否 | 否，且不把人数更新为 0 |

服务重启后没有可信的上一次人数。第一次成功检测到人员时按人数变化处理并保存图片。

标注图片只绘制满足置信度阈值的 `person` 检测框，显示每个目标的置信度，并在画面上
显示 `Person Count: N`。不绘制模型输出中的其他类别。

## 3. 数据库设计

沿用已经手工创建的 `person_count` 表。当前实际数据库中的 `analysis_case` 是
`VARCHAR(255)`，已经是字符型，但长度需要收紧。实施时执行：

```sql
ALTER TABLE person_count
MODIFY COLUMN analysis_type CHAR(2) NOT NULL DEFAULT '01'
COMMENT '分析类型：01人员计数',
MODIFY COLUMN analysis_case VARCHAR(4) NOT NULL
COMMENT '当前画面人数，左侧补零到4位';
```

代码模型按以下字段映射：

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `id` | `VARCHAR(64)` | 计数记录唯一 ID，使用 UUID hex |
| `channel_id` | `VARCHAR(64)` | 对应本服务 `channels.id` |
| `analysis_type` | `CHAR(2)` | 固定为 `01` |
| `analysis_case` | `VARCHAR(4)` | 当前人数的四位字符串编码 |
| `snapshot_path` | `TEXT NULL` | 人数变化时生成的标注图片路径 |
| `created_at` | `DATETIME` | 当前视频帧采集时间 |

不为 `channel_id` 建立物理外键，由 Repository 在写入前校验通道存在。建议保留或增加
以下组合索引，满足按通道和时间分页查询：

```sql
CREATE INDEX idx_person_count_channel_created
ON person_count (channel_id, created_at);
```

按照“有人时每秒一条”的规则，单通道持续有人一天最多生成 86400 条记录。本期不自动
清理历史计数，数据保留策略由部署方后续根据监管和统计周期确定。

## 4. 总体架构

```text
FFmpegStreamReader（每通道一条视频连接）
                 |
                 v
          FrameBuffer（最新帧）
             |          |
             |          +--> ChannelWorker 健康检测线程
             |
             +--> PersonCountCoordinator
                         |
                  每通道每秒取最新帧
                         |
                         v
               PersonCountDetector
               单例加载 yolo11s.pt
                         |
                 仅保留 person 类别
                         |
                         v
              PersonCountResultHandler
                 |                  |
                 v                  v
           人数变化时存图       每次 N > 0 落库
```

采用统一 `PersonCountCoordinator`，而不是在现有健康检测循环中直接执行 YOLO，也不为
每个通道各自加载模型。这样可以保证：

- 健康检测不被 YOLO 推理阻塞；
- 一个服务进程只加载一份模型和一份 GPU 权重；
- 多个通道不会并发调用同一个 Ultralytics 模型；
- GPU 忙时始终处理最新帧，不积压已经失去实时价值的旧帧。

## 5. 组件设计

### 5.1 PersonCountDetector

职责：加载 YOLO 模型、执行推理、过滤 `person`、返回检测框和人数。

- 模型在首次存在启用通道时延迟加载，每个进程只加载一次。
- 通过模型 `names` 按类别名称匹配 `person`，不硬编码类别编号 0。
- 使用 `.env` 中配置的置信度、输入尺寸和推理设备。
- 返回结构包含 `person_count`、检测框、置信度和推理耗时。
- 模型加载失败只影响人员计数模块，不使 FastAPI 和健康 Worker 退出。

### 5.2 PersonCountCoordinator

职责：管理启用通道、控制每秒采样、串行调用模型并维护通道运行状态。

- 每个通道注册现有 `FrameBuffer`，不持有第二路视频连接。
- 同一通道距离上次推理不足 1 秒时跳过。
- FrameBuffer 序号未变化或帧已过期时不重复推理旧帧。
- 每个通道最多保留一个待处理的最新帧，不建立无界队列。
- 维护 `last_person_count`、`last_inference_at`、`last_success_at` 和
  `last_error`。
- 只有成功推理结果才能更新 `last_person_count`；断流和异常不等同于 0 人。

多通道采用轮询方式执行推理。如果所有启用通道一次轮询耗时超过 1 秒，每个通道的
实际间隔会大于 1 秒，但不会积压历史帧。后续通道规模明显增加时，可以在不改变数据库
和 API 的前提下升级为批量推理。

### 5.3 图片生成

新增人员计数图片保存方法，输出目录为：

```text
/app/data/camera_health/person_count/{channel_id}/{yyyyMMdd}/
```

文件名格式：

```text
{camera_code}_{analysis_type}_{yyyyMMddHHmmss}.jpg
```

其中 `camera_code` 是绑定在 `channels` 表上的 20 位摄像仪编码，不能使用本服务内部的
`channel_id` 代替。`analysis_type` 为两位分析类型编码，人员计数固定为 `01`；时间戳使用
`yyyyMMddHHmmss` 格式。示例：

```text
12345678901234567890_01_20260826103001.jpg
```

生成图片前必须校验 `camera_code` 已配置且长度为 20 位；未配置或格式不正确时不生成
不符合监管命名规则的文件，并在人员计数运行状态中记录错误。

采用临时文件加原子替换方式写入，避免接口读取到未完成图片。图片保存失败时仍允许计数
记录落库，但 `snapshot_path` 保存为 `NULL`，并在运行状态中记录错误。

### 5.4 Repository

在现有 `CameraHealthRepository` 中增加以下能力：

- 更新 `channels.person_count_enabled`；
- 创建人员计数记录；
- 按记录 ID 查询；
- 按通道和时间倒序分页查询；
- 将 SQLAlchemy 数据对象转换为稳定的接口字典。

写入前校验 `channel_id` 对应通道存在，`analysis_type` 必须是 `01`，`analysis_case`
必须是四位数字且不为 `0000`。

### 5.5 WorkerManager 与启动恢复

`WorkerManager` 在启动、同步通道、播放地址更新和计数开关变化时协调人员计数注册：

- 有视频源且计数开启：注册通道 FrameBuffer；
- 计数关闭：从 Coordinator 注销，但健康 Worker 保持运行；
- 通道停止或视频源被移除：同步注销人员计数；
- 服务启动时遍历数据库中的可用通道并执行 reconcile，避免容器重启后必须再次调用
  WVP 同步接口才能恢复检测。

## 6. 配置设计

人员计数参数统一写入 `camera-health-service/.env`：

```dotenv
CAMERA_HEALTH_PERSON_COUNT_MODEL_PATH=/app/camera-health-service/model/yolo11s.pt
CAMERA_HEALTH_PERSON_COUNT_INTERVAL_SECONDS=1.0
CAMERA_HEALTH_PERSON_COUNT_CONFIDENCE=0.25
CAMERA_HEALTH_PERSON_COUNT_IMAGE_SIZE=640
CAMERA_HEALTH_PERSON_COUNT_DEVICE=cuda:0
CAMERA_HEALTH_PERSON_COUNT_SNAPSHOT_DIR=/app/data/camera_health/person_count
YOLO_CONFIG_DIR=/tmp
```

同时增加 `.env.example` 作为可提交的参数模板，实际 `.env` 继续由根目录 `.gitignore`
排除。代码中的 Settings 只负责读取和校验环境变量，不重复维护业务阈值。

`camera-health-service/docker-compose.yml` 增加：

```yaml
services:
  camera-health-service:
    env_file:
      - .env
    gpus: all
    environment:
      NVIDIA_VISIBLE_DEVICES: all
      NVIDIA_DRIVER_CAPABILITIES: compute,utility
```

当前容器已安装 Ultralytics 8.3.251 和 CUDA 版 PyTorch，但实测
`torch.cuda.is_available()` 为 `False`，说明当前 Compose 尚未把 GPU 提供给该容器。
修改 Compose 后需要重新创建容器，不需要重新构建镜像；启动后必须验证 CUDA 可用。

配置校验规则：

- 检测间隔必须大于 0；
- 置信度必须在 0 到 1 之间；
- 输入尺寸必须大于 0；
- 模型路径必须指向可读文件；
- 配置 `cuda:0` 但 GPU 不可用时明确标记人员计数不可运行，不静默切换 CPU。

## 7. API 设计

### 7.1 修改通道计数开关

```http
PUT /channels/{channel_id}/person-count/config
Content-Type: application/json

{
  "enabled": true
}
```

接口先更新数据库，再热注册或注销人员计数任务，返回通道计数配置和运行状态。

### 7.2 查询实时计数状态

```http
GET /channels/{channel_id}/person-count/status
```

响应示例：

```json
{
  "channel_id": "0001",
  "enabled": true,
  "running": true,
  "current_count": 2,
  "last_inference_at": "2026-08-26 10:30:01",
  "last_success_at": "2026-08-26 10:30:01",
  "last_error": null
}
```

当从未成功推理时 `current_count` 为 `null`，不能返回 0，以区分“确认无人”和“没有
有效检测结果”。

### 7.3 查询历史计数

```http
GET /channels/{channel_id}/person-count/records?page=1&page_size=20
```

支持可选的 `start_time` 和 `end_time` 查询参数，按 `created_at` 倒序返回。存在标注图的
记录同时返回 `snapshot_path` 和可访问的 `snapshot_url`。

### 7.4 获取标注图片

```http
GET /person-count/records/{record_id}/snapshot
```

接口校验记录存在、`snapshot_path` 位于人员计数图片根目录内且文件存在，再返回 JPEG。
禁止直接根据客户端提供的本地路径读取文件。

## 8. 异常隔离

- 视频断流或帧过期：暂停该通道推理，不生成 0 人记录。
- YOLO 推理失败：保存错误状态，下一个调度周期继续尝试。
- GPU 不可用或模型加载失败：人员计数模块不可运行，健康检测继续运行。
- 数据库写入失败：记录错误并继续后续检测，不重复补写旧帧。
- 图片保存失败：计数记录仍可写入，`snapshot_path = NULL`。
- 通道关闭开关：清理待处理帧和内存人数状态，再次开启后第一次有人视为人数变化。
- Coordinator 意外退出：Worker 状态接口显示不可运行，不能伪装为 0 人。

## 9. 测试方案

### 9.1 单元测试

- 模型结果只保留类别名称为 `person` 的目标。
- 置信度低于阈值的 person 不参与计数。
- 人数正确转换为四位 `analysis_case`。
- 0 人不写记录，正数每次成功推理都写记录。
- 人数不变不保存图片，人数变化保存标注图片。
- 推理失败和断流不把上一次人数改成 0。
- Repository 正确保存和分页查询人员计数记录。
- 非法通道、非法分析类型和非法人数编码被拒绝。

### 9.2 调度与接口测试

- 同一通道一秒内不会重复推理。
- FrameBuffer 序号未变化时不重复处理旧帧。
- 多通道轮询不会创建第二路 FFmpeg Reader。
- 修改开关后无需重启即可启停人员计数。
- 图片接口拒绝越过人员计数图片根目录的路径。
- 容器启动后自动恢复已启用通道。

### 9.3 集成验证

1. 执行数据库字段变更；
2. 使用更新后的 Compose 重新创建 `camera-health-service`；
3. 在容器内确认 `torch.cuda.is_available() == True`；
4. 开启一个存在 RTSP 地址的通道；
5. 确认模型只加载一次且每秒最多推理一次；
6. 连续保持 1 人，确认每秒落库但只有首条记录有图片；
7. 增加为 2 人，确认新记录为 `0002` 且生成新标注图；
8. 人员离开后确认不生成 `0000` 记录；
9. 模拟断流，确认健康检测可产生视频丢失事件，人员计数不误写 0 人；
10. 运行完整测试集，确保现有摄像头健康功能没有回归。

## 10. 实施顺序

1. 增加数据库模型、字段映射和 Repository 测试；
2. 实现人员计数 Detector 及模型结果过滤测试；
3. 实现人数变化判断、标注图片和落库规则；
4. 实现 Coordinator 并接入现有 FrameBuffer；
5. 接入 WorkerManager 的启停和启动恢复流程；
6. 增加配置、Compose GPU 声明和 `.env.example`；
7. 实现开关、状态、历史记录和图片接口；
8. 完成单元测试、集成测试和容器 GPU 验证。
