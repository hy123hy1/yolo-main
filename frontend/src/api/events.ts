import api from './axios'

export const listEvents = async () => {
  const { data } = await api.get('/events')
  return data
}

export interface ListEventsParams {
  channel_id?: string
  task_id?: string
  algorithm_id?: string
  class_name?: string
  page?: number
  page_size?: number
}

export const listEventsByQuery = async (params: ListEventsParams) => {
  const { data } = await api.get('/events', { params })
  return data
}

export const getEventSnapshotUrl = (eventId: string) => {
  const apiBase = import.meta.env.VITE_API_BASE || '/dev-api'
  return `${apiBase}/events/${eventId}/snapshot`
}
