import api from './axios'

export interface ListRecordingsParams {
  channel_id?: string
  task_id?: string
  algorithm_id?: string
  page?: number
  page_size?: number
}

export const listRecordings = async (params?: ListRecordingsParams) => {
  const { data } = await api.get('/recordings', { params })
  return data
}

export const listRecordingTimeline = async (params?: { channel_id?: string }) => {
  const { data } = await api.get('/recordings/timeline', { params })
  return data
}

export const listRecordingMonthly = async (params?: { channel_id?: string }) => {
  const { data } = await api.get('/recordings/monthly', { params })
  return data
}
