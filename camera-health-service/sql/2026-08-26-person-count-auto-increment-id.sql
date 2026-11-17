-- 将 person_count.id 从 UUID 字符串迁移为自增数字。
--
-- 已有业务字段和记录会保留，但记录 ID 会重新编号；依赖旧 UUID 的历史 URL 将失效。
-- 执行前应备份 person_count，并确认没有其他表外键引用 person_count.id。
-- 应先停止 camera-health-service，避免迁移期间继续写入。

DELIMITER //

DROP PROCEDURE IF EXISTS migrate_person_count_auto_increment_id//

CREATE PROCEDURE migrate_person_count_auto_increment_id()
BEGIN
    DECLARE current_type VARCHAR(255);
    DECLARE current_extra VARCHAR(255);
    DECLARE reference_count BIGINT DEFAULT 0;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = 'person_count'
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'person_count table does not exist';
    END IF;

    SELECT column_type, extra
      INTO current_type, current_extra
      FROM information_schema.columns
     WHERE table_schema = DATABASE()
       AND table_name = 'person_count'
       AND column_name = 'id';

    SELECT COUNT(*)
      INTO reference_count
      FROM information_schema.key_column_usage
     WHERE referenced_table_schema = DATABASE()
       AND referenced_table_name = 'person_count'
       AND referenced_column_name = 'id';

    IF reference_count > 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'person_count.id is referenced by foreign keys';
    END IF;

    IF current_type NOT LIKE 'bigint%unsigned%'
       OR current_extra <> 'auto_increment' THEN
        ALTER TABLE person_count
            ADD COLUMN person_count_numeric_id BIGINT UNSIGNED NULL,
            ADD UNIQUE INDEX uk_person_count_numeric_id (person_count_numeric_id);

        SET @person_count_next_id := 0;
        UPDATE person_count
           SET person_count_numeric_id = (@person_count_next_id := @person_count_next_id + 1)
         ORDER BY created_at ASC, id ASC;

        ALTER TABLE person_count
            MODIFY COLUMN person_count_numeric_id
                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT;

        ALTER TABLE person_count
            DROP PRIMARY KEY,
            DROP COLUMN id,
            CHANGE COLUMN person_count_numeric_id id
                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            ADD PRIMARY KEY (id);

        ALTER TABLE person_count
            DROP INDEX uk_person_count_numeric_id;
    END IF;

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

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'person_count'
          AND index_name = 'idx_person_count_created_at'
    ) THEN
        CREATE INDEX idx_person_count_created_at
            ON person_count (created_at);
    END IF;
END//

CALL migrate_person_count_auto_increment_id()//
DROP PROCEDURE migrate_person_count_auto_increment_id//

DELIMITER ;
