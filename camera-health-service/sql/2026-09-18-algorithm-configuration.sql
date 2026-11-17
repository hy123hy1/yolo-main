-- 视频 AI 算法定义与摄像头算法配置。

CREATE TABLE IF NOT EXISTS ai_algorithm_definitions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    algorithm_code VARCHAR(32) NOT NULL,
    algorithm_name VARCHAR(64) NOT NULL,
    pipeline_code VARCHAR(64) NOT NULL,
    default_interval FLOAT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_ai_algorithm_code (algorithm_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS channel_algorithm_configs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    channel_id VARCHAR(64) NOT NULL,
    algorithm_code VARCHAR(32) NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 0,
    analysis_interval FLOAT NULL,
    config_json TEXT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_channel_algorithm_config (channel_id, algorithm_code),
    KEY idx_channel_algorithm_enabled (channel_id, enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO ai_algorithm_definitions
    (algorithm_code, algorithm_name, pipeline_code, default_interval, created_at, updated_at)
VALUES
    ('person_count', '人员计数及进出统计', 'person_yolo11s_bytetrack', 1.0, NOW(), NOW()),
    ('tailgating', '尾随闯入', 'person_yolo11s_bytetrack', 0.2, NOW(), NOW()),
    ('loitering', '井口滞留', 'person_yolo11s_bytetrack', 0.5, NOW(), NOW())
ON DUPLICATE KEY UPDATE
    algorithm_name = VALUES(algorithm_name),
    pipeline_code = VALUES(pipeline_code),
    default_interval = VALUES(default_interval),
    updated_at = VALUES(updated_at);

CREATE TABLE IF NOT EXISTS person_behavior_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    channel_id VARCHAR(64) NOT NULL,
    algorithm_code VARCHAR(32) NOT NULL,
    behavior_code VARCHAR(64) NOT NULL,
    track_id VARCHAR(128) NULL,
    related_track_ids_json TEXT NULL,
    confidence FLOAT NULL,
    bbox_json TEXT NULL,
    metrics_json TEXT NULL,
    snapshot_path TEXT NULL,
    event_timestamp DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    KEY idx_person_behavior_events_channel_time (channel_id, event_timestamp),
    KEY idx_person_behavior_events_algorithm (algorithm_code, event_timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
