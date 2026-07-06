@echo off
chcp 65001 >nul
"C:\Program Files\MySQL\MySQL Server 9.4\bin\mysql.exe" --default-character-set=utf8mb4 -h 192.168.1.61 -P 3306 -uroot -proot < "G:\docker\yolo-main\.tmp\dbcopy\sp-ai.sql"
