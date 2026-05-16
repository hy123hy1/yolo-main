import api from './axios'

export interface ListDevicesParams {
  key?: string
  type?: string
}

export interface DevicePayload {
  name: string
  type: string
  host?: string
  username?: string
  password?: string
  remark?: string
}

export interface UpdateDevicePayload {
  name?: string
  type?: string
  host?: string
  username?: string
  password?: string
  remark?: string
  status?: string
}

export const listDevices = async (params?: ListDevicesParams) => {
  const { data } = await api.get('/devices', { params })
  return data
}

export const createDevice = async (payload: DevicePayload) => {
  const { data } = await api.post('/devices', payload)
  return data
}

export const updateDevice = async (deviceId: string, payload: UpdateDevicePayload) => {
  const { data } = await api.put(`/devices/${deviceId}`, payload)
  return data
}

export const deleteDevice = async (deviceId: string) => {
  const { data } = await api.delete(`/devices/${deviceId}`)
  return data
}
