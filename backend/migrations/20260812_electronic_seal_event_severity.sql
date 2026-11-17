-- Normalize electronic-seal event risk levels without changing lifecycle state.

UPDATE `electronic_seal_event_index`
SET `severity` = CASE `algorithm_type`
    WHEN 'truck_departure' THEN 'yellow'
    WHEN 'room_empty' THEN 'orange'
    WHEN 'camera_occlusion' THEN 'red'
    WHEN 'camera_view_shift' THEN 'red'
    ELSE `severity`
END
WHERE `algorithm_type` IN (
    'truck_departure',
    'room_empty',
    'camera_occlusion',
    'camera_view_shift'
);

UPDATE `electronic_seal_events`
SET `severity` = CASE `algorithm_type`
    WHEN 'truck_departure' THEN 'yellow'
    WHEN 'room_empty' THEN 'orange'
    WHEN 'camera_occlusion' THEN 'red'
    WHEN 'camera_view_shift' THEN 'red'
    ELSE `severity`
END
WHERE `algorithm_type` IN (
    'truck_departure',
    'room_empty',
    'camera_occlusion',
    'camera_view_shift'
);

UPDATE `electronic_seal_truck_departure_events`
SET `severity` = 'yellow';

UPDATE `electronic_seal_room_empty_events`
SET `severity` = 'orange';

UPDATE `electronic_seal_camera_occlusion_events`
SET `severity` = 'red';

UPDATE `electronic_seal_camera_view_shift_events`
SET `severity` = 'red';
