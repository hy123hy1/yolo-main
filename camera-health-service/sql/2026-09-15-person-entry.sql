-- 人员进入事件和通道虚拟线配置迁移。
-- 仅新增字段、表和索引，不删除或改写现有人员计数/健康数据。

DELIMITER //

DROP PROCEDURE IF EXISTS migrate_person_entry_schema//

CREATE PROCEDURE migrate_person_entry_schema()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'channels'
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'channels table does not exist';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
          AND column_name = 'person_entry_enabled'
    ) THEN
        ALTER TABLE channels
            ADD COLUMN person_entry_enabled TINYINT(1) NOT NULL DEFAULT 0
            COMMENT '是否启用人员进入统计：0关闭，1启用';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
          AND column_name = 'person_entry_line_config_json'
    ) THEN
        ALTER TABLE channels
            ADD COLUMN person_entry_line_config_json TEXT NULL
            COMMENT '人员进入虚拟线配置';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
          AND column_name = 'person_entry_region_config_json'
    ) THEN
        ALTER TABLE channels
            ADD COLUMN person_entry_region_config_json TEXT NULL
            COMMENT '人员进入区域配置';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
          AND column_name = 'person_analysis_direction'
    ) THEN
        ALTER TABLE channels
            ADD COLUMN person_analysis_direction VARCHAR(32) NULL DEFAULT 'outside_to_inside'
            COMMENT '人员进入方向：outside_to_inside 或 inside_to_outside';
    END IF;

    CREATE TABLE IF NOT EXISTS person_entry_events (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        channel_id VARCHAR(64) NOT NULL,
        analysis_type CHAR(2) NOT NULL DEFAULT '01' COMMENT '分析类型：01人员进入，02人员出去',
        analysis_case VARCHAR(4) NOT NULL DEFAULT '0000' COMMENT '当日累计入井人数',
        track_id VARCHAR(128) NULL,
        line_id VARCHAR(128) NOT NULL,
        direction VARCHAR(32) NOT NULL,
        confidence FLOAT NULL,
        bbox_json TEXT NULL,
        snapshot_path TEXT NULL,
        crossing_timestamp DATETIME NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        INDEX idx_person_entry_events_channel_time (channel_id, crossing_timestamp),
        INDEX idx_person_entry_events_direction_time (direction, crossing_timestamp)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'person_entry_events'
          AND column_name = 'id'
          AND column_type LIKE 'varchar%'
    ) THEN
        IF (SELECT COUNT(*) FROM person_entry_events) > 0 THEN
            ALTER TABLE person_entry_events
                ADD COLUMN numeric_id BIGINT UNSIGNED NULL;

            SET @entry_event_next_id = 0;
            UPDATE person_entry_events
            SET numeric_id = (@entry_event_next_id := @entry_event_next_id + 1)
            ORDER BY crossing_timestamp ASC, created_at ASC, id ASC;

            ALTER TABLE person_entry_events
                ADD UNIQUE KEY uk_person_entry_events_numeric_id (numeric_id);
            ALTER TABLE person_entry_events
                MODIFY COLUMN numeric_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT;
            ALTER TABLE person_entry_events
                DROP PRIMARY KEY,
                DROP COLUMN id,
                CHANGE COLUMN numeric_id id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                ADD PRIMARY KEY (id);
        ELSE
            ALTER TABLE person_entry_events
                MODIFY COLUMN id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT;
        END IF;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'person_entry_events'
          AND column_name = 'analysis_type'
    ) THEN
        ALTER TABLE person_entry_events
            ADD COLUMN analysis_type CHAR(2) NOT NULL DEFAULT '01'
            COMMENT '分析类型：01人员进入，02人员出去';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'person_entry_events'
          AND column_name = 'analysis_case'
    ) THEN
        ALTER TABLE person_entry_events
            ADD COLUMN analysis_case VARCHAR(4) NOT NULL DEFAULT '0000'
            COMMENT '当日累计入井人数';
    END IF;
END//

CALL migrate_person_entry_schema()//
DROP PROCEDURE migrate_person_entry_schema//

DELIMITER ;
