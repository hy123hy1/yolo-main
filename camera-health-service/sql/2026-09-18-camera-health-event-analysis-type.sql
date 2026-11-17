-- 为质量检测报警事件增加分析类型，已有数据统一使用默认值 03。

DELIMITER //

DROP PROCEDURE IF EXISTS migrate_camera_health_event_analysis_type//

CREATE PROCEDURE migrate_camera_health_event_analysis_type()
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'camera_health_events'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'camera_health_events'
          AND column_name = 'analysis_type'
    ) THEN
        ALTER TABLE camera_health_events
            ADD COLUMN analysis_type VARCHAR(2) NOT NULL DEFAULT '03'
            COMMENT '分析类型：03视频质量检测'
            AFTER algorithm_type;
    END IF;
END//

CALL migrate_camera_health_event_analysis_type()//
DROP PROCEDURE migrate_camera_health_event_analysis_type//

DELIMITER ;
