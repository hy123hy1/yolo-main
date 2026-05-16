<template>
  <div class="events-page">
    <section class="toolbar-panel">
      <div class="toolbar-row">
        <label class="toolbar-select-wrap">
          <select
            v-model="filters.channel_id"
            :class="['toolbar-input', 'toolbar-select', { empty: !filters.channel_id }]"
          >
            <option value="">筛选通道</option>
            <option v-for="channel in channelOptions" :key="channel.id" :value="channel.id">
              {{ channel.name || channel.id }}
            </option>
          </select>
          <span class="select-arrow">∨</span>
        </label>

        <label class="toolbar-select-wrap">
          <select
            v-model="filters.class_name"
            :class="['toolbar-input', 'toolbar-select', { empty: !filters.class_name }]"
          >
            <option value="">筛选类型</option>
            <option v-for="label in classOptions" :key="label" :value="label">
              {{ label }}
            </option>
          </select>
          <span class="select-arrow">∨</span>
        </label>

        <div ref="rangePickerRef" class="range-picker-wrap">
          <button class="toolbar-range" type="button" @click="toggleRangePicker">
            <span :class="['range-display', { empty: !filters.start_date }]">
              {{ filters.start_date || '时间范围' }}
            </span>
            <span class="range-separator">—</span>
            <span :class="['range-display', { empty: !filters.end_date }]">
              {{ filters.end_date || '时间范围' }}
            </span>
            <span class="range-calendar" aria-hidden="true"></span>
          </button>

          <div v-if="showRangePicker" class="range-picker-panel">
            <aside class="range-shortcuts">
              <button
                v-for="item in rangeShortcuts"
                :key="item.key"
                class="shortcut-btn"
                type="button"
                @click="applyShortcut(item.key)"
              >
                {{ item.label }}
              </button>
            </aside>

            <div class="range-calendars">
              <div class="calendar-panel">
                <div class="calendar-header">
                  <div class="calendar-nav">
                    <button class="calendar-nav-btn" type="button" @click="moveCalendar(-1)">‹</button>
                    <button class="calendar-nav-btn" type="button" @click="moveCalendar(-1)">‹</button>
                  </div>
                  <strong>{{ formatCalendarTitle(leftCalendarDate) }}</strong>
                  <span class="calendar-nav-placeholder"></span>
                </div>

                <div class="calendar-weekdays">
                  <span v-for="weekday in weekdays" :key="`left-${weekday}`">{{ weekday }}</span>
                </div>

                <div class="calendar-days">
                  <button
                    v-for="day in leftCalendarDays"
                    :key="`left-${day.date}`"
                    type="button"
                    :class="calendarDayClass(day)"
                    @click="pickRangeDate(day)"
                  >
                    {{ day.day }}
                  </button>
                </div>
              </div>

              <div class="calendar-panel">
                <div class="calendar-header">
                  <span class="calendar-nav-placeholder"></span>
                  <strong>{{ formatCalendarTitle(rightCalendarDate) }}</strong>
                  <div class="calendar-nav">
                    <button class="calendar-nav-btn" type="button" @click="moveCalendar(1)">›</button>
                    <button class="calendar-nav-btn" type="button" @click="moveCalendar(1)">›</button>
                  </div>
                </div>

                <div class="calendar-weekdays">
                  <span v-for="weekday in weekdays" :key="`right-${weekday}`">{{ weekday }}</span>
                </div>

                <div class="calendar-days">
                  <button
                    v-for="day in rightCalendarDays"
                    :key="`right-${day.date}`"
                    type="button"
                    :class="calendarDayClass(day)"
                    @click="pickRangeDate(day)"
                  >
                    {{ day.day }}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <button class="refresh-btn" @click="load" :disabled="loading" title="刷新">
          ↻
        </button>
      </div>
    </section>

    <section v-if="loading" class="empty-state">正在加载事件...</section>
    <section v-else-if="filteredItems.length === 0" class="empty-state">
      暂无事件数据
    </section>
    <template v-else>
      <section class="event-grid">
        <article
          v-for="(item, index) in filteredItems"
          :key="item.id"
          class="event-card"
          @click="openPreview(index)"
        >
          <div class="thumb-wrap">
            <div class="thumb-top-left">
              <span class="class-badge">{{ shortLabel(item.class_name) }}</span>
            </div>
            <div class="thumb-top-right">
              {{ formatPercent(item.conf) }}
            </div>

            <div class="image-frame">
              <img
                v-if="item.snapshot_path"
                class="snapshot"
                :src="getEventSnapshotUrl(item.id)"
                loading="lazy"
                decoding="async"
                alt="事件抓拍图"
                @load="handleImageLoad(item.id, $event)"
              />
              <div v-else class="snapshot placeholder">无抓拍图</div>

              <div
                v-if="item.snapshot_path && getSnapshotBoxes(item).length && imageMetas[item.id]"
                class="overlay"
              >
                <div
                  v-for="(box, boxIndex) in getSnapshotBoxes(item)"
                  :key="`${item.id}-${boxIndex}`"
                  class="detect-box"
                  :style="getBoxStyle(item.id, box)"
                >
                  <span class="detect-label">{{ formatBoxLabel(box) }}</span>
                </div>
              </div>
            </div>
          </div>

          <div class="event-card-body">
            <div class="event-channel">通道: {{ channelName(item.channel_id) }}</div>
            <div class="event-time">{{ formatDateTime(item.created_at) }}</div>
          </div>
        </article>
      </section>

      <section class="pagination-bar">
        <div class="page-summary">
          共 {{ total }} 条，当前第 {{ currentPage }} / {{ totalPages }} 页
        </div>
        <div class="page-actions">
          <button class="page-btn" type="button" @click="goToPage(1)" :disabled="currentPage === 1">
            首页
          </button>
          <button class="page-btn" type="button" @click="goToPage(currentPage - 1)" :disabled="currentPage === 1">
            上一页
          </button>
          <button
            v-for="page in visiblePageNumbers"
            :key="page"
            :class="['page-btn', { active: page === currentPage }]"
            type="button"
            @click="goToPage(page)"
          >
            {{ page }}
          </button>
          <button
            class="page-btn"
            type="button"
            @click="goToPage(currentPage + 1)"
            :disabled="currentPage === totalPages"
          >
            下一页
          </button>
          <button
            class="page-btn"
            type="button"
            @click="goToPage(totalPages)"
            :disabled="currentPage === totalPages"
          >
            末页
          </button>
          <select v-model.number="pageSize" class="page-size-select">
            <option :value="10">10 / 页</option>
            <option :value="20">20 / 页</option>
            <option :value="50">50 / 页</option>
            <option :value="100">100 / 页</option>
          </select>
        </div>
      </section>
    </template>

    <div v-if="previewIndex >= 0 && currentPreview" class="preview-mask" @click.self="closePreview">
      <div class="preview-dialog">
        <button class="close-btn" @click="closePreview" title="关闭" aria-label="关闭">×</button>

        <div class="preview-main">
          <div class="preview-left">
            <div class="preview-image-wrap">
              <img
                v-if="currentPreview.snapshot_path"
                class="preview-image"
                :src="getEventSnapshotUrl(currentPreview.id)"
                alt="事件大图"
                @load="handleImageLoad(currentPreview.id, $event)"
              />
              <div v-else class="preview-image placeholder">无抓拍图</div>

              <div
                v-if="
                  currentPreview.snapshot_path &&
                  getSnapshotBoxes(currentPreview).length &&
                  imageMetas[currentPreview.id]
                "
                class="overlay"
              >
                <div
                  v-for="(box, boxIndex) in getSnapshotBoxes(currentPreview)"
                  :key="`${currentPreview.id}-preview-${boxIndex}`"
                  class="detect-box"
                  :style="getBoxStyle(currentPreview.id, box)"
                >
                  <span class="detect-label">{{ formatBoxLabel(box) }}</span>
                </div>
              </div>

              <button
                v-if="filteredItems.length > 1"
                class="nav-arrow left"
                @click.stop="goPrev"
                title="上一条"
              >
                ‹
              </button>
              <button
                v-if="filteredItems.length > 1"
                class="nav-arrow right"
                @click.stop="goNext"
                title="下一条"
              >
                ›
              </button>
            </div>

            <div v-if="filteredItems.length > 1" class="pager-pill">
              {{ previewIndex + 1 }} / {{ filteredItems.length }}
            </div>
          </div>

          <aside class="preview-side">
            <h3>告警</h3>
            <div class="detail-list">
              <div class="detail-row">
                <span>标签</span>
                <strong>{{ currentPreview.class_name || '-' }}</strong>
              </div>
              <div class="detail-row">
                <span>置信度</span>
                <strong>{{ formatPercent(currentPreview.conf) }}</strong>
              </div>
              <div class="detail-row">
                <span>通道</span>
                <strong>{{ channelName(currentPreview.channel_id) }}</strong>
              </div>
              <div class="detail-row">
                <span>设备</span>
                <strong>{{ deviceNameByChannel(currentPreview.channel_id) }}</strong>
              </div>
              <div class="detail-row">
                <span>事件时间</span>
                <strong>{{ formatDateTime(currentPreview.created_at) }}</strong>
              </div>
              <div class="detail-row">
                <span>模型</span>
                <strong>{{ modelName(currentPreview.model_id) }}</strong>
              </div>
            </div>

            <details v-if="currentPreview.remark" class="remark-box">
              <summary>查看详细信息</summary>
              <pre>{{ prettyRemark(currentPreview.remark) }}</pre>
            </details>
          </aside>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getEventSnapshotUrl, listEventsByQuery } from '@/api/events'
import { listChannels } from '@/api/channels'
import { listDevices } from '@/api/devices'
import { listModels } from '@/api/models'

type EventItem = {
  id: string
  channel_id?: string
  model_id?: string
  class_name?: string
  conf?: number
  status?: string
  created_at?: string
  snapshot_path?: string
  remark?: string
}

type DetectionBox = {
  class_name?: string
  conf?: number
  x1: number
  y1: number
  x2: number
  y2: number
}

type ChannelItem = {
  id: string
  name?: string
  device_id?: string
}

type DeviceItem = {
  id: string
  name?: string
}

type ModelItem = {
  model_id: string
  name?: string
}

type CalendarDay = {
  date: string
  day: number
  inCurrentMonth: boolean
  isToday: boolean
}

const loading = ref(false)
const items = ref<EventItem[]>([])
const channels = ref<ChannelItem[]>([])
const devices = ref<DeviceItem[]>([])
const models = ref<ModelItem[]>([])
const previewIndex = ref(-1)
const imageMetas = ref<Record<string, { width: number; height: number }>>({})
const currentPage = ref(1)
const pageSize = ref(10)
const total = ref(0)
const showRangePicker = ref(false)
const rangePickerRef = ref<HTMLElement | null>(null)
const leftCalendarDate = ref(new Date())
const rangeDraft = ref<{ start: string; end: string }>({
  start: '',
  end: '',
})

const filters = ref({
  channel_id: '',
  class_name: '',
  start_date: '',
  end_date: '',
})

const weekdays = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']
const rangeShortcuts = [
  { key: 'today', label: '今天' },
  { key: 'yesterday', label: '昨天' },
  { key: 'last7', label: '最近7天' },
  { key: 'last30', label: '最近30天' },
] as const

const channelMap = computed(() => {
  const map: Record<string, ChannelItem> = {}
  for (const item of channels.value) {
    map[item.id] = item
  }
  return map
})

const deviceMap = computed(() => {
  const map: Record<string, DeviceItem> = {}
  for (const item of devices.value) {
    map[item.id] = item
  }
  return map
})

const modelMap = computed(() => {
  const map: Record<string, ModelItem> = {}
  for (const item of models.value) {
    map[item.model_id] = item
  }
  return map
})

const classOptions = computed(() => {
  return Array.from(
    new Set(items.value.map((item) => item.class_name).filter((item): item is string => Boolean(item)))
  )
})

const channelOptions = computed(() => channels.value)
const rightCalendarDate = computed(() => addMonths(leftCalendarDate.value, 1))
const leftCalendarDays = computed(() => buildCalendarDays(leftCalendarDate.value))
const rightCalendarDays = computed(() => buildCalendarDays(rightCalendarDate.value))

const filteredItems = computed(() => {
  const start = filters.value.start_date ? new Date(filters.value.start_date).getTime() : null
  const end = filters.value.end_date ? new Date(filters.value.end_date).getTime() : null

  return items.value.filter((item) => {
    if (filters.value.channel_id && item.channel_id !== filters.value.channel_id) return false
    if (filters.value.class_name && item.class_name !== filters.value.class_name) return false

    if (start || end) {
      const current = item.created_at ? new Date(item.created_at).getTime() : null
      if (!current) return false
      if (start && current < start) return false
      if (end && current > end) return false
    }

    return true
  })
})

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

const visiblePageNumbers = computed(() => {
  const total = totalPages.value
  const current = currentPage.value
  const start = Math.max(1, current - 2)
  const end = Math.min(total, start + 4)
  const pages: number[] = []
  for (let page = Math.max(1, end - 4); page <= end; page += 1) {
    pages.push(page)
  }
  return pages
})

const currentPreview = computed(() => {
  if (previewIndex.value < 0) return null
  return filteredItems.value[previewIndex.value] || null
})

const formatDateTime = (value?: string) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const yyyy = date.getFullYear()
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  const hh = String(date.getHours()).padStart(2, '0')
  const mi = String(date.getMinutes()).padStart(2, '0')
  const ss = String(date.getSeconds()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`
}

const formatPercent = (value?: number) => {
  if (typeof value !== 'number') return '-'
  return `${(value * 100).toFixed(0)}%`
}

const prettyRemark = (remark: string) => {
  try {
    return JSON.stringify(JSON.parse(remark), null, 2)
  } catch {
    return remark
  }
}

const parseBoxes = (remark?: string): DetectionBox[] => {
  if (!remark) return []
  try {
    const parsed = JSON.parse(remark)
    const source = Array.isArray(parsed) ? parsed : [parsed]
    return source
      .filter(
        (item) =>
          item &&
          typeof item.x1 === 'number' &&
          typeof item.y1 === 'number' &&
          typeof item.x2 === 'number' &&
          typeof item.y2 === 'number'
      )
      .map((item) => ({
        class_name: item.class_name,
        conf: item.conf,
        x1: item.x1,
        y1: item.y1,
        x2: item.x2,
        y2: item.y2,
      }))
  } catch {
    return []
  }
}

const getSnapshotBoxes = (item: EventItem) => parseBoxes(item.remark)

const formatBoxLabel = (box: DetectionBox) => {
  const name = box.class_name || '目标'
  if (typeof box.conf === 'number') {
    return `${name} ${(box.conf * 100).toFixed(0)}%`
  }
  return name
}

const shortLabel = (label?: string) => {
  if (!label) return '告'
  return label.slice(0, 1)
}

const getBoxStyle = (eventId: string, box: DetectionBox) => {
  const meta = imageMetas.value[eventId]
  if (!meta?.width || !meta?.height) return {}

  const left = (box.x1 / meta.width) * 100
  const top = (box.y1 / meta.height) * 100
  const width = ((box.x2 - box.x1) / meta.width) * 100
  const height = ((box.y2 - box.y1) / meta.height) * 100

  return {
    left: `${left}%`,
    top: `${top}%`,
    width: `${width}%`,
    height: `${height}%`,
  }
}

const handleImageLoad = (eventId: string, event: Event) => {
  const img = event.target as HTMLImageElement | null
  if (!img?.naturalWidth || !img?.naturalHeight) return

  imageMetas.value = {
    ...imageMetas.value,
    [eventId]: {
      width: img.naturalWidth,
      height: img.naturalHeight,
    },
  }
}

const channelName = (channelId?: string) => {
  if (!channelId) return '-'
  return channelMap.value[channelId]?.name || channelId
}

const deviceNameByChannel = (channelId?: string) => {
  if (!channelId) return '-'
  const deviceId = channelMap.value[channelId]?.device_id
  if (!deviceId) return '-'
  return deviceMap.value[deviceId]?.name || deviceId
}

const modelName = (modelId?: string) => {
  if (!modelId) return '-'
  const model = modelMap.value[modelId]
  return model?.name || modelId
}

const pad = (value: number) => String(value).padStart(2, '0')

const formatDateValue = (date: Date) =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`

const parseDateValue = (value?: string) => {
  if (!value) return null
  const date = new Date(`${value}T00:00:00`)
  return Number.isNaN(date.getTime()) ? null : date
}

const addMonths = (date: Date, amount: number) =>
  new Date(date.getFullYear(), date.getMonth() + amount, 1)

const startOfToday = () => {
  const today = new Date()
  return new Date(today.getFullYear(), today.getMonth(), today.getDate())
}

const buildCalendarDays = (monthDate: Date): CalendarDay[] => {
  const firstDay = new Date(monthDate.getFullYear(), monthDate.getMonth(), 1)
  const lastDay = new Date(monthDate.getFullYear(), monthDate.getMonth() + 1, 0)
  const startWeekday = firstDay.getDay()
  const cells: CalendarDay[] = []
  const visibleStart = new Date(firstDay)
  visibleStart.setDate(firstDay.getDate() - startWeekday)
  const today = formatDateValue(startOfToday())

  for (let i = 0; i < 42; i += 1) {
    const current = new Date(visibleStart)
    current.setDate(visibleStart.getDate() + i)
    cells.push({
      date: formatDateValue(current),
      day: current.getDate(),
      inCurrentMonth:
        current.getMonth() === monthDate.getMonth() && current.getFullYear() === monthDate.getFullYear(),
      isToday: formatDateValue(current) === today,
    })
  }

  return cells
}

const formatCalendarTitle = (date: Date) =>
  date.toLocaleString('en-US', { month: 'short', year: 'numeric' })

const toggleRangePicker = () => {
  showRangePicker.value = !showRangePicker.value
  if (showRangePicker.value) {
    rangeDraft.value = {
      start: filters.value.start_date,
      end: filters.value.end_date,
    }
    const baseDate = parseDateValue(filters.value.start_date) || startOfToday()
    leftCalendarDate.value = new Date(baseDate.getFullYear(), baseDate.getMonth(), 1)
  }
}

const moveCalendar = (step: number) => {
  leftCalendarDate.value = addMonths(leftCalendarDate.value, step)
}

const pickRangeDate = (day: CalendarDay) => {
  if (!rangeDraft.value.start || (rangeDraft.value.start && rangeDraft.value.end)) {
    rangeDraft.value = {
      start: day.date,
      end: '',
    }
    return
  }

  if (day.date < rangeDraft.value.start) {
    rangeDraft.value = {
      start: day.date,
      end: rangeDraft.value.start,
    }
  } else {
    rangeDraft.value = {
      start: rangeDraft.value.start,
      end: day.date,
    }
  }

  filters.value.start_date = rangeDraft.value.start
  filters.value.end_date = rangeDraft.value.end
  showRangePicker.value = false
}

const calendarDayClass = (day: CalendarDay) => {
  const start = rangeDraft.value.start
  const end = rangeDraft.value.end
  const inRange = Boolean(start && end && day.date >= start && day.date <= end)
  return [
    'calendar-day',
    {
      outside: !day.inCurrentMonth,
      today: day.isToday,
      selected: day.date === start || day.date === end,
      'in-range': inRange,
    },
  ]
}

const applyShortcut = (key: (typeof rangeShortcuts)[number]['key']) => {
  const today = startOfToday()
  let start = new Date(today)
  let end = new Date(today)

  if (key === 'yesterday') {
    start.setDate(start.getDate() - 1)
    end = new Date(start)
  }

  if (key === 'last7') {
    start.setDate(start.getDate() - 6)
  }

  if (key === 'last30') {
    start.setDate(start.getDate() - 29)
  }

  const startValue = formatDateValue(start)
  const endValue = formatDateValue(end)
  filters.value.start_date = startValue
  filters.value.end_date = endValue
  rangeDraft.value = {
    start: startValue,
    end: endValue,
  }
  leftCalendarDate.value = new Date(start.getFullYear(), start.getMonth(), 1)
  showRangePicker.value = false
}

const handleDocumentClick = (event: MouseEvent) => {
  const target = event.target as Node | null
  if (!showRangePicker.value || !rangePickerRef.value || !target) return
  if (!rangePickerRef.value.contains(target)) {
    showRangePicker.value = false
  }
}

const load = async () => {
  loading.value = true
  try {
    const [eventResult, channelResult, deviceResult, modelResult] = await Promise.all([
      listEventsByQuery({
        channel_id: filters.value.channel_id || undefined,
        class_name: filters.value.class_name || undefined,
        page: currentPage.value,
        page_size: pageSize.value,
      }),
      listChannels(),
      listDevices(),
      listModels(),
    ])
    items.value = eventResult.items || []
    total.value = Number(eventResult.total || 0)
    channels.value = channelResult.items || []
    devices.value = deviceResult.items || []
    models.value = modelResult.items || []

    if (currentPage.value > totalPages.value) {
      currentPage.value = totalPages.value
    }
    if (previewIndex.value >= filteredItems.value.length) {
      previewIndex.value = filteredItems.value.length - 1
    }
  } finally {
    loading.value = false
  }
}

const openPreview = (index: number) => {
  previewIndex.value = index
}

const closePreview = () => {
  previewIndex.value = -1
}

const goToPage = (page: number) => {
  const nextPage = Math.min(Math.max(page, 1), totalPages.value)
  currentPage.value = nextPage
  previewIndex.value = -1
  void load()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

const goPrev = () => {
  if (!filteredItems.value.length) return
  previewIndex.value =
    (previewIndex.value - 1 + filteredItems.value.length) % filteredItems.value.length
}

const goNext = () => {
  if (!filteredItems.value.length) return
  previewIndex.value = (previewIndex.value + 1) % filteredItems.value.length
}

onMounted(() => {
  void load()
  document.addEventListener('click', handleDocumentClick)
})

watch(
  () => [filters.value.channel_id, filters.value.class_name, filters.value.start_date, filters.value.end_date],
  () => {
    currentPage.value = 1
    previewIndex.value = -1
    void load()
  }
)

watch(pageSize, () => {
  currentPage.value = 1
  previewIndex.value = -1
  void load()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<style scoped>
.events-page {
  display: grid;
  gap: 1rem;
}

.toolbar-panel {
  background: #fff;
  border: 1px solid #e6ebf2;
  border-radius: 16px;
  padding: 1rem;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.04);
}

.toolbar-row {
  display: grid;
  grid-template-columns: minmax(160px, 180px) minmax(140px, 160px) minmax(280px, 1fr) 46px;
  gap: 0.75rem;
  align-items: center;
}

.toolbar-select-wrap {
  position: relative;
  display: block;
}

.range-picker-wrap {
  position: relative;
}

.toolbar-input {
  min-height: 44px;
  padding: 0.72rem 2.1rem 0.72rem 0.95rem;
  border-radius: 8px;
  border: 1px solid #d8dee6;
  background: #fff;
  color: #475569;
  font: inherit;
  width: 100%;
  box-sizing: border-box;
}

.toolbar-input.empty,
.range-input::placeholder {
  color: #b7c0cb;
}

.toolbar-select {
  appearance: none;
  -webkit-appearance: none;
  -moz-appearance: none;
  cursor: pointer;
}

.toolbar-select option {
  color: #334155;
  background: #fff;
}

.select-arrow {
  position: absolute;
  right: 0.9rem;
  top: 50%;
  transform: translateY(-48%);
  color: #a5afbb;
  pointer-events: none;
  font-size: 0.72rem;
  line-height: 1;
}

.toolbar-range {
  min-height: 44px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.55rem;
  padding: 0 0.9rem;
  border-radius: 8px;
  border: 1px solid #d8dee6;
  background: #fff;
  width: 100%;
  cursor: pointer;
  text-align: left;
}

.range-display {
  color: #475569;
  font: inherit;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.range-display.empty {
  color: #b7c0cb;
}

.range-separator,
.range-calendar {
  color: #b7c0cb;
}

.range-calendar {
  width: 16px;
  height: 16px;
  border: 1.5px solid #b7c0cb;
  border-radius: 3px;
  position: relative;
  box-sizing: border-box;
}

.range-calendar::before,
.range-calendar::after {
  content: '';
  position: absolute;
  left: 2px;
  right: 2px;
  background: #b7c0cb;
}

.range-calendar::before {
  top: 3px;
  height: 1.5px;
}

.range-calendar::after {
  top: -1px;
  height: 3px;
  border-radius: 2px 2px 0 0;
}

.range-picker-panel {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: 20;
  display: grid;
  grid-template-columns: 120px minmax(560px, 1fr);
  background: #fff;
  border: 1px solid #d8dee6;
  border-radius: 12px;
  box-shadow: 0 18px 38px rgba(15, 23, 42, 0.14);
  overflow: hidden;
}

.range-shortcuts {
  display: grid;
  align-content: start;
  background: #fafafa;
  border-right: 1px solid #eceff3;
}

.shortcut-btn {
  min-height: 44px;
  padding: 0 1rem;
  border: none;
  border-bottom: 1px solid #eceff3;
  background: transparent;
  color: #334155;
  text-align: left;
  font: inherit;
  cursor: pointer;
}

.shortcut-btn:hover {
  background: #f1f5f9;
}

.range-calendars {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.calendar-panel {
  display: grid;
  gap: 0.6rem;
  padding: 0.9rem 1rem 1rem;
}

.calendar-panel + .calendar-panel {
  border-left: 1px solid #eceff3;
}

.calendar-header {
  display: grid;
  grid-template-columns: 64px 1fr 64px;
  align-items: center;
  gap: 0.5rem;
  color: #334155;
}

.calendar-header strong {
  text-align: center;
  font-weight: 600;
}

.calendar-nav {
  display: flex;
  gap: 0.15rem;
}

.calendar-nav-btn {
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 1rem;
}

.calendar-nav-placeholder {
  display: block;
}

.calendar-weekdays,
.calendar-days {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 0.25rem;
}

.calendar-weekdays span {
  text-align: center;
  color: #475569;
  font-size: 0.88rem;
  padding: 0.2rem 0;
}

.calendar-day {
  height: 34px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #334155;
  font: inherit;
  cursor: pointer;
}

.calendar-day.outside {
  color: #c0c8d2;
}

.calendar-day.today {
  box-shadow: inset 0 0 0 1px #94a3b8;
}

.calendar-day.in-range {
  background: #e8f1ff;
}

.calendar-day.selected {
  background: #ffffff;
  box-shadow: inset 0 0 0 1px #334155;
}

.refresh-btn {
  min-height: 44px;
  width: 46px;
  border-radius: 8px;
  border: 1px solid #d8dee6;
  background: #fff;
  color: #64748b;
  font: inherit;
}

.refresh-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 1.1rem;
}

.event-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 1rem;
}

.pagination-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 0.9rem 1rem;
}

.page-summary {
  color: #64748b;
  font-size: 0.95rem;
}

.page-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.page-btn,
.page-size-select {
  min-height: 38px;
  padding: 0.55rem 0.8rem;
  border-radius: 10px;
  border: 1px solid #d8dee6;
  background: #fff;
  color: #334155;
  font: inherit;
}

.page-btn {
  cursor: pointer;
}

.page-size-select {
  width: 108px;
  min-width: 108px;
  max-width: 108px;
  padding: 0.45rem 1.8rem 0.45rem 0.6rem;
  font-size: 0.9rem;
}

.page-btn.active {
  background: #1d4ed8;
  color: #fff;
  border-color: #1d4ed8;
}

.page-btn:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.event-card {
  background: #fff;
  border: 1px solid #e5eaf1;
  border-radius: 14px;
  overflow: hidden;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05);
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.event-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.09);
}

.thumb-wrap {
  position: relative;
}

.thumb-top-left,
.thumb-top-right {
  position: absolute;
  top: 8px;
  z-index: 3;
}

.thumb-top-left {
  left: 8px;
}

.thumb-top-right {
  right: 8px;
  padding: 0.2rem 0.55rem;
  border-radius: 8px;
  background: #10b981;
  color: #fff;
  font-size: 0.9rem;
  font-weight: 700;
}

.class-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 28px;
  padding: 0 0.4rem;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.82);
  color: #fff;
  font-size: 0.95rem;
  font-weight: 700;
}

.image-frame {
  position: relative;
  background: #111827;
  overflow: hidden;
}

.snapshot {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  display: block;
}

.event-card-body {
  padding: 0.8rem 0.9rem 0.9rem;
  display: grid;
  gap: 0.45rem;
}

.event-channel {
  color: #1e293b;
  font-weight: 600;
}

.event-time {
  color: #94a3b8;
  font-size: 0.95rem;
}

.overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.detect-box {
  position: absolute;
  border: 2px solid #ff3b30;
  box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.25) inset;
}

.detect-label {
  position: absolute;
  left: 0;
  top: 0;
  transform: translateY(-100%);
  padding: 0.12rem 0.35rem;
  background: #ff3b30;
  color: #fff;
  font-size: 0.68rem;
  line-height: 1.2;
  white-space: nowrap;
}

.preview-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.25rem;
  z-index: 80;
}

.preview-dialog {
  position: relative;
  width: min(1240px, 100%);
  min-height: min(760px, 90vh);
  background: #fff;
  border-radius: 22px;
  padding: 1.25rem;
  box-shadow: 0 30px 60px rgba(15, 23, 42, 0.18);
}

.close-btn {
  position: absolute;
  top: 16px;
  right: 16px;
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 999px;
  background: #8b8b8b;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 1.8rem;
  line-height: 1;
  padding: 0;
  cursor: pointer;
}

.preview-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 290px;
  gap: 1.25rem;
  align-items: stretch;
  height: 100%;
}

.preview-left {
  display: grid;
  gap: 1rem;
}

.preview-image-wrap {
  position: relative;
  background: #fff;
  min-height: 560px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.preview-image {
  width: 100%;
  max-height: 72vh;
  object-fit: contain;
  display: block;
}

.nav-arrow {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 52px;
  height: 52px;
  border: none;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.78);
  color: #fff;
  font-size: 2rem;
  line-height: 1;
  cursor: pointer;
}

.nav-arrow.left {
  left: 16px;
}

.nav-arrow.right {
  right: 16px;
}

.pager-pill {
  margin: 0 auto;
  padding: 0.45rem 0.9rem;
  border-radius: 999px;
  background: #7c7c7c;
  color: #fff;
  font-weight: 700;
}

.preview-side {
  padding-top: 0.4rem;
}

.preview-side h3 {
  margin: 0 0 0.9rem;
  font-size: 2rem;
  color: #0f172a;
}

.detail-list {
  border-top: 1px solid #e2e8f0;
  padding-top: 0.75rem;
  display: grid;
  gap: 0.95rem;
}

.detail-row {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 0.75rem;
  align-items: start;
}

.detail-row span {
  color: #64748b;
}

.detail-row strong {
  color: #0f172a;
  font-weight: 500;
  word-break: break-word;
}

.remark-box {
  margin-top: 1.25rem;
}

.remark-box summary {
  cursor: pointer;
  color: #475569;
}

.remark-box pre {
  margin: 0.75rem 0 0;
  padding: 0.9rem;
  border-radius: 12px;
  background: #0f172a;
  color: #e2e8f0;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.empty-state {
  border-radius: 14px;
  padding: 1rem 1.1rem;
  background: #fff;
  color: #64748b;
  border: 1px solid #e2e8f0;
}

.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #94a3b8;
  min-height: 180px;
}

@media (max-width: 1080px) {
  .preview-main {
    grid-template-columns: 1fr;
  }

  .toolbar-row {
    grid-template-columns: 1fr;
  }

  .range-picker-panel {
    grid-template-columns: 1fr;
    min-width: 100%;
  }

  .range-shortcuts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    border-right: none;
    border-bottom: 1px solid #eceff3;
  }

  .range-calendars {
    grid-template-columns: 1fr;
  }

  .calendar-panel + .calendar-panel {
    border-left: none;
    border-top: 1px solid #eceff3;
  }

  .preview-side {
    padding-top: 0;
  }
}

@media (max-width: 768px) {
  .event-grid {
    grid-template-columns: 1fr;
  }

  .preview-dialog {
    padding: 0.9rem;
  }

  .preview-image-wrap {
    min-height: 320px;
  }

  .detail-row {
    grid-template-columns: 1fr;
    gap: 0.25rem;
  }
}
</style>
