# 310P3 视频 AI 后端验证包

## 包的范围

本包保留项目根目录层级，仅包含后端源码、原始 TOML 配置、工具、迁移脚本和独立昇腾部署文件。
不包含前端、camera-health-service、历史数据、本机模型、测试、Git 仓库和 NVIDIA 部署文件。
backend/src 必须完整保留：当前视频 AI 入口也导入部分电子封条共享模块，不能按服务名称直接删目录。

配置文件按原样打包，可能包含数据库密码和其他连接信息，仅在可信网络传输。
没有修改原项目数据库连接，也没有为新服务器默认选择业务数据库。

## 已完成与未完成验证

目标机已验证 Python 3.12、CANN 9.1.1、源码构建的 aclruntime/ais_bench，以及一个 310P3 OM 的全零输入推理。
这不是本包业务容器的验收结论；尚未验证框坐标、多路视频、业务依赖、报警录像与接口链路。
用户选择跳过独立图片验证，后续应在单路任务中确认框与类别。

当前 Dockerfile 在缺少 FFmpeg/ffprobe 时通过 openEuler DNF 安装 ffmpeg，
随后实际试编码一帧 libx264；缺少编码器时构建失败，不生成伪装可用的镜像。
DNF 仓库是否可达及是否提供所需编码器仍需在目标机验证；GitHub 不可达不代表 DNF/PyPI 一定不可达。
OpenCV 使用无界面的 headless 4.11.0.86，避免额外 GUI 依赖。
昇腾专用依赖放宽为 numpy>=1.26,<3，不再强制降级硬件冒烟验证所用的 NumPy 2.5.3。
这不是所有 NumPy 版本均已验收的承诺，必须在构建出的业务镜像内重跑 OM 推理。
原 NVIDIA 依赖文件没有改变。
纯推理包不再安装 matplotlib，模型服务仅在实际生成训练曲线时加载它。
昇腾部署不提供本轮未安装依赖的训练绘图功能，原 NVIDIA 绘图代码和依赖保持不变。

## 服务器准备

解压到专用目录，例如 /opt/yolo-backend-ascend，保持 backend 与 docker 同级。
先在宿主机检查，不需要启动业务：

```bash
docker-compose version
docker-compose ls
docker image inspect yolo-ascend-sdk:310p3-test --format '{{.Id}}'
```

当前宿主机为 Docker 18.09，已安装独立的 Compose V2.20.3，命令为 docker-compose。
仍需通过 ls 及后续构建、运行确认这台厂商定制引擎与部署配置的兼容性。
本文件不设置 platform：Docker 18.09 的 API 1.39 不支持创建容器时指定平台所需的 API 1.41。
使用宿主机本地的 ARM64 镜像；不要从 x86 机器导入同名 AMD64 镜像。

可先在解压根目录单独构建，不必为构建填写任何数据库连接，也不会启动业务：

```bash
DOCKER_BUILDKIT=0 docker build \
  --build-arg ASCEND_BASE_IMAGE=yolo-ascend-sdk:310p3-test \
  -f docker/ascend/Dockerfile \
  -t yolo-backend:ascend-310p .
```

此处使用宿主机架构和旧版构建器，不要求额外安装 Buildx。
如 DNF 或 PyPI 下载失败，应按实际失败源准备离线依赖，不直接修改驱动或业务配置。

## 配置与构建

在确认运行依赖和 Compose 条件后执行：

```bash
cd /opt/yolo-backend-ascend/docker/ascend
cp .env.example .env
```

在新服务器的 .env 中填写以下项，不是修改原 Windows 项目的配置：

```dotenv
ASCEND_BASE_IMAGE=yolo-ascend-sdk:310p3-test
ASCEND_DEVICE_ID=0
ASCEND_ENV_SCRIPT=/usr/local/Ascend/ascend-toolkit/set_env.sh
BACKEND_PORT=8000
METADATA_DATABASE_URL=mysql+pymysql://admin:替换为URL编码密码@mysql:3306/sp-ai-yolo?charset=utf8mb4
MYSQL_PUBLISHED_PORT=3306
MYSQL_DATA_DIR=/data/mysql/data
MYSQL_CONF_DIR=/data/mysql/conf
MYSQL_LOG_DIR=/data/mysql/logs
PUBLIC_HOST=192.168.1.179
ZLM_SECRET=填写与ZLM配置相同的密钥
```

数据库密码中的特殊字符必须 URL 编码，例如 @ 写为 %40。数据库 URL 使用 Compose 服务名 mysql 和容器内部端口 3306。

检查 backend/configs 中 WVP、媒体、报警推送等原连接是否适用于新服务器。
现有 main.py 会在启动时初始化数据库结构、恢复任务并启动后台组件，因此首次启动不是只读操作。
推荐使用单独的验收数据库，并避免与原 NVIDIA 后端同时运行相同任务。
不会因为不上传 backend/data 就自动隔离远程数据库中的旧任务。

### 将已有 MySQL 纳入本 Compose

现有 MySQL 容器 mysql8 的数据、配置和日志为宿主机 bind mount：
`/data/mysql/data`、`/data/mysql/conf`、`/data/mysql/logs`。
Compose 的 mysql 服务复用这三个目录，不初始化空数据卷。切换时严禁两个 MySQL 进程同时打开同一个数据目录。

```bash
# 先确认路径确实存在，并备份数据库（使用客户端交互输入密码）
mkdir -p /data/mysql/backup
docker exec -i mysql8 mysqldump -uadmin -p --single-transaction --routines --triggers sp-ai-yolo > /data/mysql/backup/sp-ai-yolo-before-compose.sql

# 停止旧实例但保留容器和数据目录
docker stop mysql8

# 在项目 docker/ascend 目录启动 Compose 管理的 MySQL
docker-compose up -d mysql
docker-compose logs --tail 100 mysql
```

确认 Compose MySQL 正常且原库表仍在后，再启动后端。后端、MySQL、ZLM 自动位于同一个 Compose 网络，
分别通过 mysql:3306 和 zlm-server:80 通信。
如果新 MySQL 启动失败，不要删除旧容器或清空 `/data/mysql/data`；先停止 Compose MySQL，再执行 `docker start mysql8` 回退。

```bash
docker-compose config --quiet
```

backend/data 和 backend/models 不在包内，需要在服务器创建空目录用于持久化：

```bash
mkdir -p ../../backend/data ../../backend/models
```

仅在业务镜像依赖和单次 OM 推理通过、连接已核对后启动视频 AI：

```bash
docker-compose --profile video-ai up -d --no-build backend
docker-compose logs --tail 100 -f backend
curl -f http://127.0.0.1:8000/health
```

不要启用 electronic-seal profile，本轮只验证视频 AI。
避免让临时验证容器与业务容器同时占用设备 0，先完成临时容器成果备份再安排切换。

OM 不在本包内。通过 backend/tools/prepare_om_package.py 将正确的 OM 打成模型 ZIP，
标准 COCO-80 模型可显式使用 --coco，然后沿用 POST /models/upload 上传。
上传后先建立一个通道、一个 OM 算法的验收任务，再检查事件、录像与 Java 推送。
打包和模型上传不会替代硬件及业务验收。

## 统一 Compose 中的 ZLM

只修改 docker/ascend/docker-compose.yml，原 NVIDIA Compose 不变。
后台视频 AI 与电子封条分别放在 video-ai 与 electronic-seal profile，默认只启动 ZLM。
无需填写数据库连接即可单独运行：

```bash
cd docker/ascend
docker-compose up -d --no-build zlm-server
docker-compose exec zlm-server sh -c 'sed -n "/^\[api\]/,/^\[/p" /opt/media/conf/config.ini'
docker-compose exec zlm-server vi /opt/media/conf/config.ini
```

先查看、再修改已有 [api] 段的 secret 为独立随机值，不使用官方公开默认密钥。
如宿主机具备 openssl，可使用 openssl rand -hex 24 生成；不要把密钥发送到聊天或日志中。
修改后执行 docker-compose restart zlm-server，并将相同值填入服务器 .env 的 ZLM_SECRET。
仅设置 .env 的 ZLM_SECRET 不会修改 ZLM 配置，它只会传给后端。
如旧版镜像没有 vi，可用 docker-compose cp 导出、在宿主机编辑后再复制回容器。

配置使用命名卷，首次创建时保留官方镜像内的默认配置，而不是用空宿主机目录覆盖配置。
容器内编辑在命名卷中持久化；不要执行 down -v，否则会删除配置和媒体数据卷。
日志目录也持久化，json-file 轮转仅约束标准输出，需另行关注 ZLM 文件日志占用。

本轮只映射 HTTP 8010:80、RTSP 554:554、RTMP 1935:1935，不照搬原机器的自定义端口。
后端 API 通过同一 Compose 网络的 http://zlm-server:80 访问，客户端播放使用 PUBLIC_HOST。
没有开放 WebRTC 媒体端口或 GB28181 RTP 端口，也不表示 WVP 已接入本机 ZLM；需要时独立配置。
运行环境需限制管理 API 的网络访问范围，避免将含管理接口的 HTTP 端口无保护地暴露公网。

本补丁新增了后端入口的空数据库连接/空密钥检查。启动后端前重新构建业务镜像以应用检查，
ZLM 本身不需要重新构建，也不需要停止 NPU 验证容器。
数据库和密钥确认后，仅启动视频 AI：docker-compose up -d --no-build backend。
