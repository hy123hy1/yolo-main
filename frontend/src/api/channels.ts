import api from './axios'

export const listChannels = async () => {
  const { data } = await api.get('/channels')
  return data
}

export const listAvailableTaskChannels = async () => {
  const { data } = await api.get('/channels/available-for-task')
  return data
}

export const syncWvpChannels = async (params?: { page?: number; count?: number; query?: string }) => {
  const { data } = await api.post('/channels/sync-wvp', null, {
    params,
  })
  return data
}

export interface ChannelPayload {
  device_id?: string
  name: string
  type: string
  source_url: string
  app?: string
  stream?: string
  media_node_id?: string
  record_mode?: string
}

export const createChannel = async (payload: ChannelPayload) => {
  const { data } = await api.post('/channels', payload)
  return data
}

export const updateChannel = async (channelId: string, payload: Partial<ChannelPayload>) => {
  const { data } = await api.put(`/channels/${channelId}`, payload)
  return data
}

export const deleteChannel = async (channelId: string) => {
  const { data } = await api.delete(`/channels/${channelId}`)
  return data
}

export const getChannelPlayUrl = async (
  channelId: string,
  mode: 'auto' | 'channel' | 'zone' = 'auto',
  compact = false,
) => {
  const { data } = await api.get(`/channels/${channelId}/play-url`, {
    params: { mode, compact },
  })
  return data
}

export interface ZonePreviewStartPayload {
  task_id?: string
  owner_task_id?: string
  zone_source?: string
  zones?: Array<Record<string, any>>
}

export const startChannelZonePreview = async (channelId: string, payload?: ZonePreviewStartPayload) => {
  const { data } = await api.post(`/channels/${channelId}/zone-preview/start`, payload || undefined)
  return data
}

export const stopChannelZonePreview = async (channelId: string) => {
  const { data } = await api.post(`/channels/${channelId}/zone-preview/stop`)
  return data
}

export const getChannelZonePreviewStatus = async (channelId: string) => {
  const { data } = await api.get(`/channels/${channelId}/zone-preview/status`)
  return data
}

export const getChannelFenceBrowserPlayUrl = async (channelId: string) => {
  const { data } = await api.get(`/channels/${channelId}/fence/browser-play-url`)
  return data
}

export const debugChannelAIOnce = async (channelId: string) => {
  const { data } = await api.post(`/channels/${channelId}/ai/debug-once`)
  return data
}

export const getChannelAIStatus = async (channelId: string) => {
  const { data } = await api.get(`/channels/${channelId}/ai-status`)
  return data
}

export interface ChannelAIConfigPayload {
  enabled?: boolean
  model_id?: string
  labels?: string[]
  conf?: number
  frame_interval?: number
  zone_ids?: string[]
  save_snapshot?: boolean
  models?: Array<{
    model_id: string
    enabled?: boolean
    labels?: string[]
    conf?: number
    frame_interval?: number
    zone_ids?: string[]
    save_snapshot?: boolean
  }>
}

export interface ChannelZonePayload {
  name: string
  color?: string
  labels?: string[]
  coordinates: number[][]
}

export interface UpdateChannelZonePayload {
  name?: string
  color?: string
  labels?: string[]
  coordinates?: number[][]
}

export const updateChannelAIConfig = async (channelId: string, payload: ChannelAIConfigPayload) => {
  const { data } = await api.put(`/channels/${channelId}/ai-config`, payload)
  return data
}

export const listChannelZones = async (channelId: string) => {
  const { data } = await api.get(`/channels/${channelId}/zones`)
  return data
}

export const addChannelZone = async (channelId: string, payload: ChannelZonePayload) => {
  const { data } = await api.post(`/channels/${channelId}/zones`, payload)
  return data
}

export const updateChannelZone = async (
  channelId: string,
  zoneId: string,
  payload: UpdateChannelZonePayload
) => {
  const { data } = await api.put(`/channels/${channelId}/zones/${zoneId}`, payload)
  return data
}

export const deleteChannelZone = async (channelId: string, zoneId: string) => {
  const { data } = await api.delete(`/channels/${channelId}/zones/${zoneId}`)
  return data
}

export const captureChannelSnapshot = async (channelId: string) => {
  const { data } = await api.post(`/channels/${channelId}/snapshot`)
  return data
}

export const getLatestChannelSnapshot = async (channelId: string) => {
  const { data } = await api.get(`/channels/${channelId}/snapshot/latest`)
  return data
}

export const startChannelAI = async (channelId: string) => {
  const { data } = await api.post(`/channels/${channelId}/ai/start`)
  return data
}

export const applyChannelAI = async (channelId: string) => {
  const { data } = await api.post(`/channels/${channelId}/ai/apply`)
  return data
}

export const stopChannelAI = async (channelId: string) => {
  const { data } = await api.post(`/channels/${channelId}/ai/stop`)
  return data
}

export const startChannelPlay = async (channelId: string) => {
  const { data } = await api.post(`/channels/${channelId}/play`)
  return data
}

export const stopChannelPlay = async (channelId: string) => {
  const { data } = await api.post(`/channels/${channelId}/stop`)
  return data
}
