# Electronic Seal Model Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将电子封条模型 `903eb72b` 从视频 AI 的 `models` 表迁移到独立的 `electronic_seal_models` 表，并让电子封条 API、默认配置和 Worker 推理只读取独立模型数据。

**Architecture:** 保留共享 registry 中的权重路径和模型 ID，不复制或移动权重文件。后端为电子封条增加独立 SQLAlchemy 映射、Store 方法和只读 API；`electronic_seal_configs.model_id` 指向新表，视频 AI 的 `ModelService` 与 `GET /models` 保持原实现，因此迁移后电子封条模型不会出现在视频 AI 模型列表中。

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, MySQL 8, pytest, Vue 3, TypeScript, Node test runner, Docker Compose.

---

## 文件与职责

- Create: `backend/migrations/20260810_electronic_seal_models.sql` - MySQL 幂等迁移、数据复制、外键和源表清理。
- Modify: `backend/src/db/models.py` - 新模型表映射和电子封条配置外键。
- Modify: `backend/src/electronic_seal/store.py` - 新表建表清单、序列化、模型查询和配置引用校验。
- Modify: `backend/src/electronic_seal/defaults.py` - 默认模型从电子封条 Store 查询，不再扫描 registry 元数据。
- Modify: `backend/src/electronic_seal/manager.py` - 注入独立模型元数据读取器并暴露模型查询。
- Modify: `backend/src/electronic_seal/api.py` - 增加电子封条模型列表和详情接口。
- Modify: `frontend/src/api/electronicSeal.ts` - 增加独立模型 API 与类型。
- Modify: `frontend/src/pages/ElectronicSeal.vue` - 去掉视频 AI `listModels`，改用电子封条模型接口。
- Modify: `backend/tests/test_electronic_seal_schema.py` - 新表字段、配置外键和 schema helper 覆盖。
- Modify: `backend/tests/test_electronic_seal_store.py` - Store 模型查询和未知模型引用校验。
- Modify: `backend/tests/test_electronic_seal_defaults.py` - 默认模型只从新表解析。
- Modify: `backend/tests/test_electronic_seal_inference.py` - 推理适配器使用独立仓储元数据。
- Modify: `backend/tests/test_electronic_seal_manager.py` - Manager API 委托和 Worker 元数据注入。
- Modify: `backend/tests/test_electronic_seal_api.py` - 新接口响应、404 和配置 400。
- Modify: `frontend/tests/electronic-seal.test.mjs` - 前端不再依赖视频 AI 模型接口。

## Task 1: 先建立独立表映射和失败测试

**Files:**
- Modify: `backend/src/db/models.py`
- Modify: `backend/src/electronic_seal/store.py`
- Modify: `backend/tests/test_electronic_seal_schema.py`
- Modify: `backend/tests/test_electronic_seal_store.py`

- [ ] **Step 1: 写 schema 失败测试**

在 `test_electronic_seal_schema.py` 增加断言：`electronic_seal_models` 的字段集合与 `models` 相同；`ElectronicSealConfigMeta.model_id` 的外键目标为 `electronic_seal_models.id`；`ensure_electronic_seal_schema()` 创建新表。

```python
from src.db.models import ElectronicSealModelMetaRecord, ModelMetaRecord

def test_electronic_seal_model_table_matches_models_columns():
    assert set(ElectronicSealModelMetaRecord.__table__.columns.keys()) == set(
        ModelMetaRecord.__table__.columns.keys()
    )

def test_electronic_seal_config_model_id_targets_isolated_table():
    foreign_keys = list(ElectronicSealConfigMeta.__table__.c.model_id.foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "electronic_seal_models.id"
```

- [ ] **Step 2: 写 Store 失败测试**

使用现有 SQLite fixture 插入一个 `ElectronicSealModelMetaRecord`，验证 `list_models()`、`get_model()`、`find_model_by_name()`、`model_exists()`；再验证向 `upsert_config` 传入 `{"model_id": "missing"}` 会抛出明确的 `ValueError`，且数据库没有保存悬空引用。

```python
def test_store_reads_only_electronic_seal_models(store, session_factory):
    with session_factory() as session:
        session.add(ElectronicSealModelMetaRecord(id="seal-1", name="电子封条"))
        session.commit()
    assert store.list_models()[0]["model_id"] == "seal-1"
    assert store.get_model("seal-1")["name"] == "电子封条"
    assert store.find_model_by_name("电子封条")["model_id"] == "seal-1"
    assert store.model_exists("seal-1") is True
    assert store.model_exists("missing") is False

def test_store_rejects_unknown_electronic_seal_model(store):
    with pytest.raises(ValueError, match="electronic_seal_models"):
        store.upsert_config("channel-1", "room_empty", {"model_id": "missing"})
```

- [ ] **Step 3: 运行失败测试确认缺口**

Run:

```powershell
cd G:\docker\yolo-main\backend
pytest -q tests/test_electronic_seal_schema.py tests/test_electronic_seal_store.py
```

Expected: FAIL because the isolated model mapping and Store methods do not exist yet.

- [ ] **Step 4: 实现最小 schema 和 Store 支持**

在 `models.py` 中复制 `ModelMetaRecord` 的字段定义，类名为 `ElectronicSealModelMetaRecord`、表名为 `electronic_seal_models`；把 `ElectronicSealConfigMeta.model_id` 的 `ForeignKey` 改为新表。将新表加入 `ELECTRONIC_SEAL_TABLES` 的首位，保证 SQLite/MySQL schema helper 先创建被引用表。

在 `store.py` 中增加一个独立序列化函数，输出当前模型 API 使用的 `model_id`、`name`、`version`、`source`、`description`、`tags`、`classes`、`weights_path`、`file_size`、`file_size_mb`、`job_id`、`confidence`、`alert_interval`、`created_at`、`updated_at`，并增加：

Store 方法使用下列签名，并通过 SQLAlchemy 精确过滤新表：

```python
def list_models(self) -> list[dict]:
    with self.session_factory() as session:
        rows = session.query(ElectronicSealModelMetaRecord).order_by(
            ElectronicSealModelMetaRecord.created_at.desc(),
            ElectronicSealModelMetaRecord.id.asc(),
        ).all()
        return [_electronic_seal_model_dict(row) for row in rows]

def get_model(self, model_id: str) -> dict | None:
    with self.session_factory() as session:
        row = session.get(ElectronicSealModelMetaRecord, str(model_id))
        return _electronic_seal_model_dict(row) if row else None

def find_model_by_name(self, name: str) -> dict | None:
    with self.session_factory() as session:
        rows = session.query(ElectronicSealModelMetaRecord).filter(
            ElectronicSealModelMetaRecord.name == str(name)
        ).all()
        if len(rows) > 1:
            raise ValueError(f"expected one electronic-seal model named {name!r}, found {len(rows)}")
        return _electronic_seal_model_dict(rows[0]) if rows else None

def model_exists(self, model_id: str) -> bool:
    with self.session_factory() as session:
        return session.get(ElectronicSealModelMetaRecord, str(model_id)) is not None
```

`upsert_config()` 和 `ensure_default_configs()` 在写入非空 `model_id` 前调用 `model_exists()`，失败时抛出 `ValueError(f"electronic_seal_models model not found: {model_id}")`。提交前先校验新建行和更新行两条路径，确保失败不留下半提交状态。

- [ ] **Step 5: 运行 schema 和 Store 测试确认通过**

Run:

```powershell
cd G:\docker\yolo-main\backend
pytest -q tests/test_electronic_seal_schema.py tests/test_electronic_seal_store.py
```

Expected: PASS，且原有电子封条配置、事件、上传任务测试不受影响。

- [ ] **Step 6: 提交 schema/store 变更**

```powershell
cd G:\docker\yolo-main
git add backend/src/db/models.py backend/src/electronic_seal/store.py backend/tests/test_electronic_seal_schema.py backend/tests/test_electronic_seal_store.py
git commit -m "feat: isolate electronic seal model metadata"
```

## Task 2: 编写并验证 MySQL 数据迁移

**Files:**
- Create: `backend/migrations/20260810_electronic_seal_models.sql`
- Modify: `backend/tests/test_electronic_seal_schema.py`

- [ ] **Step 1: 写迁移静态失败测试**

断言迁移包含 `CREATE TABLE IF NOT EXISTS electronic_seal_models LIKE models`、目标模型 ID 校验、`task_algorithms` 引用保护、显式字段复制、`ON DUPLICATE KEY UPDATE`、新外键 `ON DELETE RESTRICT`、删除源模型以及重复执行保护。

- [ ] **Step 2: 运行迁移测试确认失败**

```powershell
cd G:\docker\yolo-main\backend
pytest -q tests/test_electronic_seal_schema.py -k migration
```

Expected: FAIL because the migration file does not exist.

- [ ] **Step 3: 写幂等 MySQL 迁移**

迁移必须停止在任何前置校验失败处，不能删除 `models` 中的记录。使用临时存储过程处理条件 DDL：

```sql
CREATE TABLE IF NOT EXISTS electronic_seal_models LIKE models;

DROP PROCEDURE IF EXISTS migrate_electronic_seal_model;
DELIMITER $$
CREATE PROCEDURE migrate_electronic_seal_model()
BEGIN
    DECLARE v_model_count INT DEFAULT 0;
    DECLARE v_task_refs INT DEFAULT 0;
    DECLARE v_config_refs INT DEFAULT 0;
    DECLARE v_fk_count INT DEFAULT 0;

    SELECT COUNT(*) INTO v_model_count FROM models WHERE id = '903eb72b';
    IF v_model_count <> 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'expected exactly one electronic seal model in models';
    END IF;

    SELECT COUNT(*) INTO v_task_refs FROM task_algorithms WHERE model_id = '903eb72b';
    IF v_task_refs <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'video AI task_algorithms still references electronic seal model';
    END IF;

    INSERT INTO electronic_seal_models (
        id, name, version, source, description, tags_json, classes_json,
        weights_path, file_size, file_size_mb, job_id, confidence,
        alert_interval, created_at, updated_at, raw_json
    )
    SELECT
        id, name, version, source, description, tags_json, classes_json,
        weights_path, file_size, file_size_mb, job_id, confidence,
        alert_interval, created_at, updated_at, raw_json
      FROM models WHERE id = '903eb72b'
    ON DUPLICATE KEY UPDATE
        name = VALUES(name), version = VALUES(version), source = VALUES(source),
        description = VALUES(description), tags_json = VALUES(tags_json),
        classes_json = VALUES(classes_json), weights_path = VALUES(weights_path),
        file_size = VALUES(file_size), file_size_mb = VALUES(file_size_mb),
        job_id = VALUES(job_id), confidence = VALUES(confidence),
        alert_interval = VALUES(alert_interval), created_at = VALUES(created_at),
        updated_at = VALUES(updated_at), raw_json = VALUES(raw_json);

    SELECT COUNT(*) INTO v_config_refs
      FROM electronic_seal_configs c
      LEFT JOIN electronic_seal_models m ON m.id = c.model_id
     WHERE c.model_id IS NOT NULL AND m.id IS NULL;
    IF v_config_refs <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'electronic seal config has an orphan model_id';
    END IF;

    SELECT COUNT(*) INTO v_fk_count
      FROM information_schema.KEY_COLUMN_USAGE
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'electronic_seal_configs'
       AND COLUMN_NAME = 'model_id'
       AND REFERENCED_TABLE_NAME = 'electronic_seal_models';
    IF v_fk_count = 0 THEN
        ALTER TABLE electronic_seal_configs
          ADD CONSTRAINT fk_electronic_seal_configs_model
          FOREIGN KEY (model_id) REFERENCES electronic_seal_models(id)
          ON DELETE RESTRICT;
    END IF;

    DELETE FROM models WHERE id = '903eb72b';
END$$
DELIMITER ;
CALL migrate_electronic_seal_model();
DROP PROCEDURE IF EXISTS migrate_electronic_seal_model;
```

The migration uses the exact 16 columns currently defined by `ModelMetaRecord`, in the same order on both sides; it does not use `SELECT *`, so future column additions cannot silently corrupt the migration. Before the source count check, query the destination count. When the source count is 0 and destination count is 1, treat the database as already migrated, skip the copy and source delete, and continue with the orphan-reference and foreign-key checks. Any other source/destination combination raises `SQLSTATE '45000'`.

- [ ] **Step 4: Add rerun/already-migrated checks to the test**

Assert the SQL includes a destination existence check before the source count failure, `INFORMATION_SCHEMA.KEY_COLUMN_USAGE`, and does not issue a source delete unless the destination row is verified. Keep the actual destructive operation opt-in to the real MySQL integration test.

- [ ] **Step 5: Run static and available migration tests**

```powershell
cd G:\docker\yolo-main\backend
pytest -q tests/test_electronic_seal_schema.py -k migration
```

Expected: PASS. Do not run the real MySQL migration until the container is stopped and the configured database endpoint has been confirmed.

- [ ] **Step 6: Commit migration**

```powershell
cd G:\docker\yolo-main
git add -f backend/migrations/20260810_electronic_seal_models.sql
git add backend/tests/test_electronic_seal_schema.py
git commit -m "feat: add electronic seal model migration"
```

## Task 3: Move default model resolution and Worker metadata reads

**Files:**
- Modify: `backend/src/electronic_seal/defaults.py`
- Modify: `backend/src/electronic_seal/manager.py`
- Modify: `backend/src/electronic_seal/inference.py`
- Modify: `backend/tests/test_electronic_seal_defaults.py`
- Modify: `backend/tests/test_electronic_seal_manager.py`
- Modify: `backend/tests/test_electronic_seal_inference.py`

- [ ] **Step 1: Replace registry-name tests with Store-name tests**

Use a fake Store exposing `find_model_by_name()` and assert `seed_defaults()` uses its returned `model_id`; assert missing and duplicate names raise `ValueError`. Keep registry only for weights resolution. Remove tests that create `*/model.json` solely to resolve the default model.

- [ ] **Step 2: Run defaults tests and confirm the old behavior fails**

```powershell
cd G:\docker\yolo-main\backend
pytest -q tests/test_electronic_seal_defaults.py
```

Expected before implementation: FAIL because `seed_defaults()` still requires registry metadata.

- [ ] **Step 3: Implement database-backed resolution**

Change `resolve_default_model_id()` to accept the electronic-seal Store (or a callable repository) and resolve via `find_model_by_name()`. Preserve the exact-one rule: no match or more than one match is an error. `seed_defaults()` calls that resolver and passes the returned ID into `ensure_default_configs()`.

- [ ] **Step 4: Inject Store metadata into inference**

Extend `YoloInferenceAdapter.__init__` with `metadata_loader: Callable[[str], dict[str, Any] | None] | None`. In `ElectronicSealChannelWorker`, pass `self.store.get_model`; if a referenced model is missing, raise/log `model not found in electronic_seal_models` before trying to load weights. `_default_metadata_loader()` remains only as a fallback for direct adapter callers that explicitly provide no repository, but Worker production construction must use the Store loader.

- [ ] **Step 5: Run Worker/defaults/inference tests**

```powershell
cd G:\docker\yolo-main\backend
pytest -q tests/test_electronic_seal_defaults.py tests/test_electronic_seal_inference.py tests/test_electronic_seal_manager.py
```

Expected: PASS; inference still resolves `/app/models/registry/903eb72b/weights/yolo11s.pt` and loads the same file.

- [ ] **Step 6: Commit runtime isolation**

```powershell
cd G:\docker\yolo-main
git add backend/src/electronic_seal/defaults.py backend/src/electronic_seal/manager.py backend/src/electronic_seal/inference.py backend/tests/test_electronic_seal_defaults.py backend/tests/test_electronic_seal_manager.py backend/tests/test_electronic_seal_inference.py
git commit -m "feat: load electronic seal models from isolated metadata"
```

## Task 4: Add independent API and switch frontend

**Files:**
- Modify: `backend/src/electronic_seal/manager.py`
- Modify: `backend/src/electronic_seal/api.py`
- Modify: `frontend/src/api/electronicSeal.ts`
- Modify: `frontend/src/pages/ElectronicSeal.vue`
- Modify: `backend/tests/test_electronic_seal_api.py`
- Modify: `frontend/tests/electronic-seal.test.mjs`

- [ ] **Step 1: Write API/frontend failing tests**

Add a fake Manager `list_models()` and `get_model()` to the API test and assert:

```python
assert client.get("/electronic-seal/models").json() == {
    "models": [{"model_id": "seal-1", "name": "电子封条"}]
}
assert client.get("/electronic-seal/models/missing").status_code == 404
```

Assert an unknown `model_id` configuration request returns 400. In the frontend source test assert `listElectronicSealModels` is imported/called and `listModels` from `@/api/models` is absent.

- [ ] **Step 2: Run tests to verify the API/frontend gap**

```powershell
cd G:\docker\yolo-main\backend
pytest -q tests/test_electronic_seal_api.py
cd G:\docker\yolo-main\frontend
node --test tests/electronic-seal.test.mjs
```

Expected: FAIL because the independent endpoints and frontend function do not exist.

- [ ] **Step 3: Implement Manager/API methods**

Add `ElectronicSealManager.list_models()` returning `{"models": self.store.list_models()}` and `get_model(model_id)` raising `ValueError` when absent. Add:

```python
@router.get("/models")
async def list_electronic_seal_models():
    return electronic_seal_manager.list_models()

@router.get("/models/{model_id}")
async def get_electronic_seal_model(model_id: str):
    try:
        return electronic_seal_manager.get_model(model_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
```

Before applying a config, validate non-empty `model_id` through Store/Manager and convert unknown IDs to HTTP 400. Keep all existing config and geometry validation unchanged.

- [ ] **Step 4: Implement frontend API and call-site switch**

Add `ElectronicSealModel` with the existing model response fields and:

```typescript
export const listElectronicSealModels = async () => {
  const { data } = await electronicSealApi.get('/electronic-seal/models')
  return data as { models: ElectronicSealModel[] }
}
```

Replace only the `listModels()` import and `loadData()` Promise entry in `ElectronicSeal.vue`; retain the same `models.value` shape so selectors and display behavior do not change.

- [ ] **Step 5: Run API/frontend tests and build**

```powershell
cd G:\docker\yolo-main\backend
pytest -q tests/test_electronic_seal_api.py
cd G:\docker\yolo-main\frontend
node --test tests/electronic-seal.test.mjs
npm.cmd run build
```

Expected: PASS and the frontend TypeScript/Vite build completes.

- [ ] **Step 6: Commit integration surface**

```powershell
cd G:\docker\yolo-main
git add backend/src/electronic_seal/manager.py backend/src/electronic_seal/api.py backend/tests/test_electronic_seal_api.py frontend/src/api/electronicSeal.ts frontend/src/pages/ElectronicSeal.vue frontend/tests/electronic-seal.test.mjs
git commit -m "feat: expose isolated electronic seal model API"
```

## Task 5: Deploy, migrate and verify against MySQL

**Files:**
- Modify only when required by the verification result: `docker/docker-compose.yml`, `backend/src/electronic_seal/settings.py`, or the existing missing-column migration.

- [ ] **Step 1: Run the full focused test suite**

```powershell
cd G:\docker\yolo-main\backend
pytest -q tests/test_electronic_seal_schema.py tests/test_electronic_seal_store.py tests/test_electronic_seal_defaults.py tests/test_electronic_seal_inference.py tests/test_electronic_seal_manager.py tests/test_electronic_seal_api.py tests/test_electronic_seal_isolation.py
cd G:\docker\yolo-main\frontend
node --test tests/electronic-seal.test.mjs
npm.cmd run build
```

Expected: all focused backend/frontend checks pass.

- [ ] **Step 2: Check database prerequisites before stopping the Worker**

Use the configured MySQL connection and inspect `electronic_seal_channel_profiles` for `runtime_owner`, `runtime_owner_id`, and `runtime_lease_until`. If any is absent, apply the existing approved migration for those fields first; do not start the new Worker against a partially migrated schema.

- [ ] **Step 3: Stop only the electronic-seal Worker and run the migration**

From `G:\docker\yolo-main`:

```powershell
docker compose -f docker/docker-compose.yml stop electronic-seal-worker
$sql = Get-Content -Raw backend/migrations/20260810_electronic_seal_models.sql
$sql | mysql --defaults-extra-file="$env:MYSQL_CLIENT_CONFIG" "$env:MYSQL_DATABASE"
```

Use the deployment's actual MySQL client/credentials or run the SQL from the MySQL host; never embed a guessed password in source control. The script must be run once, then run a second time to verify idempotence.

- [ ] **Step 4: Verify data and ownership invariants with SQL**

Run these queries and require the stated results:

```sql
SELECT id FROM electronic_seal_models WHERE id = '903eb72b';
SELECT COUNT(*) FROM models WHERE id = '903eb72b';
SELECT COUNT(*) FROM task_algorithms WHERE model_id = '903eb72b';
SELECT COUNT(*) FROM electronic_seal_configs WHERE model_id = '903eb72b';
SELECT CONSTRAINT_NAME, REFERENCED_TABLE_NAME
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'electronic_seal_configs'
   AND COLUMN_NAME = 'model_id';
```

Expected: destination count 1, source count 0, video task reference count 0, four existing electronic-seal config references, and the FK target `electronic_seal_models`.

- [ ] **Step 5: Start Worker and verify HTTP/runtime behavior**

```powershell
docker compose -f docker/docker-compose.yml up -d electronic-seal-worker
docker compose -f docker/docker-compose.yml logs --tail=200 electronic-seal-worker
```

Call `GET /electronic-seal/models`, `GET /electronic-seal/models/903eb72b`, `GET /models`, and the existing electronic-seal status/config endpoints. Require the isolated endpoint to return the model, video AI `/models` to omit it, existing config IDs to remain `903eb72b`, and Worker logs to show the shared registry weight path loaded without missing-model or missing-column errors.

- [ ] **Step 6: Record final verification and commit only required deployment fixes**

If Task 5 finds a missing schema field or compose path, add only the corresponding migration/config change, rerun the focused suite and container checks, inspect `git diff --name-only`, and stage only the migration/config file edited for that failure. Commit it separately with `git commit -m "fix: align electronic seal deployment schema"`.

## Self-review checklist

- [x] The design requirement is covered: independent table, copied model, source deletion, independent API, frontend switch, Worker/runtime isolation, and shared weights.
- [x] The four existing `electronic_seal_configs` references remain valid because `model_id` is unchanged.
- [x] Video AI model table, service and endpoint are not modified except that the migrated row disappears from their normal query result.
- [x] Migration refuses unsafe source deletion, has a duplicate-safe insert and an existing-migration path.
- [x] Tests cover schema, Store, defaults, inference, Manager, API and frontend.
- [x] No `TODO`, `TBD`, or unspecified implementation step is left in the plan.
