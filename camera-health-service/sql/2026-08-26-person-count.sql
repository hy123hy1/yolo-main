-- 人员计数数据库迁移。
-- 执行前确认当前连接用户有 ALTER、CREATE INDEX 和 CREATE ROUTINE 权限。
-- 该脚本只修改 camera-health-service 使用的表，不会自动执行应用代码。

DELIMITER //

DROP PROCEDURE IF EXISTS migrate_person_count_schema//

CREATE PROCEDURE migrate_person_count_schema()
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'channels table does not exist';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = 'person_count'
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'person_count table does not exist';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
          AND column_name = 'camera_code'
    ) THEN
        ALTER TABLE channels
            ADD COLUMN camera_code CHAR(20) NULL
            COMMENT '监管平台20位摄像仪编码';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
          AND column_name = 'person_count_enabled'
    ) THEN
        ALTER TABLE channels
            ADD COLUMN person_count_enabled TINYINT(1) NOT NULL DEFAULT 0
            COMMENT '是否启用人员计数：0关闭，1启用';
    END IF;

    ALTER TABLE person_count
        MODIFY COLUMN analysis_type CHAR(2) NOT NULL DEFAULT '01'
            COMMENT '分析类型：01人员计数',
        MODIFY COLUMN analysis_case VARCHAR(4) NOT NULL
            COMMENT '当前画面人数，左侧补零到4位';

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'person_count'
          AND index_name = 'idx_person_count_channel_created'
    ) THEN
        CREATE INDEX idx_person_count_channel_created
            ON person_count (channel_id, created_at);
    END IF;
END//

CALL migrate_person_count_schema()//
DROP PROCEDURE migrate_person_count_schema//

DELIMITER ;
