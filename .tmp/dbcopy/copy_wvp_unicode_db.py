import pymysql


SOURCE = {
    "host": "192.168.1.200",
    "port": 3307,
    "user": "root",
    "password": "root",
    "charset": "utf8mb4",
    "autocommit": True,
}

TARGET = {
    "host": "192.168.1.61",
    "port": 3306,
    "user": "root",
    "password": "root",
    "charset": "utf8mb4",
    "autocommit": True,
}

DB_NAME = "wvp273数据库统合"


def quote_name(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def main() -> None:
    source = pymysql.connect(**SOURCE)
    target = pymysql.connect(**TARGET)
    try:
        with source.cursor() as cur:
            cur.execute(
                """
                SELECT table_name, table_type
                FROM information_schema.tables
                WHERE table_schema=%s
                ORDER BY table_name
                """,
                (DB_NAME,),
            )
            tables = cur.fetchall()

        with target.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {quote_name(DB_NAME)}")
            cur.execute(
                f"CREATE DATABASE {quote_name(DB_NAME)} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )

        copied = 0
        for table_name, table_type in tables:
            if table_type != "BASE TABLE":
                continue
            src_table = f"{quote_name(DB_NAME)}.{quote_name(table_name)}"
            dst_table = f"{quote_name(DB_NAME)}.{quote_name(table_name)}"
            with source.cursor() as s_cur, target.cursor() as t_cur:
                s_cur.execute(f"SHOW CREATE TABLE {src_table}")
                _, create_sql = s_cur.fetchone()
                t_cur.execute(f"USE {quote_name(DB_NAME)}")
                t_cur.execute(create_sql)

                s_cur.execute(f"SELECT * FROM {src_table}")
                rows = s_cur.fetchall()
                if rows:
                    placeholders = ", ".join(["%s"] * len(rows[0]))
                    insert_sql = f"INSERT INTO {dst_table} VALUES ({placeholders})"
                    t_cur.executemany(insert_sql, rows)
            copied += 1
            print(f"copied table: {table_name}")

        print(f"done: database={DB_NAME} tables={copied}")
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    main()
