import api from './axios'

export const listRecordings = async (params?: { channel_id?: string }) => {
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
