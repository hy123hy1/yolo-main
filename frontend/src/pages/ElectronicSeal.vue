<template>
  <div class="electronic-seal-page">
    <section class="seal-header">
      <div>
        <p class="eyebrow">ELECTRONIC SEAL</p>
        <h2>电子封条</h2>
        <p>按通道独立运行车辆驶出、房间无人和摄像头健康检查。</p>
      </div>
      <button class="primary-btn" type="button" :disabled="loading" @click="load">刷新</button>
    </section>

    <div v-if="message" :class="['message', messageType]">{{ message }}</div>

    <section class="control-panel">
      <label>
        <span>通道</span>
        <select v-model="selectedChannelId" @change="selectChannel">
          <option value="">选择通道</option>
          <option v-for="channel in channels" :key="channel.id" :value="channel.id">
            {{ channel.name || channel.id }}
          </option>
        </select>
      </label>
      <div class="algorithm-tabs" role="tablist" aria-label="电子封条算法">
        <button
          v-for="algorithm in visibleAlgorithms"
          :key="algorithm.key"
          :class="['tab-btn', { active: selectedAlgorithm === algorithm.key }]"
          type="button"
          @click="selectedAlgorithm = algorithm.key"
        >
          {{ algorithm.label }}
        </button>
      </div>
    </section>

    <section v-if="!selectedChannelId" class="empty-state">请选择通道开始配置电子封条。</section>
    <template v-else>
      <section class="status-strip">
        <div><span>worker</span><strong>{{ status.worker_status || 'stopped' }}</strong></div>
        <div><span>帧状态</span><strong>{{ status.frame?.status || 'offline' }}</strong></div>
        <div><span>检测源</span><strong>{{ status.source_url || 'electronic_seal_channels.source_url' }}</strong></div>
        <div><span>最近错误</span><strong>{{ status.frame?.last_error || '-' }}</strong></div>
      </section>

      <section class="preview-card">
        <div class="card-title-row">
          <div>
            <h3>配置预览</h3>
            <p>预览使用通道浏览器流，检测仍使用原始 RTSP。</p>
          </div>
          <div class="action-row preview-actions">
            <button class="secondary-btn" type="button" :disabled="previewLoading" @click="startPreview">
              {{ previewPlaying ? '重新加载预览' : '加载原始流' }}
            </button>
            <button class="ghost-btn" type="button" :disabled="!previewPlaying" @click="stopPreview">停止</button>
          </div>
        </div>
        <div class="preview-stage" :class="{ drawing: Boolean(drawingMode) }">
          <video ref="previewVideo" class="preview-video" muted autoplay playsinline @loadedmetadata="resizeCanvas" />
          <canvas
            ref="overlayCanvas"
            class="preview-overlay"
            :class="{ editable: Boolean(drawingMode) }"
            @pointerdown="handleCanvasPointerDown"
            @dblclick="finishRegion"
          />
          <div v-if="!previewPlaying" class="preview-placeholder">预览未加载，可点击按钮重试</div>
        </div>
        <p v-if="previewError" class="preview-error">{{ previewError }}</p>
        <div class="draw-toolbar">
          <button class="ghost-btn" type="button" :class="{ active: drawingMode === 'line' }" @click="beginDrawing('line')">画车辆驶出线</button>
          <button class="ghost-btn" type="button" :class="{ active: drawingMode === 'region' }" @click="beginDrawing('region')">画空岗区域</button>
          <button class="ghost-btn" type="button" :disabled="!drawingMode" @click="finishRegion">完成</button>
          <button class="ghost-btn" type="button" @click="clearDrawing">清除当前图形</button>
          <span class="draw-hint">{{ drawingHint }}</span>
        </div>
      </section>

      <section class="config-grid">
        <article class="config-card">
          <div class="card-title-row">
            <div>
              <h3>{{ selectedAlgorithmLabel }}</h3>
              <p>{{ selectedAlgorithm }}</p>
            </div>
          </div>

          <div class="form-grid">
            <label v-if="isModelAlgorithm"><span>复用检测模型</span><select v-model="draft.model_id">
              <option value="">选择 YOLO 模型</option>
              <option v-for="model in models" :key="model.model_id" :value="model.model_id">
                {{ model.name || model.model_id }}
              </option>
            </select></label>
            <label><span>采样间隔(ms)</span><input v-model.number="draft.sample_interval_ms" type="number" min="200" /></label>
            <label><span>健康检查间隔(s)</span><input v-model.number="draft.camera_health_interval_seconds" type="number" min="1" /></label>
            <label><span>报警保持(s)</span><input v-model.number="draft.alarm_hold_seconds" type="number" min="0" /></label>
            <label><span>恢复保持(s)</span><input v-model.number="draft.recovery_hold_seconds" type="number" min="0" /></label>
          </div>

          <div class="json-grid">
            <label><span>虚拟线配置</span><textarea v-model="lineText" rows="8" placeholder='{"line_start":[0,0],"line_end":[100,0],"direction":"inside_to_outside"}' /></label>
            <label><span>区域配置</span><textarea v-model="regionText" rows="8" placeholder='{"polygon":[[0,0],[100,0],[100,100],[0,100]],"hold_seconds":600}' /></label>
          </div>

          <div class="action-row">
            <button class="primary-btn" type="button" :disabled="saving" @click="save">保存配置</button>
            <button class="secondary-btn" type="button" :disabled="saving" @click="recalibrate">重新校准</button>
          </div>
        </article>

        <article class="config-card state-card">
          <div class="card-title-row"><h3>算法状态</h3><span :class="['status-pill', status.running ? 'ok' : 'muted']">{{ status.running ? '运行中' : '未运行' }}</span></div>
          <div class="state-list">
            <div><span>当前算法</span><strong>{{ selectedAlgorithmLabel }}</strong></div>
            <div><span>配置状态</span><strong>{{ selectedRuntimeStatus }}</strong></div>
            <div><span>通道源</span><strong>原始 RTSP</strong></div>
            <div><span>数据目录</span><strong>电子封条独立目录</strong></div>
          </div>
          <p class="hint">摄像头健康算法按配置间隔执行，状态和事件只写入电子封条表。</p>
        </article>
      </section>

      <section class="events-card">
        <div class="card-title-row"><div><h3>报警事件</h3><p>共 {{ events.total }} 条</p></div><button class="ghost-btn" type="button" @click="loadEvents">刷新事件</button></div>
        <div v-if="events.items.length === 0" class="empty-state compact">暂无报警事件</div>
        <div v-else class="event-table-wrap">
          <table class="event-table">
            <thead><tr><th>算法</th><th>时间</th><th>状态</th><th>Score</th><th>证据</th><th>上传</th></tr></thead>
            <tbody>
              <tr v-for="event in events.items" :key="event.id">
                <td>{{ labelFor(event.algorithm_type) }}</td>
                <td>{{ event.started_at || '-' }}</td>
                <td>{{ event.status || event.severity }}</td>
                <td>{{ formatScore(event.score) }}</td>
                <td class="evidence-links">
                  <a v-if="event.snapshot_url" :href="evidenceUrl(event.snapshot_url)" target="_blank">截图</a>
                  <a v-if="event.video_url" :href="evidenceUrl(event.video_url)" target="_blank">视频</a>
                  <span v-if="!event.snapshot_url && !event.video_url">-</span>
                </td>
                <td>
                  <button v-if="event.upload_job_id" class="mini-btn" type="button" @click="retryUpload(event.upload_job_id)">重试上传</button>
                  <span v-else>{{ event.upload_status || '-' }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="retry-card">
        <label><span>上传任务 ID</span><input v-model.trim="retryJobId" placeholder="输入 upload job id" /></label>
        <button class="secondary-btn" type="button" :disabled="!retryJobId" @click="retryUpload(retryJobId)">重试上传</button>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  ELECTRONIC_SEAL_ALGORITHMS,
  electronicSealEvidenceUrl,
  getElectronicSealStatus,
  listElectronicSealConfigs,
  listElectronicSealChannels,
  listElectronicSealModels,
  listElectronicSealProfiles,
  listElectronicSealEvents,
  recalibrateElectronicSeal,
  retryElectronicSealUpload,
  startElectronicSealChannelPlay,
  updateElectronicSealConfig,
  type ElectronicSealAlgorithm,
  type ElectronicSealConfig,
  type ElectronicSealEvent,
  type ElectronicSealModel,
  type ElectronicSealStatus,
} from '@/api/electronicSeal'

type Channel = { id: string; name?: string | null }
const algorithms = ELECTRONIC_SEAL_ALGORITHMS
const roleAlgorithmKeys: Record<string, ElectronicSealAlgorithm[]> = {
  vehicle_entry: ['truck_departure', 'camera_occlusion', 'camera_view_shift'],
  dispatch_room: ['room_empty', 'camera_occlusion', 'camera_view_shift'],
}
const channels = ref<Channel[]>([])
const profiles = reactive<Record<string, { role: string; bound: boolean }>>({})
const models = ref<ElectronicSealModel[]>([])
const selectedChannelId = ref('')
const selectedAlgorithm = ref<ElectronicSealAlgorithm>('truck_departure')
const configs = reactive<Record<string, ElectronicSealConfig>>({})
const status = reactive<ElectronicSealStatus>({ channel_id: '', running: false, worker_status: 'stopped' })
const events = reactive<{ items: ElectronicSealEvent[]; total: number }>({ items: [], total: 0 })
const draft = reactive<ElectronicSealConfig>({ channel_id: '', algorithm_type: 'truck_departure', enabled: true, model_id: '', sample_interval_ms: 1000, camera_health_interval_seconds: 3600, alarm_hold_seconds: 10, recovery_hold_seconds: 15 })
const lineText = ref('{}')
const regionText = ref('{}')
const retryJobId = ref('')
const loading = ref(false)
const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')
const previewVideo = ref<HTMLVideoElement | null>(null)
const overlayCanvas = ref<HTMLCanvasElement | null>(null)
const previewPlaying = ref(false)
const previewLoading = ref(false)
const previewError = ref('')
const drawingMode = ref<'line' | 'region' | null>(null)
const drawingPoints = ref<Array<[number, number]>>([])
let previewPeerConnection: RTCPeerConnection | null = null
let previewAttemptId = 0
let pollTimer: number | undefined
let previewResizeObserver: ResizeObserver | undefined

const configKey = (channelId: string, algorithm: ElectronicSealAlgorithm) => `${channelId}:${algorithm}`
const selectedAlgorithmLabel = computed(() => algorithms.find((item) => item.key === selectedAlgorithm.value)?.label || selectedAlgorithm.value)
const visibleAlgorithms = computed(() => {
  const role = profiles[selectedChannelId.value]?.role
  const keys = roleAlgorithmKeys[role || ''] || algorithms.map((item) => item.key)
  return algorithms.filter((item) => keys.includes(item.key))
})
const selectedRuntimeStatus = computed(() => {
  const config = status.configs?.find((item) => item.algorithm_type === selectedAlgorithm.value)
  return config?.state?.status || (status.running ? 'starting' : 'stopped')
})
const isModelAlgorithm = computed(() => selectedAlgorithm.value === 'truck_departure' || selectedAlgorithm.value === 'room_empty')
const drawingHint = computed(() => {
  if (drawingMode.value === 'line') return drawingPoints.value.length < 2 ? '点击两个点确定车辆驶出方向线' : '车辆驶出线已完成'
  if (drawingMode.value === 'region') return drawingPoints.value.length < 3 ? '连续点击至少三个点，双击或点击完成结束' : '空岗区域已完成'
  return '先选择画线或画区域'
})
const labelFor = (key: string) => algorithms.find((item) => item.key === key)?.label || key
const formatScore = (value?: number | null) => typeof value === 'number' ? value.toFixed(3) : '-'
const evidenceUrl = (eventUrl: string | null | undefined, kind?: 'snapshot' | 'video') => electronicSealEvidenceUrl(eventUrl, kind)

const showMessage = (type: 'success' | 'error', text: string) => { messageType.value = type; message.value = text; window.setTimeout(() => { message.value = '' }, 3000) }
const defaultConfig = (channelId: string, algorithm: ElectronicSealAlgorithm): ElectronicSealConfig => ({ channel_id: channelId, algorithm_type: algorithm, enabled: true, model_id: '', sample_interval_ms: 1000, camera_health_interval_seconds: 3600, alarm_hold_seconds: algorithm === 'room_empty' ? 600 : 10, recovery_hold_seconds: 15, line_config_json: {}, region_config_json: {}, threshold_config_json: {} })
const canvasDebug = (event: string, payload: Record<string, unknown>) => {
  console.debug(`[ElectronicSealCanvas] ${event} ${JSON.stringify(payload)}`)
}
const REGION_COLOR = '#0000ff'
const syncDraft = async () => {
  if (!selectedChannelId.value) return
  const config = configs[configKey(selectedChannelId.value, selectedAlgorithm.value)] || defaultConfig(selectedChannelId.value, selectedAlgorithm.value)
  Object.assign(draft, config)
  lineText.value = JSON.stringify(config.line_config_json || {}, null, 2)
  regionText.value = JSON.stringify(config.region_config_json || {}, null, 2)
  canvasDebug('config-sync', {
    channelId: selectedChannelId.value,
    algorithm: selectedAlgorithm.value,
    lineConfig: config.line_config_json || {},
    regionConfig: config.region_config_json || {},
  })
  await nextTick()
  resizeCanvas()
}

const loadEvents = async () => {
  try {
    const result = await listElectronicSealEvents({ channel_id: selectedChannelId.value || undefined, page: 1, page_size: 100 })
    events.items = result.items || []
    events.total = result.total || 0
  } catch {
    events.items = []
    events.total = 0
  }
}
const refreshStatus = async () => {
  if (!selectedChannelId.value) return
  try {
    Object.assign(status, await getElectronicSealStatus(selectedChannelId.value))
  } catch {
    Object.assign(status, {
      channel_id: selectedChannelId.value,
      running: false,
      worker_status: 'unavailable',
      frame: { status: 'offline', last_error: '电子封条 Worker 未连接' },
    })
  }
}
const selectChannel = async () => {
  stopPreview()
  const keys = visibleAlgorithms.value
  if (keys.length && !keys.some((item) => item.key === selectedAlgorithm.value)) selectedAlgorithm.value = keys[0].key
  await syncDraft()
  await Promise.allSettled([refreshStatus(), loadEvents()])
  await startPreview()
}
const load = async () => {
  loading.value = true
  try {
    const [channelResult, modelResult] = await Promise.all([
      listElectronicSealChannels(),
      listElectronicSealModels(),
    ])
    const workerResults = await Promise.allSettled([listElectronicSealConfigs(), listElectronicSealProfiles()])
    const configResult = workerResults[0].status === 'fulfilled' ? workerResults[0].value : { items: [] }
    const profileResult = workerResults[1].status === 'fulfilled' ? workerResults[1].value : { items: [] }
    channels.value = channelResult.items || []
    models.value = modelResult.models || []
    Object.keys(profiles).forEach((key) => delete profiles[key])
    for (const profile of profileResult.items || []) profiles[profile.channel_id] = { role: profile.role, bound: profile.bound }
    Object.keys(configs).forEach((key) => delete configs[key])
    for (const config of configResult.items || []) configs[configKey(config.channel_id, config.algorithm_type)] = config
    if (!selectedChannelId.value && channels.value[0]) selectedChannelId.value = channels.value[0].id
    await syncDraft()
    void selectChannel()
  } catch (error: any) { showMessage('error', error?.response?.data?.detail || error?.message || '加载电子封条失败') }
  finally { loading.value = false }
}
const save = async () => {
  if (!selectedChannelId.value) return
  saving.value = true
  try {
    const { enabled: _legacyEnabled, ...configPayload } = draft
    const payload = { ...configPayload, line_config_json: JSON.parse(lineText.value || '{}'), region_config_json: JSON.parse(regionText.value || '{}') }
    configs[configKey(selectedChannelId.value, selectedAlgorithm.value)] = await updateElectronicSealConfig(selectedChannelId.value, selectedAlgorithm.value, payload)
    await refreshStatus(); resizeCanvas(); showMessage('success', '电子封条配置已保存')
  } catch (error: any) { showMessage('error', error?.message || '配置 JSON 或参数无效') }
  finally { saving.value = false }
}
const recalibrate = async () => { if (!selectedChannelId.value) return; await recalibrateElectronicSeal(selectedChannelId.value, selectedAlgorithm.value); await refreshStatus(); showMessage('success', '已重新校准') }
const retryUpload = async (jobId: string) => { try { await retryElectronicSealUpload(jobId); retryJobId.value = ''; showMessage('success', '上传任务已重新排队') } catch (error: any) { showMessage('error', error?.message || '上传重试失败') } }

watch([selectedChannelId, selectedAlgorithm], () => { void syncDraft() }, { flush: 'post' })
const normalizedPoint = (event: PointerEvent): [number, number] | null => {
  const canvas = overlayCanvas.value
  if (!canvas) return null
  const rect = canvas.getBoundingClientRect()
  if (!rect.width || !rect.height) return null
  return [
    Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
    Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
  ]
}
const beginDrawing = (mode: 'line' | 'region') => { drawingMode.value = mode; drawingPoints.value = []; redrawCanvas() }
const handleCanvasPointerDown = (event: PointerEvent) => {
  if (!drawingMode.value) return
  const point = normalizedPoint(event)
  if (!point) return
  if (drawingMode.value === 'line') {
    drawingPoints.value = [...drawingPoints.value, point].slice(-2)
    if (drawingPoints.value.length === 2) finishLine()
  } else if (event.detail < 2) {
    drawingPoints.value = [...drawingPoints.value, point]
  }
  redrawCanvas()
}
const finishLine = () => {
  if (drawingPoints.value.length !== 2) return
  const [start, end] = drawingPoints.value
  const current = JSON.parse(lineText.value || '{}')
  lineText.value = JSON.stringify({ ...current, coordinate_space: 'normalized', points: [start, end], direction: current.direction || 'inside_to_outside', inside_side: current.inside_side || 'left' }, null, 2)
  drawingMode.value = null
}
const finishRegion = () => {
  if (drawingMode.value !== 'region' || drawingPoints.value.length < 3) return
  const current = JSON.parse(regionText.value || '{}')
  regionText.value = JSON.stringify({ ...current, coordinate_space: 'normalized', polygon: drawingPoints.value, hold_seconds: Number(current.hold_seconds || 600) }, null, 2)
  drawingMode.value = null
  redrawCanvas()
}
const clearDrawing = () => {
  if (selectedAlgorithm.value === 'truck_departure') lineText.value = '{}'
  if (selectedAlgorithm.value === 'room_empty') regionText.value = '{}'
  drawingPoints.value = []
  redrawCanvas()
}
const resizeCanvas = () => {
  const canvas = overlayCanvas.value
  const video = previewVideo.value
  if (!canvas) return
  const width = video?.videoWidth || canvas.clientWidth || 1280
  const height = video?.videoHeight || canvas.clientHeight || 720
  canvas.width = width
  canvas.height = height
  canvasDebug('resize', {
    source: video?.videoWidth && video?.videoHeight ? 'video' : canvas.clientWidth ? 'css-layout' : 'fallback',
    videoWidth: video?.videoWidth || 0,
    videoHeight: video?.videoHeight || 0,
    clientWidth: canvas.clientWidth,
    clientHeight: canvas.clientHeight,
    canvasWidth: canvas.width,
    canvasHeight: canvas.height,
  })
  redrawCanvas()
}
const drawShape = (context: CanvasRenderingContext2D, points: Array<[number, number]>, close = false) => {
  if (!points.length) return
  const canvas = overlayCanvas.value
  if (!canvas) return
  context.beginPath()
  points.forEach(([x, y], index) => { const px = x * canvas.width; const py = y * canvas.height; if (index === 0) context.moveTo(px, py); else context.lineTo(px, py) })
  if (close) context.closePath()
  context.stroke()
}
const parseGeometryPoints = (value: unknown, canvasWidth = 1, canvasHeight = 1): Array<[number, number]> => {
  if (!value || typeof value !== 'object') return []
  const config = value as Record<string, unknown>
  const polygon = Array.isArray(config.polygon) ? config.polygon : null
  const points = Array.isArray(config.points) ? config.points : null
  const line = Array.isArray(config.line) ? config.line : null
  const lineStart = Array.isArray(config.line_start) ? config.line_start : null
  const lineEnd = Array.isArray(config.line_end) ? config.line_end : null
  const rawPoints = points || polygon || line || (lineStart && lineEnd ? [lineStart, lineEnd] : [])
  const coordinateSpace = String(config.coordinate_space || 'normalized').toLowerCase()
  if (!['normalized', 'relative', 'ratio', 'pixel', 'pixels'].includes(coordinateSpace)) return []
  return rawPoints.flatMap((point): Array<[number, number]> => {
    if (!Array.isArray(point) || point.length !== 2) return []
    const x = Number(point[0])
    const y = Number(point[1])
    if (!Number.isFinite(x) || !Number.isFinite(y)) return []
    if (coordinateSpace === 'pixel' || coordinateSpace === 'pixels') {
      return canvasWidth > 0 && canvasHeight > 0 ? [[x / canvasWidth, y / canvasHeight]] : []
    }
    return [[x, y]]
  })
}
const redrawCanvas = () => {
  const canvas = overlayCanvas.value
  if (!canvas) return
  const context = canvas.getContext('2d')
  if (!context) return
  context.clearRect(0, 0, canvas.width, canvas.height)
  context.lineWidth = Math.max(2, canvas.width / 500)
  context.strokeStyle = '#f59e0b'
  let line: Record<string, unknown> = {}
  let region: Record<string, unknown> = {}
  try { line = JSON.parse(lineText.value || '{}') } catch (error) { canvasDebug('config-parse-error', { field: 'line', error: String(error) }) }
  try { region = JSON.parse(regionText.value || '{}') } catch (error) { canvasDebug('config-parse-error', { field: 'region', error: String(error) }) }
  const linePoints = parseGeometryPoints(line, canvas.width, canvas.height)
  const regionPoints = parseGeometryPoints(region, canvas.width, canvas.height)
  canvasDebug('redraw', {
    canvasWidth: canvas.width,
    canvasHeight: canvas.height,
    linePoints: linePoints.length,
    regionPoints: regionPoints.length,
    drawingPoints: drawingPoints.value.length,
  })
  if (linePoints.length === 2) drawShape(context, linePoints, false)
  if (regionPoints.length >= 3) { context.strokeStyle = REGION_COLOR; drawShape(context, regionPoints, true) }
  if (drawingPoints.value.length) { context.strokeStyle = '#38bdf8'; drawShape(context, drawingPoints.value, drawingMode.value === 'region' && drawingPoints.value.length >= 3) }
}

const waitForIceComplete = (peerConnection: RTCPeerConnection, timeoutMs = 5000) => {
  if (peerConnection.iceGatheringState === 'complete') return Promise.resolve()
  return new Promise<void>((resolve, reject) => {
    let timer = 0
    const cleanup = () => {
      peerConnection.removeEventListener('icegatheringstatechange', handleStateChange)
      window.clearTimeout(timer)
    }
    const handleStateChange = () => {
      if (peerConnection.iceGatheringState !== 'complete') return
      cleanup()
      resolve()
    }
    peerConnection.addEventListener('icegatheringstatechange', handleStateChange)
    timer = window.setTimeout(() => {
      cleanup()
      reject(new Error('WebRTC ICE 候选收集超时'))
    }, timeoutMs)
  })
}

const attachRemoteStream = (video: HTMLVideoElement, remoteStream: MediaStream, event: RTCTrackEvent) => {
  const stream = event.streams?.[0]
  if (stream) video.srcObject = stream
  else if (!remoteStream.getTracks().some((track) => track.id === event.track.id)) remoteStream.addTrack(event.track)
  void video.play().catch(() => undefined)
}

const waitForVideoFrame = (video: HTMLVideoElement, timeoutMs = 5000) => {
  if (video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) return Promise.resolve()
  return new Promise<void>((resolve, reject) => {
    let timer = 0
    const cleanup = () => {
      video.removeEventListener('loadeddata', handleReady)
      video.removeEventListener('playing', handleReady)
      video.removeEventListener('error', handleError)
      window.clearTimeout(timer)
    }
    const handleReady = () => {
      if (video.readyState < 2 || video.videoWidth <= 0 || video.videoHeight <= 0) return
      cleanup()
      resolve()
    }
    const handleError = () => {
      cleanup()
      reject(new Error('视频流未收到首帧'))
    }
    video.addEventListener('loadeddata', handleReady)
    video.addEventListener('playing', handleReady)
    video.addEventListener('error', handleError, { once: true })
    timer = window.setTimeout(() => {
      cleanup()
      reject(new Error('视频流未收到首帧'))
    }, timeoutMs)
  })
}

const clearPreviewPlayback = () => {
  if (previewPeerConnection) {
    previewPeerConnection.ontrack = null
    previewPeerConnection.close()
    previewPeerConnection = null
  }
  const video = previewVideo.value
  if (video) {
    video.pause()
    video.srcObject = null
    video.removeAttribute('src')
    video.load()
  }
  previewPlaying.value = false
}

const stopPreview = () => {
  previewAttemptId += 1
  clearPreviewPlayback()
}

const hasBrowserPreviewUrls = (urls: Record<string, string> | null | undefined) => Boolean(
  urls?.browser_webrtc_api || urls?.browser_webrtc_api_url || urls?.webrtc_api ||
  urls?.browser_hls || urls?.browser_hls_url || urls?.hls,
)

const browserPreviewUrlsFrom = (result: any) => (
  result?.browser_urls || result?.browser_play_urls || result?.final_browser_stream ||
  result?.final_browser_play_urls || result?.browser_stream || {}
)

const ensureBrowserPreviewUrls = async (channelId: string) => {
  // Recreate the browser transcode session so a stale cached ZL URL cannot be reused.
  const started = await startElectronicSealChannelPlay(channelId)
  const urls = browserPreviewUrlsFrom(started)
  if (!hasBrowserPreviewUrls(urls)) {
    throw new Error('浏览器转码流未就绪，请检查原始 RTSP 流')
  }
  return urls
}

const startPreview = async () => {
  if (!selectedChannelId.value) return
  const attemptId = ++previewAttemptId
  previewLoading.value = true
  previewError.value = ''
  clearPreviewPlayback()
  try {
    const urls = await ensureBrowserPreviewUrls(selectedChannelId.value)
    const video = previewVideo.value
    const webrtcApiUrl = urls.browser_webrtc_api || urls.browser_webrtc_api_url || urls.webrtc_api || ''
    const hlsUrl = urls.browser_hls || urls.browser_hls_url || urls.hls || ''
    if (!video || (!webrtcApiUrl && !hlsUrl)) throw new Error('当前没有可用的浏览器预览流')

    const playHlsFallback = async () => {
      if (!hlsUrl || attemptId !== previewAttemptId) throw new Error('没有可用的 HLS 回退地址')
      video.srcObject = null
      video.src = hlsUrl
      video.load()
      await video.play()
      await waitForVideoFrame(video)
    }

    try {
      if (!webrtcApiUrl) throw new Error('没有可用的 WebRTC API 地址')
      const peerConnection = new RTCPeerConnection({ iceServers: [] })
      previewPeerConnection = peerConnection
      const remoteStream = new MediaStream()
      video.srcObject = remoteStream
      peerConnection.addTransceiver('video', { direction: 'recvonly' })
      peerConnection.addTransceiver('audio', { direction: 'recvonly' })
      peerConnection.ontrack = (event) => {
        if (attemptId !== previewAttemptId) return
        attachRemoteStream(video, remoteStream, event)
      }

      const offer = await peerConnection.createOffer()
      await peerConnection.setLocalDescription(offer)
      await waitForIceComplete(peerConnection)
      const response = await fetch(webrtcApiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: peerConnection.localDescription?.sdp || offer.sdp || '',
      })
      if (!response.ok) throw new Error(await response.text())
      const answer = await response.json()
      if (answer.code !== 0 || !answer.sdp) throw new Error(answer.msg || 'WebRTC 应答 SDP 缺失')
      if (attemptId !== previewAttemptId) return
      await peerConnection.setRemoteDescription({ type: answer.type || 'answer', sdp: answer.sdp })
      await video.play()
      await waitForVideoFrame(video)
    } catch (webrtcError) {
      if (attemptId !== previewAttemptId) return
      clearPreviewPlayback()
      await playHlsFallback()
      previewError.value = `WebRTC 播放失败，已切换到 HLS：${webrtcError instanceof Error ? webrtcError.message : String(webrtcError)}`
    }

    if (attemptId !== previewAttemptId) return
    previewPlaying.value = true
    resizeCanvas()
  } catch (error: any) {
    if (attemptId !== previewAttemptId) return
    previewError.value = error?.response?.data?.detail || error?.message || '预览加载失败'
    showMessage('error', previewError.value)
  } finally { previewLoading.value = false }
}
onMounted(async () => { await load(); previewResizeObserver = new ResizeObserver(resizeCanvas); if (overlayCanvas.value) previewResizeObserver.observe(overlayCanvas.value); pollTimer = window.setInterval(() => { void refreshStatus(); void loadEvents() }, 3000) })
onBeforeUnmount(() => { if (pollTimer) window.clearInterval(pollTimer); previewResizeObserver?.disconnect(); stopPreview() })
</script>

<style scoped>
.electronic-seal-page { display: grid; gap: 1rem; }
.seal-header, .control-panel, .preview-card, .config-card, .events-card, .retry-card { background: #fff; border: 1px solid #dbe4ee; border-radius: 10px; padding: 1.2rem; box-shadow: 0 8px 20px rgba(15, 23, 42, .05); }
.seal-header, .control-panel, .card-title-row, .action-row, .retry-card { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.eyebrow { color: #0f766e; font-size: .72rem; letter-spacing: .08em; }
h2, h3, p { margin: 0; } h2 { margin-top: .3rem; color: #0f172a; } p { color: #64748b; line-height: 1.55; }
.control-panel label, .form-grid label, .json-grid label, .retry-card label { display: grid; gap: .35rem; color: #475569; font-size: .84rem; }
select, input, textarea { font: inherit; border: 1px solid #cbd5e1; border-radius: 6px; padding: .55rem; min-width: 190px; } textarea { width: 100%; resize: vertical; font-family: ui-monospace, Consolas, monospace; }
.algorithm-tabs { display: flex; gap: .5rem; flex-wrap: wrap; align-self: end; }
.tab-btn, .primary-btn, .secondary-btn, .ghost-btn, .mini-btn { border: 1px solid #cbd5e1; border-radius: 6px; padding: .55rem .8rem; cursor: pointer; font: inherit; }
.tab-btn { background: #fff; color: #334155; } .tab-btn.active, .primary-btn { background: #0f766e; color: #fff; border-color: #0f766e; } .secondary-btn { background: #334155; color: #fff; border-color: #334155; } .ghost-btn, .mini-btn { background: #fff; color: #334155; }
button:disabled { opacity: .55; cursor: not-allowed; }
.status-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .75rem; } .status-strip > div, .state-list > div { display: grid; gap: .25rem; background: #f8fafc; padding: .8rem; border-radius: 6px; } .status-strip span, .state-list span { color: #64748b; font-size: .78rem; } .status-strip strong, .state-list strong { color: #0f172a; overflow-wrap: anywhere; }
.config-grid { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(260px, .7fr); gap: 1rem; } .config-card { display: grid; gap: 1rem; } .config-card h3 { color: #0f172a; } .card-title-row p { font-size: .8rem; }
.preview-card { display: grid; gap: .8rem; } .preview-stage { position: relative; width: 100%; aspect-ratio: 16 / 9; overflow: hidden; background: #0f172a; border-radius: 6px; isolation: isolate; } .preview-video { position: absolute; inset: 0; z-index: 1; width: 100%; height: 100%; object-fit: contain; } .preview-overlay { position: absolute; inset: 0; z-index: 2; width: 100%; height: 100%; object-fit: contain; pointer-events: none; } .preview-overlay.editable { pointer-events: auto; cursor: crosshair; } .preview-placeholder { position: absolute; inset: 0; z-index: 3; display: grid; place-items: center; color: #cbd5e1; pointer-events: none; } .preview-error { color: #b91c1c; font-size: .82rem; overflow-wrap: anywhere; } .draw-toolbar { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; } .draw-toolbar .active { border-color: #0f766e; color: #0f766e; background: #f0fdfa; } .draw-hint { color: #64748b; font-size: .82rem; } .preview-actions { margin-left: auto; }
.switch-label { display: inline-flex; align-items: center; gap: .4rem; color: #475569; } .form-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .7rem; } .json-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; }
.state-list { display: grid; gap: .6rem; } .hint { padding: .8rem; background: #f0fdfa; border: 1px solid #99f6e4; border-radius: 6px; }
.status-pill { border-radius: 999px; padding: .3rem .6rem; font-size: .8rem; } .status-pill.ok { background: #dcfce7; color: #166534; } .status-pill.muted { background: #e2e8f0; color: #475569; }
.event-table-wrap { overflow: auto; } .event-table { width: 100%; border-collapse: collapse; min-width: 800px; } .event-table th, .event-table td { text-align: left; padding: .75rem; border-bottom: 1px solid #eef2f7; vertical-align: top; } .event-table th { color: #475569; } .evidence-links { display: flex; gap: .6rem; } a { color: #0f766e; }
.message { padding: .75rem 1rem; border-radius: 6px; } .message.success { background: #ecfdf5; color: #047857; } .message.error { background: #fef2f2; color: #b91c1c; } .empty-state { padding: 2rem; text-align: center; color: #64748b; background: #f8fafc; border-radius: 6px; } .empty-state.compact { padding: 1rem; }
@media (max-width: 900px) { .config-grid { grid-template-columns: 1fr; } .status-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); } .form-grid, .json-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 600px) { .status-strip, .form-grid, .json-grid { grid-template-columns: 1fr; } select, input { min-width: 0; width: 100%; } }
</style>
