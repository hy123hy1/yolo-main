<template>
  <div class="devices-page">
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>设备中心</h2>
          <p>统一管理摄像头/流设备，查看状态、基础信息和关联通道。</p>
        </div>
        <button class="ghost-btn" @click="load" :disabled="loading">
          {{ loading ? '刷新中...' : '刷新' }}
        </button>
      </div>

      <div v-if="message" :class="['message', messageType]">
        {{ message }}
      </div>

      <div class="form-grid">
        <input v-model.trim="form.name" placeholder="设备名称" />
        <select v-model="form.type">
          <option value="rtsp">RTSP</option>
          <option value="rtmp">RTMP</option>
          <option value="onvif">ONVIF</option>
        </select>
        <input v-model.trim="form.host" placeholder="主机/IP/说明" />
        <input v-model.trim="form.username" placeholder="用户名，可选" />
        <input v-model.trim="form.password" placeholder="密码，可选" />
        <input v-model.trim="form.remark" placeholder="备注，可选" />
        <button class="primary-btn" @click="submit" :disabled="submitting">
          {{ submitting ? '提交中...' : '新增设备' }}
        </button>
      </div>
    </section>

    <section class="panel">
      <div class="filter-grid">
        <input v-model.trim="filters.key" placeholder="按名称 / 主机 / 备注搜索" />
        <select v-model="filters.type">
          <option value="">全部类型</option>
          <option value="rtsp">RTSP</option>
          <option value="rtmp">RTMP</option>
          <option value="onvif">ONVIF</option>
        </select>
        <button class="ghost-btn" @click="load">查询</button>
        <button class="ghost-btn" @click="resetFilters">重置</button>
      </div>
    </section>

    <section class="panel">
      <div v-if="loading" class="empty-state">正在加载设备...</div>
      <div v-else-if="items.length === 0" class="empty-state">暂无设备</div>
      <div v-else class="device-list">
        <article v-for="item in items" :key="item.id" class="device-card">
          <div class="device-top">
            <div>
              <h3>{{ item.name }}</h3>
              <p class="meta-line">{{ item.id }}</p>
            </div>
            <div class="status-pills">
              <span class="pill type">{{ String(item.type || '').toUpperCase() }}</span>
              <span :class="['pill', item.status === 'online' ? 'ok' : 'muted']">
                {{ item.status || 'offline' }}
              </span>
            </div>
          </div>

          <div class="info-grid">
            <div><strong>主机：</strong>{{ item.host || '-' }}</div>
            <div><strong>用户名：</strong>{{ item.username || '-' }}</div>
            <div><strong>关联通道：</strong>{{ channelCountByDevice(item.id) }}</div>
            <div><strong>创建时间：</strong>{{ item.created_at || '-' }}</div>
            <div><strong>更新时间：</strong>{{ item.updated_at || '-' }}</div>
            <div><strong>备注：</strong>{{ item.remark || '-' }}</div>
          </div>

          <div v-if="channelsByDevice(item.id).length" class="related-box">
            <strong>关联通道</strong>
            <div class="related-list">
              <span v-for="channel in channelsByDevice(item.id)" :key="channel.id" class="related-chip">
                {{ channel.name || channel.id }}
              </span>
            </div>
          </div>

          <div class="actions">
            <button class="ghost-btn" @click="openEdit(item)">编辑</button>
            <button class="danger-btn" @click="remove(item.id)">删除</button>
          </div>
        </article>
      </div>
    </section>

    <div v-if="editingDevice" class="modal-mask" @click.self="closeEdit">
      <div class="modal-dialog">
        <div class="modal-header">
          <div>
            <h3>编辑设备</h3>
            <p>{{ editingDevice.id }}</p>
          </div>
          <button class="ghost-btn modal-close-btn" @click="closeEdit" aria-label="关闭">×</button>
        </div>

        <div class="form-grid modal-grid">
          <input v-model.trim="editForm.name" placeholder="设备名称" />
          <select v-model="editForm.type">
            <option value="rtsp">RTSP</option>
            <option value="rtmp">RTMP</option>
            <option value="onvif">ONVIF</option>
          </select>
          <input v-model.trim="editForm.host" placeholder="主机/IP/说明" />
          <input v-model.trim="editForm.username" placeholder="用户名，可选" />
          <input v-model.trim="editForm.password" placeholder="密码，可选" />
          <select v-model="editForm.status">
            <option value="offline">offline</option>
            <option value="online">online</option>
            <option value="unknown">unknown</option>
          </select>
          <input v-model.trim="editForm.remark" placeholder="备注，可选" class="span-2" />
        </div>

        <div class="modal-actions">
          <button class="ghost-btn" @click="closeEdit">取消</button>
          <button class="primary-btn" @click="saveEdit" :disabled="savingEdit">
            {{ savingEdit ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { createDevice, deleteDevice, listDevices, updateDevice } from '@/api/devices'
import { listChannels } from '@/api/channels'

type DeviceItem = {
  id: string
  name: string
  type: string
  host?: string
  username?: string
  password?: string
  remark?: string
  status?: string
  created_at?: string
  updated_at?: string
}

const loading = ref(false)
const submitting = ref(false)
const savingEdit = ref(false)
const items = ref<DeviceItem[]>([])
const channels = ref<any[]>([])
const message = ref('')
const messageType = ref<'success' | 'error'>('success')
const editingDevice = ref<DeviceItem | null>(null)

const filters = ref({
  key: '',
  type: ''
})

const form = ref({
  name: '',
  type: 'rtsp',
  host: '',
  username: '',
  password: '',
  remark: ''
})

const editForm = ref({
  name: '',
  type: 'rtsp',
  host: '',
  username: '',
  password: '',
  remark: '',
  status: 'offline'
})

const showMessage = (type: 'success' | 'error', text: string) => {
  messageType.value = type
  message.value = text
}

const channelGroups = computed(() => {
  const grouped: Record<string, any[]> = {}
  for (const channel of channels.value) {
    const deviceId = channel.device_id
    if (!deviceId) continue
    if (!grouped[deviceId]) grouped[deviceId] = []
    grouped[deviceId].push(channel)
  }
  return grouped
})

const channelsByDevice = (deviceId: string) => channelGroups.value[deviceId] || []
const channelCountByDevice = (deviceId: string) => channelsByDevice(deviceId).length

const load = async () => {
  loading.value = true
  try {
    const [deviceResult, channelResult] = await Promise.all([
      listDevices({
        key: filters.value.key || undefined,
        type: filters.value.type || undefined
      }),
      listChannels()
    ])
    items.value = deviceResult.items || []
    channels.value = channelResult.items || []
  } finally {
    loading.value = false
  }
}

const resetFilters = async () => {
  filters.value.key = ''
  filters.value.type = ''
  await load()
}

const submit = async () => {
  if (!form.value.name) return
  submitting.value = true
  message.value = ''
  try {
    await createDevice(form.value)
    form.value = {
      name: '',
      type: 'rtsp',
      host: '',
      username: '',
      password: '',
      remark: ''
    }
    await load()
    showMessage('success', '设备新增成功')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '设备新增失败')
  } finally {
    submitting.value = false
  }
}

const openEdit = (item: DeviceItem) => {
  editingDevice.value = item
  editForm.value = {
    name: item.name || '',
    type: item.type || 'rtsp',
    host: item.host || '',
    username: item.username || '',
    password: item.password || '',
    remark: item.remark || '',
    status: item.status || 'offline'
  }
}

const closeEdit = () => {
  editingDevice.value = null
}

const saveEdit = async () => {
  if (!editingDevice.value) return
  savingEdit.value = true
  try {
    await updateDevice(editingDevice.value.id, editForm.value)
    closeEdit()
    await load()
    showMessage('success', '设备更新成功')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '设备更新失败')
  } finally {
    savingEdit.value = false
  }
}

const remove = async (deviceId: string) => {
  if (!confirm(`确认删除设备 ${deviceId} 吗？`)) return
  try {
    await deleteDevice(deviceId)
    await load()
    showMessage('success', '设备已删除')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '设备删除失败')
  }
}

onMounted(load)
</script>

<style scoped>
.devices-page {
  display: grid;
  gap: 1rem;
}

.panel {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 1.25rem;
  box-shadow: 0 12px 24px rgba(15, 23, 42, 0.04);
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
  margin-bottom: 1rem;
}

.panel-header h2,
.device-top h3,
.modal-header h3 {
  margin: 0;
}

.panel-header p,
.meta-line,
.modal-header p {
  margin: 0.35rem 0 0;
  color: #64748b;
}

.form-grid,
.filter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 0.75rem;
}

.form-grid input,
.form-grid select,
.form-grid button,
.filter-grid input,
.filter-grid select,
.filter-grid button {
  min-height: 42px;
  padding: 0.75rem 0.9rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  font: inherit;
}

.device-list {
  display: grid;
  gap: 1rem;
}

.device-card {
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 1rem;
  display: grid;
  gap: 0.9rem;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
}

.device-top {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
}

.status-pills {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.pill {
  padding: 0.3rem 0.65rem;
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 600;
}

.pill.type {
  background: #dbeafe;
  color: #1d4ed8;
}

.pill.ok {
  background: #dcfce7;
  color: #166534;
}

.pill.muted {
  background: #e2e8f0;
  color: #475569;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.45rem;
  color: #334155;
  word-break: break-all;
}

.related-box {
  display: grid;
  gap: 0.6rem;
  padding: 0.9rem;
  border: 1px solid #dbe4ee;
  border-radius: 12px;
  background: #f8fafc;
}

.related-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.55rem;
}

.related-chip {
  display: inline-flex;
  align-items: center;
  padding: 0.35rem 0.65rem;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
}

.actions,
.modal-actions {
  display: flex;
  gap: 0.65rem;
  flex-wrap: wrap;
}

.primary-btn,
.ghost-btn,
.danger-btn {
  cursor: pointer;
}

.primary-btn {
  background: #1d4ed8;
  color: #fff;
  border-color: #1d4ed8;
}

.ghost-btn {
  background: #fff;
  color: #334155;
}

.modal-close-btn {
  min-width: 40px;
  padding: 0.4rem 0.75rem;
  font-size: 1.35rem;
  line-height: 1;
}

.danger-btn {
  background: #b91c1c;
  color: #fff;
  border-color: #b91c1c;
}

.empty-state,
.message {
  border-radius: 10px;
  padding: 0.8rem 1rem;
}

.empty-state {
  background: #f8fafc;
  color: #475569;
  border: 1px solid #e2e8f0;
}

.message.success {
  background: #ecfdf3;
  color: #027a48;
  border: 1px solid #a6f4c5;
}

.message.error {
  background: #fef3f2;
  color: #b42318;
  border: 1px solid #fecdca;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.62);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.25rem;
  z-index: 60;
}

.modal-dialog {
  width: min(860px, 100%);
  background: #fff;
  border-radius: 16px;
  padding: 1rem;
  display: grid;
  gap: 1rem;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
}

.modal-grid .span-2 {
  grid-column: span 2;
}

@media (max-width: 768px) {
  .panel-header,
  .device-top,
  .modal-header {
    flex-direction: column;
  }

  .modal-grid .span-2 {
    grid-column: span 1;
  }
}
</style>
