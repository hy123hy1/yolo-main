# Metadata DB Modes

The backend metadata store now follows a DSN-driven mode similar to `owl-main`.

Primary config entry:

- `backend/configs/config.toml`

Environment variables still work and have higher priority than values loaded
from `config.toml`.

## Default mode

If `METADATA_DATABASE_URL` is empty, the backend uses local SQLite:

```text
/app/data/metadata_v2.db
```

This is the default development mode.

## Optional MySQL

Example:

```text
mysql+pymysql://yolo:yolo123456@127.0.0.1:3306/yolo_meta?charset=utf8mb4
```

## Optional PostgreSQL

Example:

```text
postgresql+psycopg2://postgres:123456@127.0.0.1:5432/yolo_meta
```

## Notes

- Database service is no longer required in `docker-compose.yml`
- Backend auto-creates metadata tables at startup
- Metadata JSON files are still synchronized into the database on startup
