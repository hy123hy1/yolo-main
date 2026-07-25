<template>
  <div class="camera-health-page">
    <section class="health-header">
      <div>
        <p class="eyebrow">CHANNEL HEALTH</p>
        <h2>摄像头健康</h2>
        <p class="header-copy">独立查看摄像头遮挡和画面扭转状态，检测源为通道原始 RTSP。</p>
      </div>
      <button class="refresh-button" type="button" :disabled="loading" @click="load">
        {{ loading ? '刷新中...' : '刷新' }}
      </button>
    </section>

    <div v-if="message" :class="['health-message', messageType]">{{ message }}</div>

    <section v-if="loading && channels.length === 0" class="health-empty">正在加载通道健康状态...</section>
    <section v-else-if="channels.length === 0" class="health-empty">暂无通道</section>
    <section v-else class="channel-health-list">
      <article v-for="channel in channels" :key="channel.id" class="channel-health-card">
        <header class="channel-health-card__header">
          <div>
            <h3>{{ channel.name || channel.id }}</h3>
            <p>{{ channel.id }}</p>
          </div>
          <div class="status-group">
            <span :class="['status-pill', channelStatus(channel.id) === 'online' ? 'ok' : 'muted']">
              {{ statusLabel(channelStatus(channel.id)) }}
            </span>
            <span class="source-pill">原始 RTSP</span>
          </div>
        </header>

        <div class="algorithm-grid">
          <article v-for="algorithm in algorithms" :key="algorithm.key" class="algorithm-row">
            <div class="algorithm-row__top">
              <div>
                <h4>{{ algorithm.label }}</h4>
                <span class="algorithm-key">{{ algorithm.key }}</span>
              </div>
              <label class="switch-label">
                <input
                  type="checkbox"
                  :checked="configFor(channel.id, algorithm.key).enabled"
                  :disabled="savingKey === configKey(channel.id, algorithm.key)"
                  @change="toggleAlgorithm(channel.id, algorithm.key, $event)"
                />
                <span>启用</span>
              </label>
            </div>

            <div class="metric-grid">
              <div><span>状态</span><strong>{{ statusLabel(stateFor(channel.id, algorithm.key).status) }}</strong></div>
              <div><span>Score</span><strong>{{ formatNumber(stateFor(channel.id, algorithm.key).current_score) }}</strong></div>
              <div><span>最近更新</span><strong>{{ stateFor(channel.id, algorithm.key).updated_at || '-' }}</strong></div>
              <div><span>报警次数</span><strong>{{ eventCount(channel.id, algorithm.key) }}</strong></div>
            </div>

            <div class="parameter-grid">
              <label>
                <span>采样间隔(ms)</span>
                <input v-model.number="configFor(channel.id, algorithm.key).sample_interval_ms" type="number" min="200" />
              </label>
              <label>
                <span>报警保持(s)</span>
                <input v-model.number="configFor(channel.id, algorithm.key).alarm_hold_seconds" type="number" min="0" />
              </label>
              <label>
                <span>恢复保持(s)</span>
                <input v-model.number="configFor(channel.id, algorithm.key).recovery_hold_seconds" type="number" min="1" />
              </label>
              <label>
                <span>重复间隔(s)</span>
                <input v-model.number="configFor(channel.id, algorithm.key).alert_interval_seconds" type="number" min="0" />
              </label>
            </div>

            <div class="algorithm-row__actions">
              <button class="save-button" type="button" :disabled="savingKey === configKey(channel.id, algorithm.key)" @click="saveConfig(channel.id, algorithm.key)">
                {{ savingKey === configKey(channel.id, algorithm.key) ? '保存中...' : '保存参数' }}
              </button>
              <button class="secondary-button" type="button" @click="recalibrate(channel.id, algorithm.key)">
                重新校准
              </button>
            </div>

            <div class="metrics-line">
              <span v-for="(value, key) in stateFor(channel.id, algorithm.key).last_metrics_json || {}" :key="key">
                {{ metricLabel(String(key)) }}: {{ formatMetric(value) }}
              </span>
            </div>
          </article>
        </div>
      </article>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { listChannels } from '@/api/channels'
import {
  CAMERA_OCCLUSION,
  CAMERA_VIEW_SHIFT,
  getCameraHealthStatus,
  listCameraHealthConfigs,
  listCameraHealthEvents,
  recalibrateCameraHealth,
  updateCameraHealthConfig,
  type CameraHealthAlgorithm,
  type CameraHealthConfig,
  type CameraHealthEvent,
  type CameraHealthState,
  type CameraHealthStatus,
} from '@/api/cameraHealth'

type ChannelItem = { id: string; name?: string; source_url?: string }
type AlgorithmOption = { key: CameraHealthAlgorithm; label: string }

const algorithms: AlgorithmOption[] = [
  { key: CAMERA_OCCLUSION, label: '摄像头遮挡识别' },
  { key: CAMERA_VIEW_SHIFT, label: '摄像头扭转角度识别' },
]
const channels = ref<ChannelItem[]>([])
const configs = reactive<Record<string, CameraHealthConfig>>({})
const statuses = reactive<Record<string, CameraHealthStatus>>({})
const events = ref<CameraHealthEvent[]>([])
const loading = ref(false)
const savingKey = ref('')
const message = ref('')
const messageType = ref<'success' | 'error'>('success')
let refreshTimer: number | undefined

const configKey = (channelId: string, algorithmType: CameraHealthAlgorithm) => `${channelId}:${algorithmType}`

const configFor = (channelId: string, algorithmType: CameraHealthAlgorithm): CameraHealthConfig => {
  const key = configKey(channelId, algorithmType)
  if (!configs[key]) {
    configs[key] = {
      channel_id: channelId,
      algorithm_type: algorithmType,
      enabled: false,
      sample_interval_ms: 1000,
      alarm_hold_seconds: 10,
      recovery_hold_seconds: 15,
      alert_interval_seconds: 300,
      need_snapshot: true,
    }
  }
  return configs[key]
}

const stateFor = (channelId: string, algorithmType: CameraHealthAlgorithm): CameraHealthState => {
  const state = statuses[channelId]?.algorithms?.find((item) => item.algorithm_type === algorithmType)
  return state || { channel_id: channelId, algorithm_type: algorithmType, status: configFor(channelId, algorithmType).enabled ? 'offline' : 'disabled' }
}

const channelStatus = (channelId: string) => statuses[channelId]?.frame?.status || statuses[channelId]?.worker_status || 'offline'
const statusLabel = (status: string) => ({
  online: '在线',
  running: '运行中',
  normal: '正常',
  calibrating: '校准中',
  suspected: '疑似异常',
  alarming: '报警中',
  recovering: '恢复中',
  degraded: '画面不可用',
  offline: '离线',
  disabled: '未启用',
  stopped: '未运行',
}[status] || status || '未知')
const formatNumber = (value?: number) => typeof value === 'number' ? value.toFixed(3) : '-'
const formatMetric = (value: number | string) => typeof value === 'number' ? value.toFixed(3) : value
const metricLabel = (key: string) => ({
  brightness_mean: '亮度',
  entropy: '熵',
  edge_density: '边缘密度',
  blur_variance: '模糊度',
  uniform_ratio: '纯色比例',
  roll_angle_deg: '扭转角',
  horizontal_shift_ratio: '水平偏移',
  vertical_shift_ratio: '垂直偏移',
  inlier_ratio: '内点比例',
}[key] || key)
const eventCount = (channelId: string, algorithmType: CameraHealthAlgorithm) =>
  events.value.filter((item) => item.channel_id === channelId && item.algorithm_type === algorithmType).length

const showMessage = (type: 'success' | 'error', value: string) => {
  messageType.value = type
  message.value = value
  window.setTimeout(() => { message.value = '' }, 3000)
}

const load = async () => {
  loading.value = true
  try {
    const [channelResult, configResult, eventResult] = await Promise.all([
      listChannels(),
      listCameraHealthConfigs(),
      listCameraHealthEvents({ page: 1, page_size: 100 }),
    ])
    channels.value = channelResult.items || []
    Object.keys(configs).forEach((key) => delete configs[key])
    for (const item of configResult.items || []) configs[configKey(item.channel_id, item.algorithm_type)] = item
    events.value = eventResult.items || []
    await refreshStatuses()
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '加载摄像头健康失败')
  } finally {
    loading.value = false
  }
}

const refreshStatuses = async () => {
  const results = await Promise.all(channels.value.map(async (channel) => [channel.id, await getCameraHealthStatus(channel.id)] as const))
  for (const [channelId, status] of results) statuses[channelId] = status
}

const saveConfig = async (channelId: string, algorithmType: CameraHealthAlgorithm) => {
  const key = configKey(channelId, algorithmType)
  savingKey.value = key
  try {
    configs[key] = await updateCameraHealthConfig(channelId, algorithmType, configFor(channelId, algorithmType))
    await refreshStatuses()
    showMessage('success', '摄像头健康参数已保存')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '保存参数失败')
  } finally {
    savingKey.value = ''
  }
}

const toggleAlgorithm = async (channelId: string, algorithmType: CameraHealthAlgorithm, event: Event) => {
  configFor(channelId, algorithmType).enabled = (event.target as HTMLInputElement).checked
  await saveConfig(channelId, algorithmType)
}

const recalibrate = async (channelId: string, algorithmType: CameraHealthAlgorithm) => {
  try {
    await recalibrateCameraHealth(channelId, algorithmType)
    await refreshStatuses()
    showMessage('success', '已重新进入校准状态')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '重新校准失败')
  }
}

onMounted(async () => {
  await load()
  refreshTimer = window.setInterval(() => { void refreshStatuses() }, 3000)
})

onBeforeUnmount(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<style scoped>
.camera-health-page { display: grid; gap: 1rem; }
.health-header, .channel-health-card { background: #fff; border: 1px solid #e4e8ed; border-radius: 8px; box-shadow: 0 4px 18px rgba(31, 41, 55, .05); }
.health-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; padding: 1.5rem; }
.eyebrow, .algorithm-key { color: #7b8794; font-size: .72rem; letter-spacing: .08em; }
.health-header h2 { margin: .3rem 0 .5rem; color: #1f2937; }
.header-copy { color: #6b7280; }
.refresh-button, .save-button, .secondary-button { border: 0; border-radius: 5px; color: #fff; cursor: pointer; padding: .55rem .85rem; }
.refresh-button, .save-button { background: #2563eb; }
.secondary-button { background: #64748b; }
.refresh-button:disabled, .save-button:disabled { opacity: .6; cursor: not-allowed; }
.health-message { padding: .75rem 1rem; border-radius: 5px; }
.health-message.success { background: #ecfdf5; color: #047857; }
.health-message.error { background: #fef2f2; color: #b91c1c; }
.health-empty { padding: 3rem; color: #6b7280; text-align: center; background: #fff; border: 1px dashed #cbd5e1; border-radius: 8px; }
.channel-health-card { padding: 1.25rem; }
.channel-health-card__header, .algorithm-row__top, .algorithm-row__actions { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
.channel-health-card__header { padding-bottom: 1rem; border-bottom: 1px solid #eef2f7; }
.channel-health-card h3, .algorithm-row h4 { color: #1f2937; }
.channel-health-card p { margin-top: .25rem; color: #94a3b8; font-size: .85rem; }
.status-group { display: flex; gap: .5rem; flex-wrap: wrap; justify-content: flex-end; }
.status-pill, .source-pill { border-radius: 999px; padding: .25rem .55rem; font-size: .78rem; }
.status-pill.ok { background: #dcfce7; color: #166534; }
.status-pill.muted, .source-pill { background: #f1f5f9; color: #64748b; }
.algorithm-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; padding-top: 1rem; }
.algorithm-row { border: 1px solid #e5e7eb; border-radius: 6px; padding: 1rem; }
.switch-label { display: inline-flex; align-items: center; gap: .4rem; color: #475569; font-size: .85rem; }
.metric-grid, .parameter-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .6rem; margin-top: 1rem; }
.metric-grid div, .parameter-grid label { display: grid; gap: .25rem; color: #64748b; font-size: .75rem; }
.metric-grid strong { color: #1f2937; font-size: .88rem; }
.parameter-grid input { width: 100%; padding: .45rem; border: 1px solid #cbd5e1; border-radius: 4px; }
.algorithm-row__actions { justify-content: flex-start; margin-top: 1rem; }
.metrics-line { display: flex; flex-wrap: wrap; gap: .5rem 1rem; margin-top: 1rem; color: #64748b; font-size: .75rem; }
@media (max-width: 900px) { .algorithm-grid { grid-template-columns: 1fr; } }
@media (max-width: 640px) { .health-header, .channel-health-card__header { flex-direction: column; } .metric-grid, .parameter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
