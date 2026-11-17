import api from './axios'

export const CAMERA_OCCLUSION = 'camera_occlusion' as const
export const CAMERA_VIEW_SHIFT = 'camera_view_shift' as const
export type CameraHealthAlgorithm = typeof CAMERA_OCCLUSION | typeof CAMERA_VIEW_SHIFT

export interface CameraHealthConfig {
  id?: string
  channel_id: string
  algorithm_type: CameraHealthAlgorithm
  enabled: boolean
  sample_interval_ms?: number
  warmup_seconds?: number
  alarm_hold_seconds?: number
  recovery_hold_seconds?: number
  alert_interval_seconds?: number
  need_snapshot?: boolean
  need_video_clip?: boolean
  threshold_config_json?: Record<string, number | string>
}

export interface CameraHealthState {
  id?: string
  channel_id: string
  algorithm_type: CameraHealthAlgorithm
  status: string
  current_score?: number
  last_metrics_json?: Record<string, number | string>
  last_event_id?: string | null
  updated_at?: string
}

export interface CameraHealthStatus {
  channel_id: string
  running: boolean
  source_type: string
  worker_status: string
  algorithms: CameraHealthState[]
  frame?: {
    status?: string
    sequence?: number
    last_frame_at?: number
  }
}

export interface CameraHealthEvent {
  id: string
  channel_id: string
  algorithm_type: CameraHealthAlgorithm
  event_type: string
  severity: string
  started_at?: string
  ended_at?: string | null
  duration_seconds?: number | null
  score?: number | null
  metrics_json?: Record<string, number | string>
  snapshot_path?: string | null
  status?: string
}

export const cameraHealthConfigPath = (channelId: string, algorithmType: CameraHealthAlgorithm) =>
  `/camera-health/channels/${channelId}/configs/${algorithmType}`

export const listCameraHealthConfigs = async (channelId?: string) => {
  const { data } = await api.get('/camera-health/configs', { params: channelId ? { channel_id: channelId } : undefined })
  return data as { items: CameraHealthConfig[] }
}

export const updateCameraHealthConfig = async (
  channelId: string,
  algorithmType: CameraHealthAlgorithm,
  payload: Partial<CameraHealthConfig>,
) => {
  const { data } = await api.put(cameraHealthConfigPath(channelId, algorithmType), payload)
  return data as CameraHealthConfig
}

export const recalibrateCameraHealth = async (channelId: string, algorithmType: CameraHealthAlgorithm) => {
  const { data } = await api.post(`/camera-health/channels/${channelId}/recalibrate`, null, {
    params: { algorithm_type: algorithmType },
  })
  return data
}

export const getCameraHealthStatus = async (channelId: string) => {
  const { data } = await api.get(`/camera-health/channels/${channelId}/status`)
  return data as CameraHealthStatus
}

export const listCameraHealthEvents = async (params?: {
  channel_id?: string
  algorithm_type?: CameraHealthAlgorithm
  page?: number
  page_size?: number
}) => {
  const { data } = await api.get('/camera-health/events', { params })
  return data as { items: CameraHealthEvent[]; total: number; page: number; page_size: number }
}
