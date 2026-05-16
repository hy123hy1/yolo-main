# 视频 AI 训练与事件分析平台

一个以视频流接入、AI 检测、事件中心、录像中心、数据集管理、模型训练与模型管理为核心的可扩展平台。

当前项目已经具备以下主线能力：

- RTSP/RTMP 通道接入与媒体代理
- 浏览器预览播放（WebRTC / HLS-fMP4 回退）
- 单通道多模型 AI 配置
- 区域配置与事件抓拍
- 事件中心与事件图片查看
- 录像中心
- 数据集上传、标注、训练、模型管理
- 元数据落 SQLite，后续可切换 MySQL / PostgreSQL

## 1. 项目定位

本项目不是单纯的 YOLO 训练工具，而是一个面向视频 AI 场景的综合平台，目标是把以下能力放到同一套系统中：

- 设备 / 通道 / 媒体流管理
- AI 模型配置与视频分析
- 事件告警与抓拍图归档
- 录像查询与回放
- 数据集、标注、训练、模型管理
- 后续扩展 GB28181、ONVIF、AI 事件推送、第三方平台集成

适用场景包括：

- 园区 / 工地 / 车间视频告警
- 安全帽、人员、区域入侵等目标检测
- 视频通道统一管理与 AI 任务配置
- 训练模型上线后的闭环验证

## 2. 当前架构

系统当前由 3 个核心部分组成：

- `frontend`
  Vue 3 + Vite 前端，负责首页、通道、事件、录像、数据集、训练、模型等页面
- `backend`
  FastAPI 后端，负责业务接口、AI 分析任务、元数据同步、文件落盘
- `zlm-server`
  ZLMediaKit 媒体服务，负责流代理、WebRTC / RTMP / HLS 播放能力

整体链路如下：

1. 摄像头或视频源通过 `RTSP/RTMP` 接入
2. 后端将流注册到 `ZLMediaKit`
3. 前端通过 `WebRTC` 优先播放，失败时回退到 `HLS-fMP4`
4. AI 分析线程按通道配置读取视频流并执行模型推理
5. 命中规则后生成事件、保存抓拍图、进入事件中心
6. 事件图片与元数据可通过接口供第三方平台调用

## 3. 目录结构

```text
yolo-main/
├─ backend/                 # FastAPI 后端
│  ├─ configs/              # config.toml 配置
│  ├─ data/                 # SQLite、通道、事件、录像、快照等数据
│  ├─ models/               # 模型注册目录
│  └─ src/                  # 后端源码
├─ frontend/                # Vue 3 前端
├─ docker/                  # Docker / compose / ZLM 配置
├─ docs/                    # 文档
├─ build-backend-base.bat   # 构建后端基础镜像
├─ build-backend-app.bat    # 构建后端应用镜像
└─ README.md
```

## 4. 核心功能

### 4.1 通道与媒体

- 创建 RTSP / RTMP 通道
- 自动推流到 ZLMediaKit
- 浏览器预览
- 在线 / 预览状态区分
- 浏览器专用转码流
- 区域配置底图抓拍

说明：

- `在线` 表示媒体服务中能探测到该路流
- `播放中` 表示当前浏览器页面里的预览播放器正在播放

### 4.2 AI 能力

- 单通道支持绑定多个模型
- 每个模型单独配置：
  - 模型
  - 类别
  - 置信度
  - 帧间隔
  - 区域
  - 是否保存抓拍图
- AI 任务可启动 / 停止
- AI 配置支持从模型类别自动读取

当前多模型逻辑说明：

- 同一个流中多个模型是独立运行、独立触发事件
- 不做模型之间的耦合判断
- 如果需要“模型 A + 模型 B 联合判断”，需后续新增规则引擎

### 4.3 事件中心

- 事件卡片展示
- 点击图片查看大图
- 大图显示识别框与类别
- 支持按通道、类别、时间范围筛选
- 支持分页与每页数量切换
- 事件抓拍图支持缓存

### 4.4 录像中心

- 录像列表查询
- 时间范围筛选
- 回放入口预留

### 4.5 数据集 / 标注 / 训练 / 模型

- 数据集上传
- 标注任务创建
- BBox 标注
- 训练任务启动
- 模型上传与模型管理
- 模型类别列表自动读取

## 5. 元数据与存储

当前项目采用“文件 + 元数据库”的混合模式：

- 业务对象原始数据仍落在 `backend/data/` 目录
- 元数据同步进入数据库
- 默认数据库为 SQLite

当前默认 SQLite 文件：

- `backend/data/metadata.db`

当前元数据模式：

- 默认：SQLite
- 可切换：MySQL / PostgreSQL
- 配置入口：`backend/configs/config.toml`

当前配置示例：

```toml
[Data]
  MetadataDBEnabled = true
  MetadataDBStrict = false

  [Data.Database]
    Dsn = "./data/metadata.db"
```

说明：

- 如果 `Dsn` 是文件路径，则自动解析为 SQLite
- 如果 `Dsn` 是 `mysql+pymysql://...` 或 `postgresql+psycopg2://...`，则切换到对应数据库

## 6. 运行方式

### 6.1 Docker 开发模式

推荐使用开发模式运行：

```bash
cd docker
docker compose up -d backend frontend-dev zlm-server
```

启动后默认端口：

- 前端开发页：`http://localhost:3000`
- 后端接口：`http://localhost:8000`
- ZLMediaKit HTTP：`http://localhost:8010`

### 6.2 Docker 生产预览模式

```bash
cd docker
docker compose up -d --build backend frontend zlm-server
```

### 6.3 常用命令

启动：

```bash
docker compose -f docker/docker-compose.yml up -d backend frontend-dev zlm-server
```

重启后端：

```bash
docker compose -f docker/docker-compose.yml restart backend
```

查看容器：

```bash
docker ps
```

查看后端日志：

```bash
docker logs yolo-backend
```

## 7. 配置说明

主配置文件：

- [backend/configs/config.toml](/G:/docker/yolo-main/backend/configs/config.toml)

当前关键配置项：

- `Server.EnableTraining`
  控制是否启用训练相关接口
- `Data.Database.Dsn`
  元数据库 DSN，默认 SQLite
- `Media.APIBase`
  后端访问 ZLM API 地址
- `Media.PlayBase`
  前端播放基地址
- `Media.WebRTCBase`
  WebRTC 播放地址

后端启动时会自动读取 `config.toml` 并注入环境变量。

## 8. 接口能力

当前系统已经可以作为一个“能力提供方”被其他平台调用。

推荐的对外集成方式是：

- 查询类：HTTP API
- 实时告警类：Webhook / 后续消息队列
- 图片与媒体资源：URL / 文件接口

适合开放给第三方的接口包括：

- 通道管理接口
- 播放地址接口
- AI 配置接口
- 事件分页查询接口
- 事件图片接口
- 录像查询接口
- 模型管理接口

不建议直接让第三方平台连接业务数据库。

## 9. 当前技术栈

### 前端

- Vue 3
- Vite
- Vue Router
- Axios
- ECharts

### 后端

- FastAPI
- Uvicorn
- OpenCV
- Ultralytics YOLO
- Pydantic Settings

### 媒体

- ZLMediaKit
- WebRTC
- RTSP / RTMP / HLS-fMP4

### 数据

- SQLite（当前默认）
- 可扩展 MySQL / PostgreSQL

### 部署

- Docker
- Docker Compose

## 10. 当前已知特点与限制

### 已优化项

- 通道页与事件页已做页面缓存
- 事件图片已加缓存头
- 事件页已做分页
- 事件卡片图片已启用懒加载
- 通道预览切回时优先恢复首屏可见卡片

### 仍需持续优化项

- WebRTC 恢复播放仍可能存在 1~3 秒波动
- 事件分页目前虽然已是真分页，但时间范围过滤仍可继续下沉到后端增强
- 多模型联合规则尚未实现
- 区域与检测框联动预览仍可继续增强
- README 中未展开完整 API 文档，详见 `API_DOCUMENTATION.md`

## 11. 后续规划建议

建议后续优先扩展以下能力：

- 事件时间范围后端过滤
- 事件推送 webhook
- 录像回放链路完善
- 多模型联合规则引擎
- AI 任务运行态监控
- ONVIF / GB28181 接入
- 外部平台集成接口清单

## 12. 补充说明

项目目前已经从“训练平台”逐步演进为“视频 AI 平台”，README 也按当前状态做了更新。

如果后面你要继续扩展：

- AI 事件联动
- 第三方平台对接
- 数据库切换 MySQL
- 设备协议接入

建议同步维护以下文档：

- `README.md`
- `API_DOCUMENTATION.md`
- `docs/architecture.md`（建议后续补一份）

