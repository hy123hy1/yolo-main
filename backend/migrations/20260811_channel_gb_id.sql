-- Add a normalized WVP gbId field to both channel ownership tables.
-- Back up the database before running this migration.

DROP PROCEDURE IF EXISTS add_channel_gb_id;
DELIMITER $$

CREATE PROCEDURE add_channel_gb_id()
BEGIN
    DECLARE channels_column_count BIGINT DEFAULT 0;
    DECLARE seal_channels_column_count BIGINT DEFAULT 0;
    DECLARE channels_index_count BIGINT DEFAULT 0;
    DECLARE seal_channels_index_count BIGINT DEFAULT 0;

    SELECT COUNT(*) INTO channels_column_count
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'channels'
      AND COLUMN_NAME = 'gb_id';
    IF channels_column_count = 0 THEN
        ALTER TABLE channels ADD COLUMN gb_id VARCHAR(128) NULL;
    END IF;

    SELECT COUNT(*) INTO seal_channels_column_count
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'electronic_seal_channels'
      AND COLUMN_NAME = 'gb_id';
    IF seal_channels_column_count = 0 THEN
        ALTER TABLE electronic_seal_channels ADD COLUMN gb_id VARCHAR(128) NULL;
    END IF;

    SELECT COUNT(*) INTO channels_index_count
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'channels'
      AND COLUMN_NAME = 'gb_id';
    IF channels_index_count = 0 THEN
        ALTER TABLE channels ADD KEY idx_channels_gb_id (gb_id);
    END IF;

    SELECT COUNT(*) INTO seal_channels_index_count
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'electronic_seal_channels'
      AND COLUMN_NAME = 'gb_id';
    IF seal_channels_index_count = 0 THEN
        ALTER TABLE electronic_seal_channels
            ADD KEY idx_electronic_seal_channels_gb_id (gb_id);
    END IF;

    UPDATE channels
    SET gb_id = COALESCE(
        NULLIF(gb_id, ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(raw_json, '$.ext.upstream.channel_db_id')), '')
    )
    WHERE (gb_id IS NULL OR gb_id = '')
      AND raw_json IS NOT NULL;

    UPDATE electronic_seal_channels
    SET gb_id = COALESCE(
        NULLIF(gb_id, ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(raw_json, '$.ext.upstream.channel_db_id')), '')
    )
    WHERE (gb_id IS NULL OR gb_id = '')
      AND raw_json IS NOT NULL;
END$$

DELIMITER ;
CALL add_channel_gb_id();
DROP PROCEDURE IF EXISTS add_channel_gb_id;
