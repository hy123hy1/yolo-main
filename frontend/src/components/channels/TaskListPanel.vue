<template>
  <div class="task-list-panel">
    <div class="task-list-head">
      <div>
        <h3>任务管理</h3>
        <p>{{ headerDescription }}</p>
      </div>
      <button
        class="primary-btn"
        type="button"
        :disabled="createDisabled"
        @click="emit('create')"
      >
        {{ createButtonText }}
      </button>
    </div>

    <input
      :value="keyword"
      class="task-search"
      type="text"
      placeholder="搜索任务名称"
      @input="handleSearchInput"
    />

    <div v-if="loading" class="empty-state">正在加载任务...</div>
    <div v-else-if="tasks.length === 0" class="empty-state">
      <p>{{ emptyDescription }}</p>
      <button
        class="ghost-btn"
        type="button"
        :disabled="createDisabled"
        @click="emit('create')"
      >
        {{ createButtonText }}
      </button>
    </div>

    <div v-else class="task-list">
      <article
        v-for="task in tasks"
        :key="task.id"
        :class="['task-card', { active: selectedTaskId === task.id }]"
        @click="emit('select', task.id)"
      >
        <div class="task-card-head">
          <strong>{{ task.name || '未命名任务' }}</strong>
          <span :class="['task-status', statusClass(task.runtime?.status || task.status)]">
            {{ statusLabel(task.runtime?.status || task.status) }}
          </span>
        </div>

        <div class="task-card-meta">
          <span>算法 {{ (task.algorithms || []).length }}</span>
          <span>最近事件 {{ formatDateTime(task.runtime?.last_event_at) }}</span>
        </div>

        <div class="task-card-actions" @click.stop>
          <button class="ghost-btn mini" type="button" @click="emit('start', task.id)">启动</button>
          <button class="ghost-btn mini" type="button" @click="emit('stop', task.id)">停止</button>
          <button class="danger-btn mini" type="button" @click="emit('delete', task.id)">删除</button>
        </div>
      </article>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatDateTime } from '@/utils/datetime'

const props = withDefaults(defineProps<{
  tasks: any[]
  selectedTaskId: string
  loading: boolean
  keyword: string
  createDisabled?: boolean
  createDisabledReason?: string
}>(), {
  createDisabled: false,
  createDisabledReason: '',
})

const emit = defineEmits<{
  (event: 'select', taskId: string): void
  (event: 'create'): void
  (event: 'start', taskId: string): void
  (event: 'stop', taskId: string): void
  (event: 'delete', taskId: string): void
  (event: 'search', value: string): void
}>()

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

const createButtonText = computed(() => {
  return props.createDisabled ? '当前通道已有任务' : '新建任务'
})

const headerDescription = computed(() => {
  return props.createDisabledReason || '围绕当前通道管理 AI 任务和算法。'
})

const emptyDescription = computed(() => {
  return props.createDisabledReason || '当前筛选条件下暂无任务。'
})

const handleSearchInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null
  emit('search', target?.value || '')
}
</script>

<style scoped>
.task-list-panel {
  display: grid;
  gap: 0.9rem;
}

.task-list-head {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  align-items: flex-start;
}

.task-list-head h3 {
  margin: 0;
}

.task-list-head p {
  margin: 0.35rem 0 0;
  color: #64748b;
}

.task-search {
  min-height: 40px;
  padding: 0.65rem 0.8rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  font: inherit;
}

.task-list {
  display: grid;
  gap: 0.75rem;
}

.task-card {
  display: grid;
  gap: 0.75rem;
  padding: 0.95rem;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  background: #fff;
  cursor: pointer;
}

.task-card.active {
  border-color: #1d4ed8;
  box-shadow: 0 8px 20px rgba(29, 78, 216, 0.12);
}

.task-card-head,
.task-card-actions {
  display: flex;
  justify-content: space-between;
  gap: 0.6rem;
  align-items: center;
  flex-wrap: wrap;
}

.task-card-meta {
  display: grid;
  gap: 0.35rem;
  color: #64748b;
  font-size: 0.9rem;
}

.task-status {
  padding: 0.26rem 0.62rem;
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

.empty-state {
  display: grid;
  gap: 0.75rem;
  padding: 1rem;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  color: #475569;
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
  border-color: #1d4ed8;
  color: #fff;
}

.primary-btn:disabled,
.ghost-btn:disabled,
.danger-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
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

.mini {
  min-height: 34px;
  padding: 0.42rem 0.72rem;
  font-size: 0.9rem;
}
</style>
