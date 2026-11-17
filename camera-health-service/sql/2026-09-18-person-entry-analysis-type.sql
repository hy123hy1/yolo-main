-- 按人员通行方向修正 person_entry_events.analysis_type。

DELIMITER //

DROP PROCEDURE IF EXISTS migrate_person_entry_analysis_type//

CREATE PROCEDURE migrate_person_entry_analysis_type()
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'person_entry_events'
    ) THEN
        UPDATE person_entry_events
        SET analysis_type = CASE
            WHEN direction IN ('inside_to_outside', 'toward_first_edge') THEN '02'
            WHEN direction = 'outside_to_inside' THEN '01'
            ELSE analysis_type
        END
        WHERE direction IN ('outside_to_inside', 'inside_to_outside', 'toward_first_edge');

        ALTER TABLE person_entry_events
            MODIFY COLUMN analysis_type CHAR(2) NOT NULL DEFAULT '01'
            COMMENT '分析类型：01人员进入，02人员出去';
    END IF;
END//

CALL migrate_person_entry_analysis_type()//
DROP PROCEDURE migrate_person_entry_analysis_type//

DELIMITER ;
