<template>
  <div class="recordings-page">
    <section class="panel hero-panel">
      <div class="panel-header">
        <div>
          <h2>录像中心</h2>
          <p>按通道查看录像记录、时间分布和最近录像，方便后续扩展时间轴回放和录像检索。</p>
        </div>
        <button class="ghost-btn" @click="load" :disabled="loading">
          {{ loading ? '刷新中...' : '刷新' }}
        </button>
      </div>

      <div class="filter-grid">
        <input v-model.trim="filters.channel_id" placeholder="按通道 ID 过滤，如 ch_xxx" />
        <button class="primary-btn" @click="load">查询录像</button>
        <button class="ghost-btn" @click="resetFilters">重置筛选</button>
      </div>
    </section>

    <section class="stats-grid">
      <article class="stat-card">
        <span class="stat-label">录像总数</span>
        <strong>{{ items.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">覆盖通道数</span>
        <strong>{{ uniqueChannelCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">累计时长</span>
        <strong>{{ totalDurationLabel }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">最近录像</span>
        <strong>{{ latestStartedAt }}</strong>
      </article>
    </section>

    <section class="content-grid">
      <div class="panel">
        <div class="section-head">
          <h3>最近录像</h3>
          <span>{{ items.length }} 条</span>
        </div>

        <div v-if="loading" class="empty-state">正在加载录像...</div>
        <div v-else-if="items.length === 0" class="empty-state">
          当前还没有录像记录。建议先去通道页确认录像策略和流是否正常。
        </div>
        <div v-else class="recording-list">
          <article v-for="item in items" :key="item.id" class="recording-card">
            <div class="recording-top">
              <div>
                <h4>{{ item.channel_id || '未知通道' }}</h4>
                <p>{{ item.id }}</p>
              </div>
              <span class="duration-pill">{{ item.duration || '-' }}</span>
            </div>

            <div class="recording-meta">
              <div><strong>开始时间</strong>{{ item.started_at || '-' }}</div>
              <div><strong>结束时间</strong>{{ item.ended_at || '-' }}</div>
              <div><strong>文件路径</strong>{{ item.file_path || '-' }}</div>
            </div>
          </article>
        </div>
      </div>

      <div class="side-column">
        <section class="panel">
          <div class="section-head">
            <h3>按月份统计</h3>
          </div>
          <div v-if="monthly.length === 0" class="empty-state">暂无月度统计</div>
          <div v-else class="monthly-list">
            <div v-for="item in monthly" :key="item.month" class="monthly-item">
              <span>{{ item.month }}</span>
              <strong>{{ item.count }} 条</strong>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="section-head">
            <h3>时间线摘要</h3>
          </div>
          <div v-if="timeline.length === 0" class="empty-state">暂无时间线数据</div>
          <div v-else class="timeline-list">
            <div v-for="item in timeline.slice(0, 8)" :key="item.id" class="timeline-item">
              <div class="timeline-dot"></div>
              <div class="timeline-body">
                <strong>{{ item.channel_id || '未知通道' }}</strong>
                <span>{{ item.started_at || '-' }}</span>
              </div>
            </div>
          </div>
        </section>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { listRecordingMonthly, listRecordings, listRecordingTimeline } from '@/api/recordings'

type RecordingItem = {
  id: string
  channel_id?: string
  started_at?: string
  ended_at?: string
  duration?: string
  file_path?: string
}

type TimelineItem = {
  id: string
  channel_id?: string
  started_at?: string
  ended_at?: string
  duration?: string
}

type MonthlyItem = {
  month: string
  count: number
}

const loading = ref(false)
const items = ref<RecordingItem[]>([])
const timeline = ref<TimelineItem[]>([])
const monthly = ref<MonthlyItem[]>([])

const filters = ref({
  channel_id: '',
})

const parseDurationSeconds = (value?: string) => {
  if (!value) return 0
  if (/^\d+(\.\d+)?$/.test(value)) {
    return Number(value)
  }
  const parts = value.split(':').map((item) => Number(item))
  if (parts.some((item) => Number.isNaN(item))) return 0
  if (parts.length === 3) {
    return parts[0] * 3600 + parts[1] * 60 + parts[2]
  }
  if (parts.length === 2) {
    return parts[0] * 60 + parts[1]
  }
  return 0
}

const formatDuration = (seconds: number) => {
  if (!seconds) return '0 秒'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}小时 ${m}分钟`
  if (m > 0) return `${m}分钟 ${s}秒`
  return `${s}秒`
}

const uniqueChannelCount = computed(() => {
  return new Set(items.value.map((item) => item.channel_id).filter(Boolean)).size
})

const totalDurationLabel = computed(() => {
  const totalSeconds = items.value.reduce((sum, item) => sum + parseDurationSeconds(item.duration), 0)
  return formatDuration(totalSeconds)
})

const latestStartedAt = computed(() => items.value[0]?.started_at || '-')

const load = async () => {
  loading.value = true
  try {
    const params = {
      channel_id: filters.value.channel_id || undefined,
    }
    const [recordingResult, timelineResult, monthlyResult] = await Promise.all([
      listRecordings(params),
      listRecordingTimeline(params),
      listRecordingMonthly(params),
    ])
    items.value = recordingResult.items || []
    timeline.value = timelineResult.items || []
    monthly.value = monthlyResult.items || []
  } finally {
    loading.value = false
  }
}

const resetFilters = async () => {
  filters.value.channel_id = ''
  await load()
}

onMounted(load)
</script>

<style scoped>
.recordings-page {
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

.hero-panel {
  background:
    radial-gradient(circle at right top, rgba(59, 130, 246, 0.12), transparent 28%),
    linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
}

.panel-header,
.section-head,
.recording-top {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
}

.panel-header {
  margin-bottom: 1rem;
}

.panel-header h2,
.section-head h3,
.recording-top h4 {
  margin: 0;
}

.panel-header p,
.recording-top p,
.section-head span {
  margin: 0.35rem 0 0;
  color: #64748b;
}

.filter-grid {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto auto;
  gap: 0.75rem;
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
  padding: 1rem;
  display: grid;
  gap: 0.45rem;
  box-shadow: 0 10px 22px rgba(15, 23, 42, 0.04);
}

.stat-label {
  color: #64748b;
  font-size: 0.92rem;
}

.stat-card strong {
  font-size: 1.35rem;
  color: #0f172a;
}

.content-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(300px, 0.9fr);
  gap: 1rem;
}

.side-column {
  display: grid;
  gap: 1rem;
}

.recording-list,
.monthly-list,
.timeline-list {
  display: grid;
  gap: 0.9rem;
}

.recording-card {
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 1rem;
  display: grid;
  gap: 0.8rem;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
}

.duration-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.3rem 0.7rem;
  border-radius: 999px;
  background: #dbeafe;
  color: #1d4ed8;
  font-weight: 700;
  font-size: 0.82rem;
}

.recording-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.5rem;
  color: #334155;
  word-break: break-all;
}

.monthly-item,
.timeline-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.8rem;
  padding: 0.85rem 0.95rem;
  border-radius: 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}

.timeline-item {
  justify-content: flex-start;
}

.timeline-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: #1d4ed8;
  flex-shrink: 0;
}

.timeline-body {
  display: grid;
  gap: 0.2rem;
  color: #334155;
}

.timeline-body span {
  color: #64748b;
  font-size: 0.92rem;
}

.primary-btn,
.ghost-btn,
.filter-grid input {
  min-height: 42px;
  padding: 0.75rem 0.9rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  font: inherit;
}

.primary-btn,
.ghost-btn {
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

.empty-state {
  border-radius: 10px;
  padding: 0.85rem 1rem;
  background: #f8fafc;
  color: #475569;
  border: 1px solid #e2e8f0;
}

@media (max-width: 1080px) {
  .stats-grid,
  .content-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .panel-header,
  .section-head,
  .recording-top {
    flex-direction: column;
  }

  .filter-grid {
    grid-template-columns: 1fr;
  }
}
</style>
