<template>
  <div class="task-detail-panel">
    <div v-if="!task" class="empty-state">
      <h3>请选择任务</h3>
      <p>先在左侧选中一个任务，再在这里查看详情、管理算法和控制运行状态。</p>
    </div>

    <template v-else>
      <section class="detail-card">
        <div class="detail-head">
          <div>
            <h3>{{ draft.name || '未命名任务' }}</h3>
            <p>{{ draft.source_url || '-' }}</p>
          </div>
          <span :class="['task-status', statusClass(status?.status || draft.runtime?.status || draft.status)]">
            {{ statusLabel(status?.status || draft.runtime?.status || draft.status) }}
          </span>
        </div>

        <div class="form-grid">
          <label class="field">
            <span>任务名称</span>
            <input v-model.trim="draft.name" type="text" placeholder="请输入任务名称" />
          </label>
          <label class="field">
            <span>视频源</span>
            <input v-model.trim="draft.source_url" type="text" placeholder="请输入视频源地址" />
          </label>
          <label class="field">
            <span>帧间隔</span>
            <input v-model.number="draft.frame_interval" type="number" min="1" step="1" />
          </label>
          <div class="field">
            <span>创建时间</span>
            <div class="static-value">{{ formatDateTime(draft.created_at) }}</div>
          </div>
        </div>

        <div class="action-row">
          <button class="ghost-btn" type="button" @click="emitSave">保存配置</button>
          <button class="primary-btn" type="button" @click="emitStart">启动任务</button>
          <button class="warn-btn" type="button" @click="$emit('stop', draft.id)">停止任务</button>
        </div>
      </section>

      <section class="detail-card">
        <div class="detail-head">
          <div>
            <h4>算法列表</h4>
            <p>一个任务可绑定多个算法，各算法独立触发事件，互不耦合。</p>
          </div>
          <button class="primary-btn" type="button" @click="$emit('create-algorithm')">新增算法</button>
        </div>

        <div v-if="!draft.algorithms?.length" class="empty-state inner">
          当前任务还没有算法，请先新增算法。
        </div>

        <div v-else class="algorithm-grid">
          <article
            v-for="algorithm in draft.algorithms"
            :key="algorithm.id"
            class="algorithm-card"
          >
            <div class="algorithm-head">
              <strong>{{ modelName(algorithm) }}</strong>
              <span class="mini-pill">{{ algorithm.algorithm_type || 'detection' }}</span>
            </div>

            <div class="algorithm-meta">
              <div>检测类别：{{ algorithmLabelSummary(algorithm) }}</div>
              <div>已绑区域：{{ (algorithm.regions || []).length }}</div>
              <div>保存抓拍图：{{ algorithm.need_snapshot ? '是' : '否' }}</div>
              <div>告警视频：{{ algorithm.need_video_clip ? '6 秒' : '关闭' }}</div>
              <div>区域配置：{{ (algorithm.regions || []).length > 0 ? '已配置区域' : '未配置区域' }}</div>
            </div>

            <div class="overlay-tip">
              区域配置始终可用，可按算法需要为当前算法绑定检测区域。
            </div>

            <div class="action-row compact">
              <button class="ghost-btn mini" type="button" @click="$emit('edit-algorithm', algorithm)">编辑</button>
              <button
                class="ghost-btn mini"
                type="button"
                @click="$emit('edit-regions', algorithm)"
              >
                区域配置
              </button>
              <button class="danger-btn mini" type="button" @click="$emit('delete-algorithm', algorithm)">删除</button>
            </div>
          </article>
        </div>
      </section>

      <section class="detail-card">
        <div class="detail-head">
          <div>
            <h4>运行状态</h4>
            <p>用于确认任务是否正常运行，以及最近是否有分析和事件触发。</p>
          </div>
        </div>

        <div class="status-grid">
          <div><strong>状态：</strong>{{ statusLabel(status?.status || draft.runtime?.status || draft.status) }}</div>
          <div><strong>分析帧数：</strong>{{ status?.runtime?.frame_count ?? draft.runtime?.frame_count ?? 0 }}</div>
          <div><strong>事件数：</strong>{{ status?.runtime?.event_count ?? draft.runtime?.event_count ?? 0 }}</div>
          <div><strong>最近分析：</strong>{{ formatDateTime(status?.runtime?.last_analysis_at ?? draft.runtime?.last_analysis_at) }}</div>
          <div><strong>最近事件：</strong>{{ formatDateTime(status?.runtime?.last_event_at ?? draft.runtime?.last_event_at) }}</div>
          <div><strong>错误信息：</strong>{{ status?.runtime?.last_error ?? draft.runtime?.last_error ?? '-' }}</div>
        </div>

        <div v-if="modelRuntimeEntries.length" class="runtime-grid">
          <article v-for="[runtimeId, runtimeItem] in modelRuntimeEntries" :key="runtimeId" class="runtime-card">
            <strong>{{ modelName(runtimeItem) }}</strong>
            <div>检测数：{{ runtimeItem.detections ?? 0 }}</div>
            <div>最近分析：{{ formatDateTime(runtimeItem.last_analysis_at) }}</div>
            <div>最近事件：{{ formatDateTime(runtimeItem.last_event_at) }}</div>
          </article>
        </div>

        <div class="detail-head compact-head">
          <div>
            <h4>实时看板</h4>
            <p>人员计数、车辆计数等实时指标会展示在这里，不作为报警事件刷屏。</p>
          </div>
        </div>

        <div v-if="metricEntries.length" class="metric-grid">
          <article v-for="metric in metricEntries" :key="metric.id" class="metric-card">
            <div class="metric-head">
              <strong>{{ metric.algorithm_name || modelName(metric) }}</strong>
              <span>{{ metricTypeLabel(metric.metric_type) }}</span>
            </div>
            <div class="metric-value">{{ formatMetricValue(metric.metric_value) }}</div>
            <div class="metric-meta">更新时间：{{ formatDateTime(metric.updated_at) }}</div>
            <div v-if="classCountText(metric)" class="metric-meta">类别明细：{{ classCountText(metric) }}</div>
          </article>
        </div>

        <div v-else class="empty-state inner">
          暂无实时计数数据。任务运行后，人员计数/车辆计数会在这里更新。
        </div>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { formatDateTime } from '@/utils/datetime'

const props = defineProps<{
  task: any | null
  status: any | null
  modelOptions: Array<{ model_id?: string; id?: string; name?: string }>
  loading?: boolean
}>()

const emit = defineEmits<{
  (event: 'save', payload: any): void
  (event: 'start', payload: any): void
  (event: 'stop', taskId: string): void
  (event: 'create-algorithm'): void
  (event: 'edit-algorithm', algorithm: any): void
  (event: 'edit-regions', algorithm: any): void
  (event: 'delete-algorithm', algorithm: any): void
}>()

const draft = ref<any | null>(null)

watch(
  () => props.task,
  (value) => {
    draft.value = value ? JSON.parse(JSON.stringify(value)) : null
  },
  { immediate: true, deep: true },
)

const statusLabel = (status?: string) => {
  if (status === 'running' || status === 'starting' || status === 'restarting') return '运行中'
  if (status === 'error') return '异常'
  if (status === 'stopping') return '停止中'
  return '已停止'
}

const statusClass = (status?: string) => {
  if (status === 'running' || status === 'starting' || status === 'restarting') return 'running'
  if (status === 'error') return 'error'
  if (status === 'stopping') return 'pending'
  return 'stopped'
}

const modelName = (modelOrId?: any) => {
  const modelId = typeof modelOrId === 'string' ? modelOrId : modelOrId?.model_id
  const currentName = typeof modelOrId === 'object' ? modelOrId?.model_name : ''
  if (currentName) return currentName
  const item = props.modelOptions.find((entry) => (entry.model_id || entry.id) === modelId)
  return item?.name || modelId || '未选择模型'
}

const algorithmLabelSummary = (algorithm: any) => {
  const labels = Array.isArray(algorithm?.labels) ? algorithm.labels.filter(Boolean) : []
  if (labels.length === 0) return '全部'
  if (labels.length <= 3) return labels.join('、')
  return `${labels.slice(0, 3).join('、')} 等 ${labels.length} 类`
}

const modelRuntimeEntries = computed(() => {
  const models = props.status?.runtime?.models || props.task?.runtime?.models || {}
  return Object.entries(models) as Array<[string, Record<string, any>]>
})

const metricEntries = computed(() => {
  const metrics = Array.isArray(props.status?.metrics) ? props.status.metrics : []
  return metrics.filter((item: any) => item && item.metric_type)
})

const metricTypeLabel = (metricType?: string) => {
  if (metricType === 'person_count') return '人员计数'
  if (metricType === 'vehicle_count') return '车辆计数'
  return '实时计数'
}

const formatMetricValue = (value?: number) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toFixed(0)
}

const classCountText = (metric: any) => {
  const counts = metric?.raw_json?.class_counts || {}
  const entries = Object.entries(counts).filter(([_, value]) => Number(value) > 0)
  if (!entries.length) return ''
  return entries.map(([key, value]) => `${key}:${value}`).join('、')
}

const emitSave = () => {
  if (!draft.value) return
  emit('save', draft.value)
}

const emitStart = () => {
  if (!draft.value) return
  emit('start', draft.value)
}
</script>

<style scoped>
.task-detail-panel {
  display: grid;
  gap: 1rem;
}

.detail-card {
  display: grid;
  gap: 0.95rem;
  padding: 1rem;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  background: #fff;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 0.8rem;
  align-items: flex-start;
}

.detail-head h3,
.detail-head h4 {
  margin: 0;
}

.detail-head p {
  margin: 0.35rem 0 0;
  color: #64748b;
  word-break: break-all;
}

.form-grid,
.status-grid,
.algorithm-grid,
.runtime-grid,
.metric-grid {
  display: grid;
  gap: 0.75rem;
}

.form-grid {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.field {
  display: grid;
  gap: 0.4rem;
}

.field span {
  color: #334155;
  font-weight: 600;
}

.field input {
  min-height: 40px;
  padding: 0.65rem 0.8rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  font: inherit;
}

.static-value {
  min-height: 40px;
  display: flex;
  align-items: center;
  padding: 0.65rem 0.8rem;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  color: #475569;
}

.algorithm-grid,
.runtime-grid,
.metric-grid {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.algorithm-card,
.runtime-card,
.metric-card {
  display: grid;
  gap: 0.65rem;
  padding: 0.9rem;
  border-radius: 14px;
  border: 1px solid #dbe4ee;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
}

.compact-head {
  padding-top: 0.35rem;
  border-top: 1px solid #e2e8f0;
}

.metric-card {
  border-color: #bfdbfe;
  background: linear-gradient(180deg, #f8fbff 0%, #eff6ff 100%);
}

.metric-head {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  align-items: center;
}

.metric-head span {
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  background: #dbeafe;
  color: #1d4ed8;
  font-size: 0.78rem;
  font-weight: 700;
}

.metric-value {
  color: #0f172a;
  font-size: 2rem;
  font-weight: 800;
  letter-spacing: -0.04em;
}

.metric-meta {
  color: #475569;
  font-size: 0.9rem;
}

.algorithm-head,
.action-row {
  display: flex;
  gap: 0.65rem;
  flex-wrap: wrap;
  align-items: center;
}

.algorithm-head {
  justify-content: space-between;
}

.algorithm-meta,
.status-grid {
  color: #334155;
}

.status-grid {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}

.overlay-tip {
  padding: 0.65rem 0.8rem;
  border-radius: 10px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.9rem;
}

.empty-state {
  display: grid;
  gap: 0.55rem;
  padding: 1rem;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  color: #475569;
}

.empty-state h3 {
  margin: 0;
}

.inner {
  padding: 0.85rem;
}

.task-status {
  padding: 0.3rem 0.65rem;
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 700;
}

.task-status.running {
  background: #dcfce7;
  color: #166534;
}

.task-status.stopped {
  background: #e2e8f0;
  color: #475569;
}

.task-status.pending {
  background: #fef3c7;
  color: #92400e;
}

.task-status.error {
  background: #fee2e2;
  color: #b91c1c;
}

.mini-pill {
  padding: 0.26rem 0.6rem;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.82rem;
  font-weight: 700;
}

.primary-btn,
.ghost-btn,
.danger-btn,
.warn-btn {
  min-height: 40px;
  padding: 0.65rem 0.95rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  cursor: pointer;
  font: inherit;
}

.primary-btn {
  background: #1d4ed8;
  border-color: #1d4ed8;
  color: #fff;
}

.ghost-btn {
  background: #fff;
  color: #334155;
}

.danger-btn {
  background: #b91c1c;
  border-color: #b91c1c;
  color: #fff;
}

.warn-btn {
  background: #7c2d12;
  border-color: #7c2d12;
  color: #fff;
}

.compact {
  justify-content: flex-start;
}

.mini {
  min-height: 34px;
  padding: 0.42rem 0.72rem;
  font-size: 0.9rem;
}
</style>

