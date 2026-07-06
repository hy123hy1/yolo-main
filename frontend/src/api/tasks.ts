import api from './axios'

export interface TaskRegionPayload {
  id?: string
  name?: string
  type?: string
  color?: string
  coordinates: number[][]
  enabled?: boolean
}

export interface TaskAlgorithmPayload {
  id?: string
  algorithm_type?: string
  scene_key?: string
  scene_name?: string
  rule_type?: string
  model_id: string
  enabled?: boolean
  labels?: string[]
  detect_all_classes?: boolean
  label_source?: string
  confidence?: number
  alert_interval?: number
  need_snapshot?: boolean
  need_video_clip?: boolean
  video_before_seconds?: number
  video_after_seconds?: number
  overlay_color?: string
  postprocess_config?: Record<string, any>
  region_ids?: string[]
  regions?: TaskRegionPayload[]
}

export interface TaskPayload {
  name: string
  source_url?: string
  source_type?: string
  channel_id?: string | null
  frame_interval?: number
  status?: string
  algorithms?: TaskAlgorithmPayload[]
  regions?: TaskRegionPayload[]
}

export const listTasks = async () => {
  const { data } = await api.get('/tasks')
  return data
}

export const getTask = async (taskId: string) => {
  const { data } = await api.get(`/tasks/${taskId}`)
  return data
}

export const listTaskAlgorithms = async (taskId: string) => {
  const { data } = await api.get(`/tasks/${taskId}/algorithms`)
  return data
}

export const listTaskAlgorithmRegions = async (taskId: string, algorithmId: string) => {
  const { data } = await api.get(`/tasks/${taskId}/algorithms/${algorithmId}/regions`)
  return data
}

export const listTaskAvailableModels = async (taskId: string) => {
  const { data } = await api.get(`/tasks/${taskId}/available-models`)
  return data
}

export const createTask = async (payload: TaskPayload) => {
  const { data } = await api.post('/tasks', payload)
  return data
}

export const updateTask = async (taskId: string, payload: Partial<TaskPayload>) => {
  const { data } = await api.put(`/tasks/${taskId}`, payload)
  return data
}

export const deleteTask = async (taskId: string) => {
  const { data } = await api.delete(`/tasks/${taskId}`)
  return data
}

export const startTask = async (taskId: string) => {
  const { data } = await api.post(`/tasks/${taskId}/start`)
  return data
}

export const stopTask = async (taskId: string) => {
  const { data } = await api.post(`/tasks/${taskId}/stop`)
  return data
}

export const getTaskStatus = async (taskId: string) => {
  const { data } = await api.get(`/tasks/${taskId}/status`)
  return data
}

export const listTaskMetrics = async (taskId: string) => {
  const { data } = await api.get(`/tasks/${taskId}/metrics`)
  return data
}

export const listTaskMetricHistory = async (
  taskId: string,
  params?: {
    algorithm_id?: string
    metric_type?: string
    region_id?: string
    start_time?: string
    end_time?: string
    page?: number
    page_size?: number
  }
) => {
  const { data } = await api.get(`/tasks/${taskId}/metrics/history`, { params })
  return data
}

export const addTaskAlgorithm = async (taskId: string, payload: TaskAlgorithmPayload) => {
  const { data } = await api.post(`/tasks/${taskId}/algorithms`, payload)
  return data
}

export const updateTaskAlgorithm = async (
  taskId: string,
  algorithmId: string,
  payload: TaskAlgorithmPayload
) => {
  const { data } = await api.put(`/tasks/${taskId}/algorithms/${algorithmId}`, payload)
  return data
}

export const deleteTaskAlgorithm = async (taskId: string, algorithmId: string) => {
  const { data } = await api.delete(`/tasks/${taskId}/algorithms/${algorithmId}`)
  return data
}

export const replaceTaskAlgorithmRegions = async (
  taskId: string,
  algorithmId: string,
  payload: { regions: TaskRegionPayload[] }
) => {
  const { data } = await api.put(`/tasks/${taskId}/algorithms/${algorithmId}/regions`, payload)
  return data
}
