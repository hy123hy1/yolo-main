@echo off
chcp 65001 >nul
"C:\Program Files\MySQL\MySQL Server 9.4\bin\mysqldump.exe" --default-character-set=utf8mb4 --single-transaction --routines --events --triggers --set-gtid-purged=OFF --no-tablespaces -h 192.168.1.200 -P 3307 -uroot -proot "wvp273数据库统合" > "G:\docker\yolo-main\.tmp\dbcopy\wvp273.sql"