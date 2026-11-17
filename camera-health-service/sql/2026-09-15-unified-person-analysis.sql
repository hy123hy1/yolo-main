-- Unified personnel-analysis configuration migration.
-- Existing rows remain nullable so legacy count/entry flags can be used until
-- the unified configuration is explicitly saved.

DELIMITER //

DROP PROCEDURE IF EXISTS migrate_unified_person_analysis//

CREATE PROCEDURE migrate_unified_person_analysis()
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
          AND column_name = 'person_analysis_enabled'
    ) THEN
        ALTER TABLE channels
            ADD COLUMN person_analysis_enabled TINYINT(1) NULL DEFAULT NULL
            AFTER person_entry_region_config_json;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
          AND column_name = 'person_analysis_mode'
    ) THEN
        ALTER TABLE channels
            ADD COLUMN person_analysis_mode VARCHAR(32) NULL DEFAULT NULL
            AFTER person_analysis_enabled;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
          AND column_name = 'person_analysis_line_config_json'
    ) THEN
        ALTER TABLE channels
            ADD COLUMN person_analysis_line_config_json TEXT NULL
            AFTER person_analysis_mode;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
          AND column_name = 'person_analysis_region_config_json'
    ) THEN
        ALTER TABLE channels
            ADD COLUMN person_analysis_region_config_json TEXT NULL
            AFTER person_analysis_line_config_json;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'channels'
          AND column_name = 'person_analysis_direction'
    ) THEN
        ALTER TABLE channels
            ADD COLUMN person_analysis_direction VARCHAR(32) NULL DEFAULT 'outside_to_inside'
            AFTER person_analysis_mode;
    END IF;
END//

CALL migrate_unified_person_analysis()//
DROP PROCEDURE migrate_unified_person_analysis//

DELIMITER ;
