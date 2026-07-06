<template>
  <div v-if="visible" class="zone-editor-mask" @click.self="$emit('close')">
    <div class="zone-editor-dialog">
      <aside class="zone-editor-sidebar">
        <div class="zone-editor-header">
          <div>
            <h3>编辑区域</h3>
            <p>{{ channelName || channelId }}</p>
          </div>
          <button class="ghost-btn modal-close-btn" type="button" @click="$emit('close')" aria-label="关闭">×</button>
        </div>

        <div v-if="message" :class="['message-box', messageType]">
          {{ message }}
        </div>

        <div class="sidebar-actions">
          <button class="primary-btn" type="button" @click="createNewZone">新增区域</button>
          <button class="ghost-btn" type="button" @click="refreshSnapshot" :disabled="loading">
            {{ loading ? '刷新中...' : '刷新底图' }}
          </button>
        </div>

        <div class="zone-list-section">
          <div class="section-row">
            <strong>区域列表</strong>
            <span>{{ zones.length }} 个</span>
          </div>

          <div v-if="zones.length" class="zone-list">
            <button
              v-for="zone in zones"
              :key="zone.id"
              type="button"
              :class="['zone-item', selectedZoneId === zone.id ? 'active' : '']"
              @click="selectZone(zone)"
            >
              <span class="zone-color" :style="{ background: zone.color || '#3b82f6' }"></span>
              <span class="zone-name">{{ zone.name }}</span>
            </button>
          </div>

          <div v-else class="inline-muted">
            还没有区域，先点击“新增区域”开始绘制。
          </div>
        </div>

        <div v-if="draftZone" class="editor-form">
          <div class="section-row">
            <strong>{{ isEditingExisting ? '编辑区域' : '新建区域' }}</strong>
            <span>{{ draftZone.coordinates.length }} 个点</span>
          </div>

          <label class="form-block">
            <span>区域名称</span>
            <input v-model.trim="draftZone.name" type="text" placeholder="例如 zone_1" />
          </label>

          <label class="form-block">
            <span>区域颜色</span>
            <input v-model="draftZone.color" type="color" class="color-input" />
          </label>

          <div class="form-block">
            <span>绘制说明</span>
            <p class="hint-text">
              在右侧底图上点击可新增顶点，拖动蓝点可调整位置。至少需要 3 个点才能保存区域。
            </p>
          </div>

          <div class="editor-actions">
            <button class="ghost-btn" type="button" @click="resetDraftPoints" :disabled="!draftZone.coordinates.length">
              重置点位
            </button>
            <button class="primary-btn" type="button" @click="saveZone" :disabled="saving">
              {{ saving ? '保存中...' : '保存区域' }}
            </button>
            <button
              v-if="isEditingExisting"
              class="danger-btn"
              type="button"
              @click="removeZone"
              :disabled="saving"
            >
              删除区域
            </button>
          </div>
        </div>
      </aside>

      <section class="zone-editor-stage">
        <div v-if="!snapshotUrl" class="empty-stage">
          <p>暂无底图，点击“刷新底图”抓取当前通道画面后再编辑区域。</p>
        </div>

        <div v-else ref="stageRef" class="image-stage" @click="handleStageClick">
          <img
            ref="imageRef"
            :src="snapshotUrl"
            alt="区域编辑底图"
            class="stage-image"
            @load="handleImageLoad"
          />
          <svg
            v-if="imageSize.width && imageSize.height"
            class="stage-overlay"
            :viewBox="`0 0 ${imageSize.width} ${imageSize.height}`"
            preserveAspectRatio="none"
          >
            <g v-for="zone in displayZones" :key="zone.renderKey">
              <polygon
                v-if="zone.coordinates.length >= 2"
                :points="toPolygonPoints(zone.coordinates)"
                :fill="withAlpha(zone.color || '#3b82f6', zone.selected ? 0.26 : 0.12)"
                :stroke="zone.color || '#3b82f6'"
                :stroke-width="zone.selected ? 3 : 2"
              />
              <line
                v-if="zone.selected && zone.coordinates.length >= 2"
                v-for="(point, index) in zone.coordinates"
                :key="`${zone.renderKey}-line-${index}`"
                :x1="point[0]"
                :y1="point[1]"
                :x2="zone.coordinates[(index + 1) % zone.coordinates.length]?.[0] ?? point[0]"
                :y2="zone.coordinates[(index + 1) % zone.coordinates.length]?.[1] ?? point[1]"
                :stroke="zone.color || '#3b82f6'"
                stroke-width="2"
                stroke-dasharray="6 4"
              />
              <g
                v-if="zone.selected"
                v-for="(point, index) in zone.coordinates"
                :key="`${zone.renderKey}-point-${index}`"
              >
                <circle
                  :cx="point[0]"
                  :cy="point[1]"
                  r="8"
                  fill="#3b82f6"
                  stroke="#ffffff"
                  stroke-width="2"
                  class="point-handle"
                  @mousedown.stop="startDragPoint(index, $event)"
                />
              </g>
            </g>
          </svg>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  addChannelZone,
  captureChannelSnapshot,
  deleteChannelZone,
  getLatestChannelSnapshot,
  listChannelZones,
  updateChannelZone
} from '@/api/channels'

type ZoneItem = {
  id: string
  name: string
  color?: string
  labels: string[]
  coordinates: number[][]
}

type EditableZone = {
  id?: string
  name: string
  color: string
  labels: string[]
  coordinates: number[][]
}

const props = defineProps<{
  visible: boolean
  channelId: string
  channelName?: string
  modelLabels: string[]
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'updated'): void
}>()

const zones = ref<ZoneItem[]>([])
const selectedZoneId = ref('')
const draftZone = ref<EditableZone | null>(null)
const snapshotUrl = ref('')
const loading = ref(false)
const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')
const stageRef = ref<HTMLDivElement | null>(null)
const imageRef = ref<HTMLImageElement | null>(null)
const imageSize = ref({ width: 0, height: 0 })
const dragState = ref<{ pointIndex: number } | null>(null)

const apiBase = import.meta.env.VITE_API_BASE || '/dev-api'

const isEditingExisting = computed(() => Boolean(draftZone.value?.id))

const displayZones = computed(() => {
  const selectedDraft = draftZone.value
  return zones.value
    .map((zone) => {
      const isSelected = zone.id === selectedZoneId.value
      const source =
        isSelected && selectedDraft?.id === zone.id
          ? {
              ...zone,
              name: selectedDraft.name,
              color: selectedDraft.color,
              labels: [],
              coordinates: selectedDraft.coordinates
            }
          : zone

      return {
        ...source,
        renderKey: zone.id,
        selected: isSelected
      }
    })
    .concat(
      selectedDraft && !selectedDraft.id
        ? [
            {
              id: '__draft__',
              name: selectedDraft.name,
              color: selectedDraft.color,
              labels: [],
              coordinates: selectedDraft.coordinates,
              renderKey: '__draft__',
              selected: true
            }
          ]
        : []
    )
})

const showMessage = (type: 'success' | 'error', text: string) => {
  messageType.value = type
  message.value = text
}

const toSnapshotSrc = (path: string) => `${apiBase}${path}`
const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

const loadLatestSnapshot = async () => {
  if (!props.channelId) return ''
  const result = await getLatestChannelSnapshot(props.channelId)
  return result.snapshot_url ? toSnapshotSrc(result.snapshot_url) : ''
}

const waitForLatestSnapshot = async (previousSnapshotUrl: string) => {
  const deadline = Date.now() + 8000
  while (Date.now() < deadline) {
    const latestSnapshotUrl = await loadLatestSnapshot()
    if (latestSnapshotUrl && (!previousSnapshotUrl || latestSnapshotUrl !== previousSnapshotUrl)) {
      return latestSnapshotUrl
    }
    await sleep(350)
  }
  return ''
}

const normalizeZone = (zone: any): ZoneItem => ({
  id: zone.id,
  name: zone.name,
  color: zone.color || '#3b82f6',
  labels: [],
  coordinates: Array.isArray(zone.coordinates)
    ? zone.coordinates.map((point: any) => [Number(point[0]), Number(point[1])])
    : []
})

const cloneZoneToDraft = (zone?: ZoneItem | null): EditableZone | null => {
  if (!zone) return null
  return {
    id: zone.id,
    name: zone.name,
    color: zone.color || '#3b82f6',
    labels: [],
    coordinates: zone.coordinates.map((point) => [...point])
  }
}

const loadZones = async () => {
  const result = await listChannelZones(props.channelId)
  zones.value = (result.items || []).map(normalizeZone)
}

const refreshSnapshot = async () => {
  loading.value = true
  try {
    const previousSnapshotUrl = snapshotUrl.value
    await captureChannelSnapshot(props.channelId)
    const latestSnapshotUrl = await waitForLatestSnapshot(previousSnapshotUrl)
    if (!latestSnapshotUrl) {
      throw new Error('底图刷新超时，请稍后再试')
    }
    snapshotUrl.value = latestSnapshotUrl
    showMessage('success', '底图已刷新，可以开始编辑区域。')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '抓取底图失败')
  } finally {
    loading.value = false
  }
}

const initialize = async () => {
  if (!props.channelId) return
  loading.value = true
  message.value = ''
  try {
    await loadZones()
    const latestSnapshotUrl = await loadLatestSnapshot()
    if (latestSnapshotUrl) {
      snapshotUrl.value = latestSnapshotUrl
    } else {
      await refreshSnapshot()
    }
    if (zones.value.length) {
      selectZone(zones.value[0])
    } else {
      createNewZone()
    }
  } finally {
    loading.value = false
  }
}

const selectZone = (zone: ZoneItem) => {
  selectedZoneId.value = zone.id
  draftZone.value = cloneZoneToDraft(zone)
}

const createNewZone = () => {
  selectedZoneId.value = ''
  draftZone.value = {
    name: `zone_${zones.value.length + 1}`,
    color: '#3b82f6',
    labels: [],
    coordinates: []
  }
}

const resetDraftPoints = () => {
  if (!draftZone.value) return
  stopDragPoint()
  draftZone.value = {
    ...draftZone.value,
    coordinates: []
  }
  showMessage('success', '已重置当前区域的点位。')
}

const handleImageLoad = () => {
  if (!imageRef.value) return
  imageSize.value = {
    width: imageRef.value.naturalWidth,
    height: imageRef.value.naturalHeight
  }
}

const getImagePoint = (event: MouseEvent) => {
  if (!stageRef.value || !imageSize.value.width || !imageSize.value.height) return null
  const rect = stageRef.value.getBoundingClientRect()
  const x = ((event.clientX - rect.left) / rect.width) * imageSize.value.width
  const y = ((event.clientY - rect.top) / rect.height) * imageSize.value.height
  return [
    Math.max(0, Math.min(imageSize.value.width, x)),
    Math.max(0, Math.min(imageSize.value.height, y))
  ] as number[]
}

const handleStageClick = (event: MouseEvent) => {
  if (!draftZone.value) return
  const point = getImagePoint(event)
  if (!point) return
  draftZone.value.coordinates = [...draftZone.value.coordinates, point]
}

const startDragPoint = (pointIndex: number, event: MouseEvent) => {
  dragState.value = { pointIndex }
  window.addEventListener('mousemove', onDragMove)
  window.addEventListener('mouseup', stopDragPoint)
  event.preventDefault()
}

const onDragMove = (event: MouseEvent) => {
  if (!dragState.value || !draftZone.value) return
  const point = getImagePoint(event)
  if (!point) return
  draftZone.value.coordinates = draftZone.value.coordinates.map((item, index) =>
    index === dragState.value?.pointIndex ? point : item
  )
}

const stopDragPoint = () => {
  dragState.value = null
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', stopDragPoint)
}

const saveZone = async () => {
  if (!draftZone.value) return
  if (draftZone.value.name.trim().length < 2) {
    showMessage('error', '区域名称至少需要 2 个字符。')
    return
  }
  if (draftZone.value.coordinates.length < 3) {
    showMessage('error', '区域至少需要 3 个点。')
    return
  }

  saving.value = true
  try {
    const payload = {
      name: draftZone.value.name.trim(),
      color: draftZone.value.color,
      labels: [],
      coordinates: draftZone.value.coordinates.map((point) => [Math.round(point[0]), Math.round(point[1])])
    }

    if (draftZone.value.id) {
      await updateChannelZone(props.channelId, draftZone.value.id, payload)
    } else {
      await addChannelZone(props.channelId, payload)
    }

    await loadZones()
    const savedZone = zones.value.find((zone) => zone.name === payload.name) || zones.value[zones.value.length - 1]
    if (savedZone) {
      selectZone(savedZone)
    }
    showMessage('success', '区域已保存。')
    emit('updated')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '保存区域失败')
  } finally {
    saving.value = false
  }
}

const removeZone = async () => {
  if (!draftZone.value?.id) return
  saving.value = true
  try {
    await deleteChannelZone(props.channelId, draftZone.value.id)
    await loadZones()
    if (zones.value.length) {
      selectZone(zones.value[0])
    } else {
      createNewZone()
    }
    showMessage('success', '区域已删除。')
    emit('updated')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '删除区域失败')
  } finally {
    saving.value = false
  }
}

const toPolygonPoints = (points: number[][]) => points.map((point) => `${point[0]},${point[1]}`).join(' ')

const withAlpha = (color: string, alpha: number) => {
  if (!color.startsWith('#')) return color
  const hex = color.replace('#', '')
  if (hex.length !== 6) return color
  const r = Number.parseInt(hex.slice(0, 2), 16)
  const g = Number.parseInt(hex.slice(2, 4), 16)
  const b = Number.parseInt(hex.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      void initialize()
    } else {
      stopDragPoint()
    }
  }
)

onBeforeUnmount(() => {
  stopDragPoint()
})
</script>

<style scoped>
.zone-editor-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.62);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.25rem;
  z-index: 80;
}

.zone-editor-dialog {
  width: min(1300px, 100%);
  max-height: 92vh;
  overflow: hidden;
  background: #fff;
  border-radius: 18px;
  display: grid;
  grid-template-columns: 360px 1fr;
}

.zone-editor-sidebar {
  border-right: 1px solid #e2e8f0;
  padding: 1rem;
  overflow: auto;
  display: grid;
  gap: 1rem;
}

.zone-editor-header {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
}

.zone-editor-header h3 {
  margin: 0;
}

.zone-editor-header p {
  margin: 0.35rem 0 0;
  color: #64748b;
  word-break: break-all;
}

.modal-close-btn {
  min-width: 40px;
  padding: 0.4rem 0.75rem;
  font-size: 1.35rem;
  line-height: 1;
}

.sidebar-actions,
.editor-actions {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.section-row {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  align-items: center;
  color: #334155;
}

.zone-list-section,
.editor-form {
  display: grid;
  gap: 0.85rem;
}

.zone-list {
  display: grid;
  gap: 0.55rem;
}

.zone-item {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  background: #fff;
  padding: 0.8rem 0.9rem;
  cursor: pointer;
  text-align: left;
}

.zone-item.active {
  border-color: #3b82f6;
  background: #eff6ff;
}

.zone-color {
  width: 14px;
  height: 14px;
  border-radius: 999px;
  flex-shrink: 0;
}

.zone-name {
  color: #0f172a;
  font-weight: 600;
}

.form-block {
  display: grid;
  gap: 0.45rem;
}

.form-block > span {
  color: #334155;
  font-weight: 600;
}

.form-block input[type='text'] {
  min-height: 42px;
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  padding: 0.75rem 0.9rem;
  font: inherit;
}

.color-input {
  width: 64px;
  height: 42px;
  padding: 0.2rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  background: #fff;
}

.hint-text,
.inline-muted {
  margin: 0;
  color: #64748b;
  line-height: 1.55;
}

.message-box {
  border-radius: 12px;
  padding: 0.75rem 0.9rem;
  font-size: 0.95rem;
}

.message-box.success {
  background: #ecfdf3;
  color: #027a48;
  border: 1px solid #a6f4c5;
}

.message-box.error {
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}

.zone-editor-stage {
  background: #f8fafc;
  padding: 1rem;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
}

.empty-stage {
  color: #64748b;
  text-align: center;
}

.image-stage {
  position: relative;
  width: min(860px, 100%);
  user-select: none;
}

.stage-image {
  width: 100%;
  height: auto;
  display: block;
  border-radius: 16px;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.12);
}

.stage-overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.point-handle {
  cursor: move;
}

.primary-btn,
.ghost-btn,
.danger-btn {
  min-height: 40px;
  padding: 0.65rem 0.95rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  cursor: pointer;
  font: inherit;
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

.danger-btn {
  background: #b91c1c;
  color: #fff;
  border-color: #b91c1c;
}

@media (max-width: 1024px) {
  .zone-editor-dialog {
    grid-template-columns: 1fr;
  }

  .zone-editor-sidebar {
    border-right: none;
    border-bottom: 1px solid #e2e8f0;
  }
}
</style>
