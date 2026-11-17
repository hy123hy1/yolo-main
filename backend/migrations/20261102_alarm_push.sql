-- Video-AI alarm delivery only. No physical foreign keys or historical backfill.
CREATE TABLE IF NOT EXISTS alarm_push_targets (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    push_url VARCHAR(2048) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS alarm_push_jobs (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    target_id VARCHAR(64) NOT NULL,
    event_id VARCHAR(64) NOT NULL,
    payload_json JSON NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    attempts INT NOT NULL DEFAULT 0,
    next_retry_at DATETIME NULL,
    lease_until DATETIME NULL,
    claim_token VARCHAR(32) NULL,
    last_error TEXT NULL,
    response_status INT NULL,
    sent_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_alarm_push_target_event UNIQUE (target_id, event_id),
    INDEX idx_alarm_push_due (status, next_retry_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
