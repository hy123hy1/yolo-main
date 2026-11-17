# 人员计数识别 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在独立摄像头健康服务中增加可按通道启停的 YOLO 人员计数能力，复用现有视频帧，把有人时的计数采样保存到 `person_count`，并在人数变化时生成符合监管命名规则的标注图片。

**Architecture:** `ChannelWorker` 继续负责单路视频读取和健康检测；统一的 `PersonCountCoordinator` 从每个 Worker 的 `FrameBuffer` 获取最新帧，每通道每秒最多推理一次。`PersonCountDetector` 在进程内延迟加载一份 `yolo11s.pt`，只统计类别名为 `person` 的目标，人员计数故障与健康检测隔离。

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, OpenCV, Ultralytics YOLO, MySQL/PyMySQL, pytest, Docker Compose.

---

## 文件边界

- Create: `camera-health-service/detectors/person_count.py`，模型加载、person 过滤和人数结果。
- Create: `camera-health-service/person_count/snapshot_manager.py`，标注图片绘制和监管文件名。
- Create: `camera-health-service/worker/person_count_coordinator.py`，多通道最新帧调度和内存状态。
- Create: `camera-health-service/sql/2026-08-26-person-count.sql`，MySQL 人工迁移。
- Create: `camera-health-service/.env.example`，模型、GPU、煤矿编码配置模板。
- Create: `camera-health-service/tests/test_person_count.py`，存储、Detector 和图片测试。
- Create: `camera-health-service/tests/test_person_count_coordinator.py`，调度和落库规则测试。
- Create: `camera-health-service/tests/test_person_count_api.py`，接口与路径安全测试。
- Modify: `camera-health-service/storage/models.py`、`storage/repository.py`，通道字段和计数记录。
- Modify: `camera-health-service/config.py`、`docker-compose.yml`，环境变量和 GPU。
- Modify: `camera-health-service/worker/manager.py`、`main.py`，共享 FrameBuffer、热启停和启动恢复。
- Modify: `camera-health-service/api/routes.py`，通道编码、开关、状态、记录和图片接口。
- Modify: `camera-health-service/README.md`，迁移和运维说明。

## Task 1: 数据模型、Repository、配置和迁移

**Files:**
- Modify: `camera-health-service/storage/models.py`
- Modify: `camera-health-service/storage/repository.py`
- Modify: `camera-health-service/config.py`
- Create: `camera-health-service/sql/2026-08-26-person-count.sql`
- Create: `camera-health-service/.env.example`
- Test: `camera-health-service/tests/test_person_count.py`

- [ ] **Step 1: Write failing persistence tests.**

测试应覆盖：

~~~python
def test_channel_keeps_manual_person_count_fields(repo):
    channel = repo.upsert_channel({
        "gb_id": "gb-1",
        "name": "测试通道",
        "camera_code": "12345678901234567890",
        "person_count_enabled": True,
    })
    assert channel["camera_code"] == "12345678901234567890"
    assert channel["person_count_enabled"] is True

def test_create_person_count_record(repo, channel):
    row = repo.create_person_count_record({
        "channel_id": channel["id"],
        "analysis_type": "01",
        "analysis_case": "0001",
        "snapshot_path": None,
    })
    assert row["analysis_type"] == "01"
    assert row["analysis_case"] == "0001"
~~~

还要断言 WVP 再次 upsert 不覆盖人工维护的 `camera_code` 和 `person_count_enabled`。

- [ ] **Step 2: Run tests and verify RED.**

~~~powershell
Set-Location G:\docker\yolo-main\camera-health-service
pytest tests/test_person_count.py -q
~~~

Expected: FAIL because model fields and Repository methods are missing.

- [ ] **Step 3: Implement models and Repository.**

Add to `Channel`:

~~~python
camera_code: Mapped[str | None] = mapped_column(String(20))
person_count_enabled: Mapped[bool] = mapped_column(
    Boolean, nullable=False, default=False
)
~~~

Add `PersonCountRecord` mapped to `person_count` with `id`, `channel_id`, `analysis_type`, `analysis_case`, `snapshot_path` and `created_at`. Add `set_camera_code`, `update_person_count_config`, `create_person_count_record`, `get_person_count_record` and paginated `list_person_count_records`. Validate the camera code with `^\d{20}$`, require `analysis_type == "01"` and require a non-zero four-digit `analysis_case`.

- [ ] **Step 4: Add Settings and MySQL migration.**

Read these values from environment without checking CUDA during module import:

~~~python
mine_code: str
person_count_model_path: Path
person_count_interval_seconds: float
person_count_confidence: float
person_count_image_size: int
person_count_device: str
person_count_snapshot_dir: Path
~~~

Create migration SQL that adds `channels.camera_code CHAR(20)` and `channels.person_count_enabled TINYINT(1) DEFAULT 0`, narrows `person_count.analysis_type` to `CHAR(2)`, narrows `analysis_case` to `VARCHAR(4)` and creates `idx_person_count_channel_created`. Document that `create_all` does not alter existing MySQL tables.

- [ ] **Step 5: Verify GREEN.**

~~~powershell
pytest tests/test_person_count.py -q
~~~

Expected: PASS for storage and configuration tests.

## Task 2: YOLO Detector and annotated snapshots

**Files:**
- Create: `camera-health-service/detectors/person_count.py`
- Create: `camera-health-service/person_count/__init__.py`
- Create: `camera-health-service/person_count/snapshot_manager.py`
- Test: `camera-health-service/tests/test_person_count.py`

- [ ] **Step 1: Write failing detector and image tests.**

Use an injected fake model. Verify a model where `person` is not class 0, ignore other classes and low-confidence person boxes, encode `1 -> "0001"` and `12 -> "0012"`. Assert the generated basename is exactly:

~~~text
12345678901234567890_01_20260826103001.jpg
~~~

and reject camera codes that are not 20 digits.

- [ ] **Step 2: Run tests and verify RED.**

~~~powershell
pytest tests/test_person_count.py -q
~~~

Expected: FAIL because Detector and SnapshotManager do not exist.

- [ ] **Step 3: Implement Detector.**

Define `PersonDetection` and `PersonCountResult` dataclasses. Load Ultralytics lazily, call inference with configured confidence, image size and device, resolve `person` through result/model names instead of hard-coding class 0, and return integer box coordinates plus confidence. Keep model factory injection so unit tests never load the real PT file.

- [ ] **Step 4: Implement SnapshotManager.**

Draw only person boxes, `person {confidence:.2f}` and `Person Count: N`. Save atomically under:

~~~text
/app/data/camera_health/person_count/{channel_id}/{yyyyMMdd}/
~~~

with basename:

~~~text
{20-digit camera_code}_01_{yyyyMMddHHmmss}.jpg
~~~

Snapshot errors must not prevent the Coordinator from writing a count row with `snapshot_path = NULL`.

- [ ] **Step 5: Verify GREEN.**

~~~powershell
pytest tests/test_person_count.py -q
~~~

Expected: all Detector, encoding and image tests PASS.

## Task 3: Coordinator and shared-frame integration

**Files:**
- Create: `camera-health-service/worker/person_count_coordinator.py`
- Modify: `camera-health-service/worker/manager.py`
- Test: `camera-health-service/tests/test_person_count_coordinator.py`
- Test: `camera-health-service/tests/test_channel_worker.py`

- [ ] **Step 1: Write failing coordinator tests.**

Fake FrameBuffer, Detector, Repository and SnapshotManager. Test these transitions:

~~~text
None -> 1: write row and image
1 -> 1: write row, no image
1 -> 2: write row and image
2 -> 0: no row, internal count becomes 0
0 -> 1: write row and image
stale/error: no row, previous count unchanged
~~~

Also assert the same FrameBuffer instance owned by `ChannelWorker` is registered and no second reader is constructed.

- [ ] **Step 2: Run tests and verify RED.**

~~~powershell
pytest tests/test_person_count_coordinator.py -q
~~~

Expected: FAIL because Coordinator does not exist.

- [ ] **Step 3: Implement Coordinator.**

Use one daemon thread and a lock-protected registration dictionary. For each enabled channel, enforce the configured interval, reject missing/stale/unchanged frame sequences, run one Detector serially, and retain only the latest frame rather than creating an unbounded queue. Maintain `last_person_count`, `last_inference_at`, `last_success_at`, `last_error` and `last_sequence` per channel.

- [ ] **Step 4: Integrate WorkerManager.**

Pass `worker.frame_buffer` to Coordinator after starting/reusing the health Worker. Enabling/disabling personnel counting with unchanged video sources must register/unregister only the counting task and must not restart `FFmpegStreamReader`. Stop and close paths must unregister the channel.

- [ ] **Step 5: Verify GREEN and regression behavior.**

~~~powershell
pytest tests/test_person_count_coordinator.py tests/test_channel_worker.py tests/test_health_repository.py -q
~~~

Expected: all selected tests PASS.

## Task 4: API and startup lifecycle

**Files:**
- Modify: `camera-health-service/api/routes.py`
- Modify: `camera-health-service/main.py`
- Test: `camera-health-service/tests/test_person_count_api.py`
- Test: `camera-health-service/tests/test_api.py`

- [ ] **Step 1: Write failing API tests.**

Cover:

~~~text
PUT /channels/{channel_id}/camera-code
PUT /channels/{channel_id}/person-count/config
GET /channels/{channel_id}/person-count/status
GET /channels/{channel_id}/person-count/records
GET /person-count/records/{record_id}/snapshot
~~~

Validate 20-digit camera codes, hot reconcile on config changes, `current_count=null` before first successful inference, pagination, 404 for missing images and rejection of stored paths outside the configured image root.

- [ ] **Step 2: Run tests and verify RED.**

~~~powershell
pytest tests/test_person_count_api.py -q
~~~

Expected: FAIL because routes are missing.

- [ ] **Step 3: Implement routes and stable responses.**

Use explicit Pydantic request bodies. Return `snapshot_url` only when a row has `snapshot_path`. Resolve and validate the stored path under the configured person-count root before `FileResponse`. Never accept a filesystem path from the client.

- [ ] **Step 4: Implement startup recovery and shutdown.**

In `main.py` create one Detector, SnapshotManager and Coordinator, start Coordinator, inject it into WorkerManager, and reconcile persisted channels that already have local video addresses. Startup recovery must not call WVP play endpoints. Stop WorkerManager and Coordinator during application shutdown.

- [ ] **Step 5: Verify GREEN.**

~~~powershell
pytest tests/test_person_count_api.py tests/test_api.py -q
~~~

Expected: all selected tests PASS.

## Task 5: Compose, documentation and full verification

**Files:**
- Modify: `camera-health-service/docker-compose.yml`
- Modify: `camera-health-service/README.md`
- Modify: `camera-health-service/docs/superpowers/specs/2026-08-26-person-count-design.md` if needed

- [ ] **Step 1: Load runtime configuration and provide GPU access.**

Load optional `camera-health-service/.env`, preserve all existing database/WVP values, and add `gpus: all`, `NVIDIA_VISIBLE_DEVICES=all` and `NVIDIA_DRIVER_CAPABILITIES=compute,utility`. Do not modify or restart video-AI, electronic-seal or frontend services.

- [ ] **Step 2: Document migration and operation.**

README must explain the manual SQL migration, `camera_code` binding, `person_count_enabled` switch, `CAMERA_HEALTH_MINE_CODE`, the difference between `analysis_type=01` and the people-count `analysis_case`, and the four new query/configuration endpoints.

- [ ] **Step 3: Run full verification.**

~~~powershell
Set-Location G:\docker\yolo-main\camera-health-service
pytest -q
python -m compileall -q .
docker compose -f docker-compose.yml config
~~~

Expected: all tests pass, compileall exits 0 and Compose config parses.

- [ ] **Step 4: Validate only the camera-health container.**

After the migration has been applied to the target MySQL database, force-recreate only `camera-health-service`. Verify model file existence, `torch.cuda.is_available()` from inside the container, `GET /health` and one enabled channel. Do not claim GPU works unless the in-container command returns `True`.

## Final Verification Checklist

- [ ] WVP sync preserves manual `camera_code` and `person_count_enabled`.
- [ ] `mine_code` comes from `.env` and is not duplicated per record.
- [ ] Health and person counting share one reader and FrameBuffer.
- [ ] Model loads once and only `person` is counted.
- [ ] Positive counts write one record per successful one-second sample.
- [ ] Only count changes save annotated images.
- [ ] Image basename is `{camera_code}_01_{yyyyMMddHHmmss}.jpg`.
- [ ] Zero, stale frames and inference errors do not create records.
- [ ] Existing health tests remain passing.
- [ ] Existing MySQL schema is updated by explicit migration, not assumed from `create_all`.

