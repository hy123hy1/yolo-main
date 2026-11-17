import axios from 'axios'

export const electronicSealApi = axios.create({
  baseURL: import.meta.env.VITE_ELECTRONIC_SEAL_API_BASE || '/electronic-seal-api',
  timeout: 30000,
})

export const ELECTRONIC_SEAL_ALGORITHMS = [
  { key: 'truck_departure', label: '车辆驶出' },
  { key: 'room_empty', label: '人员离岗/房间无人' },
  { key: 'camera_occlusion', label: '摄像头遮挡' },
  { key: 'camera_view_shift', label: '摄像头扭转角度' },
] as const

export type ElectronicSealAlgorithm = typeof ELECTRONIC_SEAL_ALGORITHMS[number]['key']

export interface ElectronicSealConfig {
  id?: string
  channel_id: string
  algorithm_type: ElectronicSealAlgorithm
  enabled: boolean
  model_id?: string | null
  sample_interval_ms?: number
  camera_health_interval_seconds?: number
  alarm_hold_seconds?: number
  recovery_hold_seconds?: number
  need_snapshot?: boolean
  need_video_clip?: boolean
  line_config_json?: Record<string, unknown>
  region_config_json?: Record<string, unknown>
  threshold_config_json?: Record<string, unknown>
}

export interface ElectronicSealStatus {
  channel_id: string
  running: boolean
  worker_status: string
  source_url?: string
  frame?: { status?: string; last_frame_timestamp?: number; last_error?: string | null }
  algorithms?: string[]
  role?: string | null
  bound?: boolean
  configs?: Array<ElectronicSealConfig & { state?: { status?: string } | null }>
}

export interface ElectronicSealChannel {
  id: string
  name?: string | null
  type?: string | null
  source_url?: string | null
  app?: string | null
  stream?: string | null
  is_online?: boolean
  is_playing?: boolean
}

export interface ElectronicSealProfile {
  channel_id: string
  role: 'vehicle_entry' | 'dispatch_room'
  bound: boolean
  profile_version?: string
  initialized_at?: string | null
}

export interface ElectronicSealModel {
  model_id: string
  name?: string | null
  version?: string | null
  source?: string | null
  description?: string | null
  tags?: string[]
  classes?: string[]
  weights_path?: string | null
  file_size?: number | null
  file_size_mb?: number | null
  job_id?: string | null
  confidence?: number | null
  alert_interval?: number | null
  created_at?: string | null
  updated_at?: string | null
}

export interface ElectronicSealEvent {
  id: string
  channel_id: string
  algorithm_type: ElectronicSealAlgorithm
  event_type: string
  severity: string | null
  started_at?: string
  ended_at?: string | null
  duration_seconds?: number | null
  score?: number | null
  status?: string
  upload_status?: string
  upload_job_id?: string | null
  upload_target?: string | null
  snapshot_url?: string | null
  video_url?: string | null
  evidence_snapshot_path?: string | null
  evidence_video_path?: string | null
  metrics_json?: Record<string, unknown>
}

export const listElectronicSealConfigs = async (channelId?: string) => {
  const { data } = await electronicSealApi.get('/electronic-seal/configs', {
    params: channelId ? { channel_id: channelId } : undefined,
  })
  return data as { items: ElectronicSealConfig[]; profiles?: ElectronicSealProfile[] }
}

export const listElectronicSealChannels = async () => {
  const { data } = await electronicSealApi.get('/electronic-seal/channels')
  return data as { items: ElectronicSealChannel[] }
}

export const startElectronicSealChannelPlay = async (channelId: string) => {
  const { data } = await electronicSealApi.post(`/electronic-seal/channels/${channelId}/play`)
  return data
}

export const listElectronicSealProfiles = async (channelId?: string) => {
  const { data } = await electronicSealApi.get('/electronic-seal/profiles', {
    params: channelId ? { channel_id: channelId } : undefined,
  })
  return data as { items: ElectronicSealProfile[] }
}

export const listElectronicSealModels = async () => {
  const { data } = await electronicSealApi.get('/electronic-seal/models')
  return data as { models: ElectronicSealModel[] }
}

export const getElectronicSealOverview = async () => {
  const { data } = await electronicSealApi.get('/electronic-seal/overview')
  return data
}

export const updateElectronicSealConfig = async (
  channelId: string,
  algorithmType: ElectronicSealAlgorithm,
  payload: Partial<ElectronicSealConfig>,
) => {
  const { data } = await electronicSealApi.put(
    `/electronic-seal/channels/${channelId}/configs/${algorithmType}`,
    payload,
  )
  return data as ElectronicSealConfig
}

export const getElectronicSealStatus = async (channelId: string) => {
  const { data } = await electronicSealApi.get(`/electronic-seal/channels/${channelId}/status`)
  return data as ElectronicSealStatus
}

export const recalibrateElectronicSeal = async (channelId: string, algorithmType: ElectronicSealAlgorithm) => {
  const { data } = await electronicSealApi.post(
    `/electronic-seal/channels/${channelId}/recalibrate`,
    null,
    { params: { algorithm_type: algorithmType } },
  )
  return data
}

export const listElectronicSealEvents = async (params?: {
  channel_id?: string
  algorithm_type?: ElectronicSealAlgorithm
  page?: number
  page_size?: number
}) => {
  const { data } = await electronicSealApi.get('/electronic-seal/events', { params })
  return data as { items: ElectronicSealEvent[]; total: number; page: number; page_size: number }
}

export const retryElectronicSealUpload = async (jobId: string) => {
  const { data } = await electronicSealApi.post(`/electronic-seal/uploads/${jobId}/retry`)
  return data
}

export const electronicSealEvidenceUrl = (
  eventIdOrUrl: string | null | undefined,
  kind?: 'snapshot' | 'video',
) => {
  if (!eventIdOrUrl) return ''
  const path = kind
    ? `/electronic-seal/events/${eventIdOrUrl}/evidence?kind=${kind}`
    : eventIdOrUrl
  return `${electronicSealApi.defaults.baseURL}${path.startsWith('/') ? path : `/${path}`}`
}
