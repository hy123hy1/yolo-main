-- Existing deployments created before loitering need a metrics JSON column.

SET @column_exists = (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'person_behavior_events'
      AND COLUMN_NAME = 'metrics_json'
);

SET @add_metrics_sql = IF(
    @column_exists = 0,
    'ALTER TABLE person_behavior_events ADD COLUMN metrics_json TEXT NULL AFTER bbox_json',
    'SELECT 1'
);

PREPARE add_metrics_statement FROM @add_metrics_sql;
EXECUTE add_metrics_statement;
DEALLOCATE PREPARE add_metrics_statement;
