import api from './axios'

export interface MediaNodeItem {
  id: string
  name: string
  type: string
  api_base: string
  play_base: string
  webrtc_base?: string
  transcode_rtmp_host?: string
  public_rtsp_host?: string
  public_rtmp_host?: string
  public_http_host?: string
  rtsp_port?: number
  rtmp_port?: number
  http_port?: number
  https_port?: number
  hls_file?: string
  httpflv_suffix?: string
  webrtc_path?: string
  secret?: string
  is_default?: boolean
  status?: string
  created_at?: string
  updated_at?: string
}

export const listMediaNodes = async () => {
  const { data } = await api.get('/media-nodes')
  return data
}

export interface MediaNodePayload {
  name: string
  type: string
  api_base: string
  play_base: string
  webrtc_base?: string
  transcode_rtmp_host?: string
  public_rtsp_host?: string
  public_rtmp_host?: string
  public_http_host?: string
  rtsp_port?: number
  rtmp_port?: number
  http_port?: number
  https_port?: number
  hls_file?: string
  httpflv_suffix?: string
  webrtc_path?: string
  secret?: string
  is_default?: boolean
}

export const createMediaNode = async (payload: MediaNodePayload) => {
  const { data } = await api.post('/media-nodes', payload)
  return data
}

export type UpdateMediaNodePayload = Partial<MediaNodePayload & { status: string }>

export const updateMediaNode = async (nodeId: string, payload: UpdateMediaNodePayload) => {
  const { data } = await api.put(`/media-nodes/${nodeId}`, payload)
  return data
}

export const deleteMediaNode = async (nodeId: string) => {
  const { data } = await api.delete(`/media-nodes/${nodeId}`)
  return data
}

export const getMediaNodeHealth = async (nodeId: string) => {
  const { data } = await api.get(`/media-nodes/${nodeId}/health`)
  return data
}

export const getMediaNodeServerConfig = async (nodeId: string) => {
  const { data } = await api.get(`/media-nodes/${nodeId}/server-config`)
  return data
}
