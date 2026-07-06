<template>
  <div class="recordings-page">
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
            v-model="filters.event_type"
            :class="['toolbar-input', 'toolbar-select', { empty: !filters.event_type }]"
          >
            <option value="">筛选类型</option>
            <option v-for="type in eventTypeOptions" :key="type" :value="type">
              {{ formatEventType(type) }}
            </option>
          </select>
          <span class="select-arrow">∨</span>
        </label>

        <div ref="rangePickerRef" class="range-picker-wrap">
          <button class="toolbar-range" type="button" @click="toggleRangePicker">
            <span :class="['range-display', { empty: !filters.start_date }]">
              {{ filters.start_date || '开始时间' }}
            </span>
            <span class="range-separator">至</span>
            <span :class="['range-display', { empty: !filters.end_date }]">
              {{ filters.end_date || '结束时间' }}
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
                    <button class="calendar-nav-btn" type="button" @click="moveCalendar(-1)">«</button>
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
                    <button class="calendar-nav-btn" type="button" @click="moveCalendar(1)">»</button>
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

        <button class="refresh-btn" type="button" @click="load" :disabled="loading" title="刷新">
          ↻
        </button>
      </div>
    </section>

    <section class="stats-grid">
      <article class="stat-card">
        <span class="stat-label">报警视频</span>
        <strong>{{ filteredItems.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">涉及通道</span>
        <strong>{{ uniqueChannelCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">累计时长</span>
        <strong>{{ totalDurationLabel }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">最新开始时间</span>
        <strong>{{ latestStartedAt }}</strong>
      </article>
    </section>

    <section v-if="loading" class="empty-state">正在加载报警视频...</section>
    <section v-else-if="filteredItems.length === 0" class="empty-state">暂无符合条件的报警视频</section>
    <template v-else>
      <section class="recording-grid">
        <article
          v-for="(item, index) in pagedItems"
          :key="item.id"
          class="recording-card"
          @click="openPreview(index)"
        >
          <div class="thumb-wrap">
            <div class="thumb-top-left">
              <span class="type-badge">{{ formatAlarmLabel(item) }}</span>
            </div>
            <div class="thumb-top-right">{{ durationLabel(item.duration) }}</div>

            <div class="image-frame">
              <img
                v-if="item.snapshot_url"
                class="snapshot"
                :src="toAssetSrc(item.snapshot_url)"
                loading="lazy"
                decoding="async"
                alt="报警抓拍"
              />
              <div v-else class="snapshot placeholder">暂无抓拍图</div>

              <div class="video-entry-mask">
                <button class="play-circle" type="button" @click.stop="openPreview(index)">▶</button>
              </div>
            </div>
          </div>

          <div class="recording-card-body">
            <div class="recording-channel">通道：{{ channelName(item.channel_id) }}</div>
            <div class="recording-time">{{ formatDateTime(item.started_at) }}</div>
          </div>
        </article>
      </section>

      <section class="pagination-bar">
        <div class="page-summary">
          共 {{ filteredItems.length }} 条，当前第 {{ currentPage }} / {{ totalPages }} 页
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
          </select>
        </div>
      </section>
    </template>

    <div v-if="previewIndex >= 0 && currentPreview" class="preview-mask" @click.self="closePreview">
      <div class="preview-dialog">
        <button class="close-btn" type="button" @click="closePreview" aria-label="关闭">×</button>

        <div class="preview-main">
          <div class="preview-left">
            <div class="preview-media-wrap">
              <video
                v-if="currentPreview.video_url"
                class="preview-video"
                :src="toAssetSrc(currentPreview.video_url)"
                controls
                autoplay
                playsinline
              />
              <img
                v-else-if="currentPreview.snapshot_url"
                class="preview-image"
                :src="toAssetSrc(currentPreview.snapshot_url)"
                alt="报警抓拍"
              />
              <div v-else class="preview-image placeholder">暂无可预览内容</div>

              <button
                v-if="pagedItems.length > 1"
                class="nav-arrow left"
                type="button"
                @click.stop="goPrev"
              >
                ‹
              </button>
              <button
                v-if="pagedItems.length > 1"
                class="nav-arrow right"
                type="button"
                @click.stop="goNext"
              >
                ›
              </button>
            </div>

            <div v-if="pagedItems.length > 1" class="pager-pill">
              {{ previewIndex + 1 }} / {{ pagedItems.length }}
            </div>
          </div>

          <aside class="preview-side">
            <h3>视频详情</h3>
            <div class="detail-list">
              <div class="detail-row">
                <span>通道</span>
                <strong>{{ channelName(currentPreview.channel_id) }}</strong>
              </div>
              <div class="detail-row">
                <span>类型</span>
                <strong>{{ formatEventType(currentPreview.event_type) }}</strong>
              </div>
              <div class="detail-row">
                <span>报警类别</span>
                <strong>{{ formatAlarmLabel(currentPreview) }}</strong>
              </div>
              <div class="detail-row">
                <span>事件 ID</span>
                <strong>{{ currentPreview.event_id || '-' }}</strong>
              </div>
              <div class="detail-row">
                <span>开始时间</span>
                <strong>{{ formatDateTime(currentPreview.started_at) }}</strong>
              </div>
              <div class="detail-row">
                <span>结束时间</span>
                <strong>{{ formatDateTime(currentPreview.ended_at) }}</strong>
              </div>
              <div class="detail-row">
                <span>时长</span>
                <strong>{{ durationLabel(currentPreview.duration) }}</strong>
              </div>
              <div class="detail-row">
                <span>状态</span>
                <strong>{{ currentPreview.status || '-' }}</strong>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { listChannels } from '@/api/channels'
import { listRecordings } from '@/api/recordings'

type RecordingItem = {
  id: string
  channel_id?: string
  started_at?: string
  ended_at?: string
  duration?: string | number
  status?: string
  event_id?: string
  event_type?: string
  event_type_label?: string
  class_name?: string
  conf?: number | string | null
  video_url?: string | null
  snapshot_url?: string | null
}

type ChannelItem = {
  id: string
  name?: string
}

type CalendarDay = {
  date: string
  day: number
  inCurrentMonth: boolean
  isToday: boolean
}

const loading = ref(false)
const items = ref<RecordingItem[]>([])
const channels = ref<ChannelItem[]>([])
const previewIndex = ref(-1)
const currentPage = ref(1)
const pageSize = ref(10)
const showRangePicker = ref(false)
const rangePickerRef = ref<HTMLElement | null>(null)
const leftCalendarDate = ref(new Date())
const rangeDraft = ref<{ start: string; end: string }>({
  start: '',
  end: '',
})

const filters = ref({
  channel_id: '',
  event_type: '',
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

const channelOptions = computed(() => channels.value)

const eventTypeOptions = computed(() => {
  return Array.from(
    new Set(items.value.map((item) => item.event_type).filter((item): item is string => Boolean(item)))
  )
})

const filteredItems = computed(() => {
  const start = filters.value.start_date ? new Date(`${filters.value.start_date}T00:00:00`).getTime() : null
  const end = filters.value.end_date ? new Date(`${filters.value.end_date}T23:59:59`).getTime() : null

  return items.value.filter((item) => {
    if (filters.value.channel_id && item.channel_id !== filters.value.channel_id) return false
    if (filters.value.event_type && item.event_type !== filters.value.event_type) return false

    if (start || end) {
      const current = item.started_at ? new Date(item.started_at).getTime() : null
      if (!current) return false
      if (start && current < start) return false
      if (end && current > end) return false
    }

    return true
  })
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredItems.value.length / pageSize.value)))

const pagedItems = computed(() => {
  const startIndex = (currentPage.value - 1) * pageSize.value
  return filteredItems.value.slice(startIndex, startIndex + pageSize.value)
})

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

const uniqueChannelCount = computed(() => {
  return new Set(filteredItems.value.map((item) => item.channel_id).filter(Boolean)).size
})

const totalDurationLabel = computed(() => {
  const totalSeconds = filteredItems.value.reduce((sum, item) => sum + parseDurationSeconds(item.duration), 0)
  return durationLabel(totalSeconds)
})

const latestStartedAt = computed(() => formatDateTime(filteredItems.value[0]?.started_at))
const currentPreview = computed(() => pagedItems.value[previewIndex.value] || null)
const rightCalendarDate = computed(() => addMonths(leftCalendarDate.value, 1))
const leftCalendarDays = computed(() => buildCalendarDays(leftCalendarDate.value))
const rightCalendarDays = computed(() => buildCalendarDays(rightCalendarDate.value))

const parseDurationSeconds = (value?: string | number) => {
  if (typeof value === 'number') return value
  if (!value) return 0
  if (/^\d+(\.\d+)?$/.test(value)) return Number(value)
  const parts = value.split(':').map((item) => Number(item))
  if (parts.some((item) => Number.isNaN(item))) return 0
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2]
  if (parts.length === 2) return parts[0] * 60 + parts[1]
  return 0
}

const durationLabel = (value?: string | number) => {
  const seconds = parseDurationSeconds(value)
  if (!seconds) return '0 秒'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}小时 ${m}分钟 ${s}秒`
  if (m > 0) return `${m}分钟 ${s}秒`
  return `${s}秒`
}

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

const formatEventType = (value?: string) => {
  if (!value) return '报警视频'
  if (value === 'alarm_clip') return '报警视频'
  return value
}

const formatAlarmLabel = (item?: RecordingItem | null) => {
  if (!item) return '-'
  const className = item.class_name || ''
  if (!className) return formatEventType(item.event_type)
  const conf = typeof item.conf === 'number' ? item.conf : Number(item.conf)
  if (!Number.isFinite(conf) || conf <= 0) return className
  return `${className} ${(conf * 100).toFixed(0)}%`
}

const channelName = (channelId?: string) => {
  if (!channelId) return '-'
  return channelMap.value[channelId]?.name || channelId
}

const toAssetSrc = (url?: string | null) => {
  if (!url) return ''
  if (url.startsWith('http://') || url.startsWith('https://')) return url
  const apiBase = String(import.meta.env.VITE_API_BASE || '/dev-api').replace(/\/$/, '')
  return `${apiBase}${url.startsWith('/') ? url : `/${url}`}`
}

const openPreview = (index: number) => {
  previewIndex.value = index
}

const closePreview = () => {
  previewIndex.value = -1
}

const goPrev = () => {
  if (!pagedItems.value.length) return
  previewIndex.value = (previewIndex.value - 1 + pagedItems.value.length) % pagedItems.value.length
}

const goNext = () => {
  if (!pagedItems.value.length) return
  previewIndex.value = (previewIndex.value + 1) % pagedItems.value.length
}

const goToPage = (page: number) => {
  const nextPage = Math.min(Math.max(page, 1), totalPages.value)
  currentPage.value = nextPage
  previewIndex.value = -1
  window.scrollTo({ top: 0, behavior: 'smooth' })
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
    const [recordingResult, channelResult] = await Promise.all([listRecordings(), listChannels()])
    items.value = recordingResult.items || []
    channels.value = channelResult.items || []
    if (currentPage.value > totalPages.value) {
      currentPage.value = totalPages.value
    }
    if (previewIndex.value >= pagedItems.value.length) {
      previewIndex.value = pagedItems.value.length - 1
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
  document.addEventListener('click', handleDocumentClick)
})

watch(
  () => [filters.value.channel_id, filters.value.event_type, filters.value.start_date, filters.value.end_date],
  () => {
    currentPage.value = 1
    previewIndex.value = -1
  }
)

watch(pageSize, () => {
  currentPage.value = 1
  previewIndex.value = -1
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<style scoped>
.recordings-page {
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

.toolbar-input.empty {
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
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 1.1rem;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1rem;
}

.stat-card {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  box-shadow: 0 12px 24px rgba(15, 23, 42, 0.04);
  padding: 1rem;
  display: grid;
  gap: 0.45rem;
}

.stat-label {
  color: #64748b;
  font-size: 0.92rem;
}

.stat-card strong {
  font-size: 1.35rem;
  color: #0f172a;
}

.empty-state {
  border-radius: 10px;
  padding: 1rem 1.1rem;
  background: #f8fafc;
  color: #475569;
  border: 1px solid #e2e8f0;
}

.recording-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 1rem;
}

.recording-card {
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.05);
  cursor: pointer;
}

.thumb-wrap {
  position: relative;
}

.thumb-top-left,
.thumb-top-right {
  position: absolute;
  top: 0.8rem;
  z-index: 2;
}

.thumb-top-left {
  left: 0.8rem;
}

.thumb-top-right {
  right: 0.8rem;
  padding: 0.28rem 0.6rem;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.72);
  color: #fff;
  font-size: 0.82rem;
}

.type-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.3rem 0.65rem;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.82rem;
  font-weight: 700;
}

.image-frame {
  position: relative;
  aspect-ratio: 16 / 9;
  background: #0f172a;
  overflow: hidden;
}

.snapshot,
.preview-image,
.preview-video {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  background: #000;
}

.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #cbd5e1;
}

.video-entry-mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, rgba(15, 23, 42, 0.08), rgba(15, 23, 42, 0.34));
}

.play-circle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 66px;
  height: 66px;
  border: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  color: #0f172a;
  font-size: 1.5rem;
  cursor: pointer;
  box-shadow: 0 14px 30px rgba(15, 23, 42, 0.28);
}

.recording-card-body {
  padding: 0.9rem 1rem 1rem;
  display: grid;
  gap: 0.35rem;
}

.recording-channel {
  color: #0f172a;
  font-weight: 700;
}

.recording-time {
  color: #64748b;
  font-size: 0.92rem;
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

.page-btn.active {
  background: #1d4ed8;
  color: #fff;
  border-color: #1d4ed8;
}

.page-size-select {
  width: 108px;
  min-width: 108px;
  max-width: 108px;
  padding: 0.45rem 1.8rem 0.45rem 0.6rem;
  font-size: 0.9rem;
}

.preview-mask {
  position: fixed;
  inset: 0;
  z-index: 80;
  background: rgba(15, 23, 42, 0.72);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem;
}

.preview-dialog {
  width: min(1180px, 100%);
  background: #fff;
  border-radius: 18px;
  padding: 1rem;
  position: relative;
}

.close-btn {
  position: absolute;
  right: 1rem;
  top: 1rem;
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 999px;
  background: #eff6ff;
  color: #0f172a;
  font-size: 1.3rem;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.preview-main {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(280px, 0.7fr);
  gap: 1rem;
  align-items: start;
}

.preview-left {
  display: grid;
  gap: 0.75rem;
}

.preview-media-wrap {
  position: relative;
  border-radius: 16px;
  overflow: hidden;
  background: #020617;
  aspect-ratio: 16 / 9;
}

.nav-arrow {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 42px;
  height: 42px;
  border: none;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.6);
  color: #fff;
  font-size: 1.6rem;
  cursor: pointer;
}

.nav-arrow.left {
  left: 1rem;
}

.nav-arrow.right {
  right: 1rem;
}

.pager-pill {
  justify-self: center;
  padding: 0.35rem 0.8rem;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.88rem;
  font-weight: 700;
}

.preview-side {
  display: grid;
  gap: 0.9rem;
}

.preview-side h3 {
  margin: 0;
}

.detail-list {
  display: grid;
  gap: 0.75rem;
}

.detail-row {
  padding-bottom: 0.7rem;
  border-bottom: 1px solid #e2e8f0;
}

.detail-row span {
  display: block;
  margin-bottom: 0.2rem;
  color: #64748b;
  font-size: 0.88rem;
}

.detail-row strong {
  color: #0f172a;
}

@media (max-width: 1080px) {
  .stats-grid,
  .preview-main {
    grid-template-columns: 1fr;
  }

  .recording-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .toolbar-row {
    grid-template-columns: 1fr;
  }

  .range-picker-panel {
    grid-template-columns: 1fr;
    min-width: min(100vw - 32px, 640px);
  }

  .range-shortcuts {
    grid-template-columns: repeat(4, minmax(0, 1fr));
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

  .recording-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .recording-grid {
    grid-template-columns: 1fr;
  }

  .pagination-bar {
    align-items: stretch;
  }
}
</style>
