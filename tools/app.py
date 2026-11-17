#!/usr/bin/env python3
"""Create next-day aks_dzft_* daily tables in PostgreSQL.

By default this creates tomorrow's tables based on today's date.
Example:
  today=20260713 -> create ..._20260714 tables
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from typing import Iterable


DEFAULT_DB = {
    "dbname": os.getenv("AKS_DB_NAME", "aks"),
    "user": os.getenv("AKS_DB_USER", "system"),
    "password": os.getenv("AKS_DB_PASSWORD", "Password@2025"),
    "host": os.getenv("AKS_DB_HOST", "10.13.11.3"),
    "port": int(os.getenv("AKS_DB_PORT", "54321")),
}

# Base table names without the _YYYYMMDD suffix.
# Column types are inferred from sample rows in the provided screenshots.
TABLE_DEFS = {
    "aks_dzft_safety_helmet_detection": """
        id BIGSERIAL PRIMARY KEY,
        mine_code VARCHAR(64),
        county_code VARCHAR(64),
        data_time TIMESTAMP,
        camera_id VARCHAR(64),
        count INTEGER,
        sxt_ip VARCHAR(64),
        lxj_ip VARCHAR(64),
        sxt_id VARCHAR(64)
    """,
    "aks_dzft_safety_helmet_detection_info": """
        id BIGSERIAL PRIMARY KEY,
        safety_helmet_detection_id BIGINT,
        confidence DOUBLE PRECISION,
        bbox TEXT,
        status VARCHAR(64),
        label VARCHAR(128),
        warning_image TEXT,
        mine_code VARCHAR(64),
        county_code VARCHAR(64),
        warning_video TEXT
    """,
    "aks_dzft_intrusion_detection": """
        id BIGSERIAL PRIMARY KEY,
        mine_code VARCHAR(64),
        county_code VARCHAR(64),
        data_time TIMESTAMP,
        camera_id VARCHAR(64),
        count INTEGER,
        sxt_ip VARCHAR(64),
        lxj_ip VARCHAR(64)
    """,
    "aks_dzft_intrusion_detection_info": """
        id BIGSERIAL PRIMARY KEY,
        intrusion_detection_id BIGINT,
        confidence DOUBLE PRECISION,
        bbox TEXT,
        status VARCHAR(64),
        label VARCHAR(128),
        warning_image TEXT,
        mine_code VARCHAR(64),
        county_code VARCHAR(64),
        warning_video TEXT
    """,
    "aks_dzft_noise_and_blur": """
        id BIGSERIAL PRIMARY KEY,
        mine_code VARCHAR(64),
        county_code VARCHAR(64),
        sxt_ip VARCHAR(64),
        data_time TIMESTAMP,
        file_path TEXT
    """,
    "aks_dzft_chaoyuan": """
        id BIGSERIAL PRIMARY KEY,
        mine_code VARCHAR(64),
        county_code VARCHAR(64),
        sxt_ip VARCHAR(64),
        data_time TIMESTAMP,
        img_path TEXT,
        video_path TEXT,
        count INTEGER,
        max_people INTEGER
    """,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create aks_dzft daily tables for a target date (default: tomorrow)."
    )
    parser.add_argument(
        "--date",
        dest="target_date",
        help="Target table date as YYYYMMDD. Default is tomorrow.",
    )
    parser.add_argument("--host", default=DEFAULT_DB["host"], help="DB host")
    parser.add_argument("--port", type=int, default=DEFAULT_DB["port"], help="DB port")
    parser.add_argument("--dbname", default=DEFAULT_DB["dbname"], help="DB name")
    parser.add_argument("--user", default=DEFAULT_DB["user"], help="DB user")
    parser.add_argument(
        "--password",
        default=DEFAULT_DB["password"],
        help="DB password (prefer AKS_DB_PASSWORD env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print SQL, do not execute.",
    )
    return parser.parse_args()


def resolve_target_date(raw: str | None) -> str:
    if raw:
        try:
            datetime.strptime(raw, "%Y%m%d")
        except ValueError as exc:
            raise SystemExit(f"Invalid --date value: {raw}. Expected YYYYMMDD.") from exc
        return raw
    return (date.today() + timedelta(days=1)).strftime("%Y%m%d")


def table_names(day: str) -> list[str]:
    return [f"{base}_{day}" for base in TABLE_DEFS]


def render_create_sql(table_name: str, columns_sql: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {table_name} (\n"
        f"{columns_sql.rstrip()}\n"
        f");"
    )


def iter_create_sql(day: str) -> Iterable[tuple[str, str]]:
    for base, columns_sql in TABLE_DEFS.items():
        name = f"{base}_{day}"
        yield name, render_create_sql(name, columns_sql)


def import_psycopg2():
    try:
        import psycopg2  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Missing dependency: psycopg2\n"
            "Install with: pip install psycopg2-binary"
        ) from exc
    return psycopg2


def create_tables(conn, day: str) -> list[str]:
    created: list[str] = []
    with conn.cursor() as cur:
        for name, statement in iter_create_sql(day):
            cur.execute(statement)
            created.append(name)
    conn.commit()
    return created


def main() -> int:
    args = parse_args()
    day = resolve_target_date(args.target_date)
    names = table_names(day)

    print(f"Target date: {day}")
    print("Tables:")
    for name in names:
        print(f"  - {name}")

    if args.dry_run:
        print("\n[DRY RUN] SQL only:")
        for _, statement in iter_create_sql(day):
            print()
            print(statement)
        return 0

    psycopg2 = import_psycopg2()
    print(f"\nConnecting to {args.host}:{args.port}/{args.dbname} ...")
    conn = psycopg2.connect(
        dbname=args.dbname,
        user=args.user,
        password=args.password,
        host=args.host,
        port=args.port,
    )
    try:
        created = create_tables(conn, day)
        print("Done. Ensured tables exist:")
        for name in created:
            print(f"  OK  {name}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
