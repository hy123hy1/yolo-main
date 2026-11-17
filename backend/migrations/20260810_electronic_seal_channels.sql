-- Move electronic-seal-owned channels out of channels.
-- Back up the database before running this migration. The ALTER TABLE statements
-- are DDL and may commit independently on MySQL.

CREATE TABLE IF NOT EXISTS electronic_seal_channels LIKE channels;

DROP PROCEDURE IF EXISTS migrate_electronic_seal_channels;
DELIMITER $$

CREATE PROCEDURE migrate_electronic_seal_channels()
BEGIN
    DECLARE blocked_tasks BIGINT DEFAULT 0;
    DECLARE blocked_zones BIGINT DEFAULT 0;
    DECLARE blank_streams BIGINT DEFAULT 0;
    DECLARE duplicate_streams BIGINT DEFAULT 0;
    DECLARE existing_stream_conflicts BIGINT DEFAULT 0;
    DECLARE copy_mismatches BIGINT DEFAULT 0;
    DECLARE stream_index_count BIGINT DEFAULT 0;
    DECLARE fk_done BOOLEAN DEFAULT FALSE;
    DECLARE fk_table_name VARCHAR(128);
    DECLARE fk_constraint_name VARCHAR(128);

    DECLARE old_fk_cursor CURSOR FOR
        SELECT TABLE_NAME, CONSTRAINT_NAME
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE()
          AND REFERENCED_TABLE_NAME IN ('channels', 'electronic_seal_channels')
          AND REFERENCED_COLUMN_NAME = 'id'
          AND TABLE_NAME IN (
              'electronic_seal_channel_profiles',
              'electronic_seal_configs',
              'electronic_seal_baselines',
              'electronic_seal_states',
              'electronic_seal_events',
              'electronic_seal_event_index',
              'electronic_seal_truck_departure_events',
              'electronic_seal_room_empty_events',
              'electronic_seal_camera_occlusion_events',
              'electronic_seal_camera_view_shift_events',
              'electronic_seal_truck_counts'
          );
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET fk_done = TRUE;

    CREATE TEMPORARY TABLE IF NOT EXISTS _electronic_seal_channel_candidates (
        id VARCHAR(64) NOT NULL PRIMARY KEY,
        stream VARCHAR(255) NULL
    );
    DELETE FROM _electronic_seal_channel_candidates;

    INSERT INTO _electronic_seal_channel_candidates (id, stream)
    SELECT DISTINCT c.id, c.stream
    FROM channels c
    WHERE c.id IN (
        SELECT channel_id FROM electronic_seal_channel_profiles
        UNION SELECT channel_id FROM electronic_seal_configs
        UNION SELECT channel_id FROM electronic_seal_baselines
        UNION SELECT channel_id FROM electronic_seal_states
        UNION SELECT channel_id FROM electronic_seal_events
        UNION SELECT channel_id FROM electronic_seal_event_index
        UNION SELECT channel_id FROM electronic_seal_truck_departure_events
        UNION SELECT channel_id FROM electronic_seal_room_empty_events
        UNION SELECT channel_id FROM electronic_seal_camera_occlusion_events
        UNION SELECT channel_id FROM electronic_seal_camera_view_shift_events
        UNION SELECT channel_id FROM electronic_seal_truck_counts
    );

    SELECT COUNT(*) INTO blocked_tasks
    FROM tasks t
    INNER JOIN _electronic_seal_channel_candidates c ON c.id = t.channel_id;
    IF blocked_tasks > 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'electronic-seal channel migration blocked by video-AI tasks';
    END IF;

    SELECT COUNT(*) INTO blocked_zones
    FROM channel_zones z
    INNER JOIN _electronic_seal_channel_candidates c ON c.id = z.channel_id;
    IF blocked_zones > 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'electronic-seal channel migration blocked by video-AI zones';
    END IF;

    SELECT COUNT(*) INTO blank_streams
    FROM _electronic_seal_channel_candidates
    WHERE stream IS NULL OR TRIM(stream) = '';
    IF blank_streams > 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'electronic-seal channel migration requires non-empty stream values';
    END IF;

    SELECT COUNT(*) INTO duplicate_streams
    FROM (
        SELECT stream
        FROM _electronic_seal_channel_candidates
        GROUP BY stream
        HAVING COUNT(*) > 1
    ) duplicates;
    IF duplicate_streams > 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'electronic-seal channel migration found duplicate stream values';
    END IF;

    SELECT COUNT(*) INTO existing_stream_conflicts
    FROM _electronic_seal_channel_candidates candidates
    INNER JOIN electronic_seal_channels existing
        ON existing.stream = candidates.stream
       AND existing.id <> candidates.id;
    IF existing_stream_conflicts > 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'electronic-seal channel migration found stream ownership conflicts';
    END IF;

    SELECT COUNT(*) INTO stream_index_count
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'electronic_seal_channels'
      AND INDEX_NAME = 'uq_electronic_seal_channels_stream';
    IF stream_index_count = 0 THEN
        ALTER TABLE electronic_seal_channels
            ADD UNIQUE KEY uq_electronic_seal_channels_stream (stream);
    END IF;

    INSERT INTO electronic_seal_channels (
        id, device_id, media_node_id, name, type, source_url, app, stream,
        is_online, is_playing, record_mode, snapshot_url, snapshot_path,
        webrtc_api, hls, created_at, updated_at, raw_json
    )
    SELECT
        c.id, c.device_id, c.media_node_id, c.name, c.type, c.source_url,
        c.app, c.stream, c.is_online, c.is_playing, c.record_mode,
        c.snapshot_url, c.snapshot_path, c.webrtc_api, c.hls,
        c.created_at, c.updated_at, c.raw_json
    FROM channels c
    INNER JOIN _electronic_seal_channel_candidates candidates ON candidates.id = c.id
    ON DUPLICATE KEY UPDATE
        device_id = VALUES(device_id),
        media_node_id = VALUES(media_node_id),
        name = VALUES(name),
        type = VALUES(type),
        source_url = VALUES(source_url),
        app = VALUES(app),
        stream = VALUES(stream),
        is_online = VALUES(is_online),
        is_playing = VALUES(is_playing),
        record_mode = VALUES(record_mode),
        snapshot_url = VALUES(snapshot_url),
        snapshot_path = VALUES(snapshot_path),
        webrtc_api = VALUES(webrtc_api),
        hls = VALUES(hls),
        updated_at = VALUES(updated_at),
        raw_json = VALUES(raw_json);

    SELECT COUNT(*) INTO copy_mismatches
    FROM _electronic_seal_channel_candidates candidates
    LEFT JOIN electronic_seal_channels copied
        ON copied.id = candidates.id
       AND copied.stream = candidates.stream
    WHERE copied.id IS NULL;
    IF copy_mismatches > 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'electronic-seal channel migration copy verification failed';
    END IF;

    OPEN old_fk_cursor;
    old_fk_loop: LOOP
        FETCH old_fk_cursor INTO fk_table_name, fk_constraint_name;
        IF fk_done THEN
            LEAVE old_fk_loop;
        END IF;
        SET @drop_fk_sql = CONCAT(
            'ALTER TABLE `', fk_table_name, '` DROP FOREIGN KEY `', fk_constraint_name, '`'
        );
        PREPARE drop_fk_statement FROM @drop_fk_sql;
        EXECUTE drop_fk_statement;
        DEALLOCATE PREPARE drop_fk_statement;
    END LOOP;
    CLOSE old_fk_cursor;

    ALTER TABLE electronic_seal_channel_profiles
        ADD CONSTRAINT fk_electronic_seal_profile_channel
        FOREIGN KEY (channel_id) REFERENCES electronic_seal_channels(id) ON DELETE CASCADE;
    ALTER TABLE electronic_seal_configs
        ADD CONSTRAINT fk_electronic_seal_config_channel
        FOREIGN KEY (channel_id) REFERENCES electronic_seal_channels(id) ON DELETE CASCADE;
    ALTER TABLE electronic_seal_baselines
        ADD CONSTRAINT fk_electronic_seal_baseline_channel
        FOREIGN KEY (channel_id) REFERENCES electronic_seal_channels(id) ON DELETE CASCADE;
    ALTER TABLE electronic_seal_states
        ADD CONSTRAINT fk_electronic_seal_state_channel
        FOREIGN KEY (channel_id) REFERENCES electronic_seal_channels(id) ON DELETE CASCADE;
    ALTER TABLE electronic_seal_events
        ADD CONSTRAINT fk_electronic_seal_event_channel
        FOREIGN KEY (channel_id) REFERENCES electronic_seal_channels(id) ON DELETE CASCADE;
    ALTER TABLE electronic_seal_event_index
        ADD CONSTRAINT fk_electronic_seal_event_index_channel
        FOREIGN KEY (channel_id) REFERENCES electronic_seal_channels(id) ON DELETE CASCADE;
    ALTER TABLE electronic_seal_truck_departure_events
        ADD CONSTRAINT fk_electronic_seal_truck_departure_channel
        FOREIGN KEY (channel_id) REFERENCES electronic_seal_channels(id) ON DELETE CASCADE;
    ALTER TABLE electronic_seal_room_empty_events
        ADD CONSTRAINT fk_electronic_seal_room_empty_channel
        FOREIGN KEY (channel_id) REFERENCES electronic_seal_channels(id) ON DELETE CASCADE;
    ALTER TABLE electronic_seal_camera_occlusion_events
        ADD CONSTRAINT fk_electronic_seal_camera_occlusion_channel
        FOREIGN KEY (channel_id) REFERENCES electronic_seal_channels(id) ON DELETE CASCADE;
    ALTER TABLE electronic_seal_camera_view_shift_events
        ADD CONSTRAINT fk_electronic_seal_camera_view_shift_channel
        FOREIGN KEY (channel_id) REFERENCES electronic_seal_channels(id) ON DELETE CASCADE;
    ALTER TABLE electronic_seal_truck_counts
        ADD CONSTRAINT fk_electronic_seal_truck_count_channel
        FOREIGN KEY (channel_id) REFERENCES electronic_seal_channels(id) ON DELETE CASCADE;

    DELETE FROM channels
    WHERE id IN (SELECT id FROM _electronic_seal_channel_candidates);

    DROP TEMPORARY TABLE _electronic_seal_channel_candidates;
END$$

DELIMITER ;
CALL migrate_electronic_seal_channels();
DROP PROCEDURE IF EXISTS migrate_electronic_seal_channels;
