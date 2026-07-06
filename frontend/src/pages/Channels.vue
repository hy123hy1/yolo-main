<template>
  <div class="channels-page">
    <section class="panel header-panel">
      <div class="panel-header">
        <div>
          <h2>通道</h2>
          <p>把预览、通道管理、任务管理和电子围栏拆开，避免所有操作堆在同一屏里。</p>
        </div>
        <button class="ghost-btn" @click="load" :disabled="loading">
          {{ loading ? '刷新中...' : '刷新' }}
        </button>
      </div>

      <div v-if="message" :class="['message', messageType]">
        {{ message }}
      </div>

      <div class="tab-row">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          :class="['tab-btn', { active: activeTab === tab.key }]"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </div>
    </section>

    <section v-if="activeTab === 'preview'" class="panel">
      <div class="section-head">
        <h3>预览</h3>
        <span>{{ items.length }} 个通道</span>
      </div>

      <div v-if="loading" class="empty-state">正在加载通道...</div>
      <div v-else-if="items.length === 0" class="empty-state">暂无通道</div>
      <div v-else class="preview-grid">
        <article
          v-for="item in items"
          :key="item.id"
          :ref="(el) => setPreviewCardRef(item.id, el as HTMLElement | null)"
          class="preview-card"
        >
          <div :id="`preview-card-${item.id}`" class="preview-card-head">
            <div>
              <h4>{{ item.name }}</h4>
              <p>设备 ID: {{ item.device_id || '-' }}</p>
            </div>
            <div class="status-pills">
              <span :class="['pill', item.is_online ? 'ok' : 'muted']">
                {{ item.is_online ? '在线' : '离线' }}
              </span>
              <span :class="['pill', isPreviewPlaying(item.id) ? 'ok' : 'muted']">
                {{ isPreviewPlaying(item.id) ? '播放中' : '待播放' }}
              </span>
            </div>
          </div>

          <div class="player-shell">
            <video
              :ref="(el) => setVideoRef(item.id, el as HTMLVideoElement | null)"
              class="player"
              autoplay
              muted
              playsinline
              controls
            />
            <div class="player-overlay-actions">
              <button class="play-circle" type="button" @click="openExpandedPreview(item)">
                ▶
              </button>
            </div>
            <div v-if="!item.is_online" class="player-mask offline-mask">
              <span>通道离线时不会自动预览</span>
            </div>
            <div class="source-line">{{ item.source_url }}</div>
          </div>

          <div class="preview-actions">
            <button class="warn-btn" @click="startPlay(item.id)">开始播放</button>
            <button class="ghost-btn" @click="startBrowserPlay(item.id)">浏览器播放</button>
            <button class="danger-btn" @click="stopPlay(item.id)">停止播放</button>
          </div>

          <div v-if="rtcErrors[item.id]" class="error-text">
            {{ rtcErrors[item.id] }}
          </div>
          <div v-if="debugResults[item.id]" class="debug-text">
            {{ debugResults[item.id] }}
          </div>
        </article>
      </div>
    </section>

    <section v-if="activeTab === 'manage'" class="panel">
      <div class="section-head">
        <div>
          <h3>通道管理</h3>
          <span>新增、编辑和删除通道</span>
        </div>
        <div class="section-head-actions">
          <button class="ghost-btn" @click="handleSyncWvpChannels" :disabled="loading">
            同步 WVP 通道
          </button>
          <button class="primary-btn" @click="openCreateModal">创建通道</button>
        </div>
      </div>

      <div v-if="items.length === 0" class="empty-state">暂无通道</div>
      <div v-else class="table-wrap">
        <table class="channel-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>地址</th>
              <th>应用</th>
              <th>流名</th>
              <th>状态</th>
              <th>AI</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.id">
              <td>
                <div class="table-main">{{ item.name }}</div>
                <div class="table-sub">{{ item.id }}</div>
              </td>
              <td>{{ String(item.type || '').toUpperCase() }}</td>
              <td class="td-break">{{ item.source_url || '-' }}</td>
              <td>{{ item.app || '-' }}</td>
              <td>{{ item.stream || '-' }}</td>
              <td>
                <span :class="['pill', item.is_online ? 'ok' : 'muted']">
                  {{ item.is_online ? '在线' : '离线' }}
                </span>
                <span :class="['pill', isPreviewPlaying(item.id) ? 'ok' : 'muted']">
                  {{ isPreviewPlaying(item.id) ? '播放中' : '未播放' }}
                </span>
              </td>
              <td>
                <span :class="['pill', aiStatuses[item.id]?.is_running ? 'ai-on' : 'muted']">
                  {{ aiStatusLabel(item.id) }}
                </span>
              </td>
              <td>
                <div class="table-actions">
                  <button class="ghost-btn mini" @click="openPreviewTab(item.id)">预览</button>
                  <button class="ghost-btn mini" @click="openEdit(item)">编辑</button>
                  <button class="danger-btn mini" @click="removeChannel(item.id)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="activeTab === 'tasks'" class="panel">
      <div class="section-head">
        <div>
          <h3>任务管理</h3>
          <span>围绕通道创建和运行 AI 任务，逐步替代旧 AI 能力入口。</span>
        </div>
      </div>

      <div v-if="items.length === 0" class="empty-state">暂无通道可配置任务</div>
      <div v-else class="task-management-layout">
        <div class="task-management-toolbar">
          <label class="task-channel-switch">
            <span>当前通道</span>
            <select v-model="selectedTaskChannelId" @change="handleTaskChannelChange">
              <option value="">全部通道</option>
              <option v-for="item in items" :key="item.id" :value="item.id">
                {{ item.name }}
              </option>
            </select>
          </label>
        </div>

        <div class="task-management-grid">
          <TaskListPanel
            :tasks="filteredTasks"
            :selected-task-id="selectedTaskId"
            :loading="taskLoading"
            :keyword="taskKeyword"
            :create-disabled="taskCreateDisabled"
            :create-disabled-reason="taskCreateDisabledReason"
            @select="selectTask"
            @create="openTaskCreateModal"
            @start="startTaskAction"
            @stop="stopTaskAction"
            @delete="deleteTaskAction"
            @search="handleTaskSearch"
          />

          <TaskDetailPanel
            :task="selectedTask"
            :status="selectedTaskStatus"
            :model-options="models"
            :loading="taskLoading"
            @save="saveTaskDraft"
            @start="startTaskDraft"
            @stop="stopTaskAction"
            @create-algorithm="openAlgorithmCreateModal"
            @edit-algorithm="openAlgorithmEditModal"
            @edit-regions="openTaskRegionEditor"
            @delete-algorithm="deleteAlgorithmAction"
          />
        </div>
      </div>
    </section>

    <section v-if="activeTab === 'fence'" class="panel">
      <div class="section-head">
        <h3>电子围栏</h3>
        <span>生成带区域框的预览流，当前页可看，也可提供给第三方平台取流</span>
      </div>

      <div v-if="items.length === 0" class="empty-state">暂无通道可配置电子围栏</div>
      <div v-else class="ai-list">
        <article v-for="item in items" :key="`fence-${item.id}`" class="ai-card">
          <div :ref="(el) => setFenceCardRef(item.id, el as HTMLElement | null)"></div>
          <div class="ai-card-head">
            <div>
              <h4>{{ item.name }}</h4>
              <p>{{ item.id }}</p>
            </div>
            <div class="status-pills">
              <span class="pill type">{{ String(item.type || '').toUpperCase() }}</span>
              <span :class="['pill', zonePreviewStatuses[item.id]?.status === 'running' ? 'ok' : 'muted']">
                {{ zonePreviewLabel(item.id) }}
              </span>
            </div>
          </div>

          <div class="zone-preview-headline">
            <div><strong>当前区域数：</strong>{{ zonePreviewRegionCount(item.id) }}</div>
            <div><strong>区域来源：</strong>{{ zonePreviewSourceLabel(item.id) }}</div>
            <div><strong>区域流名：</strong>{{ zonePreviewStatuses[item.id]?.stream || `${item.stream}_zone` }}</div>
          </div>

          <div class="zone-preview-box">
            <div class="zone-preview-head">
              <div>
                <div class="section-title">区域预览</div>
                <div class="zone-preview-help">任务启动后会生成区域流。本页不自动播放，需要手动点击播放按钮；只有在对接第三方平台时，才需要展开下方的取流地址。</div>
              </div>
            </div>

            <div class="zone-preview-player-shell">
              <video
                :ref="(el) => setZonePreviewVideoRef(item.id, el as HTMLVideoElement | null)"
                class="zone-preview-player"
                autoplay
                muted
                playsinline
                controls
              />
              <div v-if="!isZonePreviewPlaying(item.id)" class="player-mask">
                <div class="zone-preview-mask-content">
                  <button
                    class="play-circle"
                    :class="{ disabled: !canManualPlayZonePreview(item.id) }"
                    type="button"
                    :disabled="!canManualPlayZonePreview(item.id)"
                    @click="startZonePreviewPlayer(item.id)"
                  >
                    ▶
                  </button>
                  <span class="zone-preview-mask-text">{{ zonePreviewMaskText(item.id) }}</span>
                </div>
              </div>
            </div>

            <div class="zone-preview-actions">
              <template v-if="canManualPlayZonePreview(item.id)">
                <button class="ghost-btn" type="button" @click="openZonePreviewModal(item)">放大预览</button>
              </template>
            </div>

            <details class="zone-preview-advanced" @toggle="handleZonePreviewAdvancedToggle(item.id, $event)">
              <summary>第三方取流地址</summary>
              <div class="zone-preview-advanced-body">
                <div class="zone-preview-meta">
                  <div><strong>浏览器播放地址：</strong>{{ zonePreviewPlayInfos[item.id]?.hls || zonePreviewPlayInfos[item.id]?.webrtc_api || '-' }}</div>
                </div>
                <div class="zone-preview-actions">
                  <button class="ghost-btn" type="button" @click="copyZonePreviewUrl(item.id)">复制播放地址</button>
                </div>
                <div v-if="zonePreviewStatuses[item.id]?.last_error" class="zone-preview-inline-error">
                  最近错误：{{ zonePreviewStatuses[item.id]?.last_error }}
                </div>
              </div>
            </details>
          </div>

          <div v-if="zonePreviewErrors[item.id]" class="error-text">
            {{ zonePreviewErrors[item.id] }}
          </div>
          <div v-if="zonePreviewDebug[item.id]" class="debug-text">
            {{ zonePreviewDebug[item.id] }}
          </div>
        </article>
      </div>
    </section>

    <div v-if="creatingChannel" class="modal-mask" @click.self="closeCreateModal">
      <div class="modal-dialog">
        <div class="modal-header">
          <div>
            <h3>创建通道</h3>
            <p>填写通道名称、源地址和可选的默认模型。</p>
          </div>
          <button class="ghost-btn modal-close-btn" @click="closeCreateModal" aria-label="关闭">×</button>
        </div>

        <div class="form-grid modal-grid">
          <input v-model.trim="form.name" placeholder="通道名称" />
          <select v-model="form.type">
            <option value="rtsp">RTSP</option>
            <option value="rtmp">RTMP</option>
          </select>
          <input v-model.trim="form.source_url" placeholder="源地址，例如 rtsp://..." class="span-2" />
          <select v-model="form.media_node_id">
            <option value="">默认媒体节点</option>
            <option v-for="node in mediaNodes" :key="node.id" :value="node.id">
              {{ node.name }}
            </option>
          </select>
          <select v-model="form.model_id">
            <option value="">不预设 AI 模型</option>
            <option v-for="model in models" :key="model.model_id" :value="model.model_id">
              {{ modelDisplayName(model) }}
            </option>
          </select>
        </div>

        <div class="modal-actions">
          <button class="ghost-btn" @click="closeCreateModal">取消</button>
          <button class="primary-btn" @click="submit" :disabled="submitting">
            {{ submitting ? '创建中...' : '创建通道' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="editingChannel" class="modal-mask" @click.self="closeEdit">
      <div class="modal-dialog">
        <div class="modal-header">
          <div>
            <h3>编辑通道</h3>
            <p>{{ editingChannel.id }}</p>
          </div>
          <button class="ghost-btn modal-close-btn" @click="closeEdit" aria-label="关闭">×</button>
        </div>

        <div class="form-grid modal-grid">
          <input v-model.trim="editForm.name" placeholder="通道名称" />
          <select v-model="editForm.type">
            <option value="rtsp">RTSP</option>
            <option value="rtmp">RTMP</option>
          </select>
          <input v-model.trim="editForm.source_url" placeholder="源地址" class="span-2" />
          <input v-model.trim="editForm.app" placeholder="应用名" />
          <input v-model.trim="editForm.stream" placeholder="流名" />
          <select v-model="editForm.media_node_id">
            <option value="">默认媒体节点</option>
            <option v-for="node in mediaNodes" :key="node.id" :value="node.id">
              {{ node.name }}
            </option>
          </select>
        </div>

        <div class="modal-actions">
          <button class="ghost-btn" @click="closeEdit">取消</button>
          <button class="primary-btn" @click="saveEdit" :disabled="savingEdit">
            {{ savingEdit ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="expandedPreview" class="modal-mask" @click.self="closeExpandedPreview">
      <div class="modal-dialog preview-modal-dialog">
        <div class="modal-header">
          <div>
            <h3>{{ expandedPreview.name }}</h3>
            <p>{{ expandedPreview.sourceUrl }}</p>
          </div>
          <button class="ghost-btn modal-close-btn" @click="closeExpandedPreview" aria-label="关闭">×</button>
        </div>

        <div class="preview-modal-player-shell">
          <video
            ref="expandedPreviewVideoRef"
            class="preview-modal-player"
            autoplay
            muted
            playsinline
            controls
          />
        </div>

        <div v-if="expandedPreviewError" class="error-text">
          {{ expandedPreviewError }}
        </div>
      </div>
    </div>


    <ZoneEditorModal
      :visible="zoneEditor.visible"
      :channel-id="zoneEditor.channelId"
      :channel-name="zoneEditor.channelName"
      :model-labels="zoneEditor.modelLabels"
      @close="closeZoneEditor"
      @updated="handleZonesUpdated"
    />

    <TaskEditorModal
      :visible="taskModalVisible"
      :channel-id="selectedTaskChannelId"
      :source-url="availableTaskChannels.find((item) => item.id === selectedTaskChannelId)?.source_url || ''"
      :channel-options="availableTaskChannels"
      @close="closeTaskCreateModal"
      @submit="submitTaskCreate"
    />

    <AlgorithmEditorModal
      :visible="algorithmModalVisible"
      :algorithm="editingAlgorithm"
      :model-options="algorithmModelOptions"
      :used-model-ids="selectedTaskUsedModelIds"
      @close="closeAlgorithmModal"
      @submit="submitAlgorithm"
    />

    <TaskRegionEditorModal
      :visible="taskRegionEditor.visible"
      :task-id="taskRegionEditor.taskId"
      :task-name="taskRegionEditor.taskName"
      :channel-id="taskRegionEditor.channelId"
      :algorithm-id="taskRegionEditor.algorithmId"
      :algorithm-name="taskRegionEditor.algorithmName"
      @close="closeTaskRegionEditor"
      @updated="handleTaskRegionsUpdated"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import ZoneEditorModal from '@/components/channels/ZoneEditorModal.vue'
import TaskListPanel from '@/components/channels/TaskListPanel.vue'
import TaskDetailPanel from '@/components/channels/TaskDetailPanel.vue'
import TaskEditorModal from '@/components/channels/TaskEditorModal.vue'
import AlgorithmEditorModal from '@/components/channels/AlgorithmEditorModal.vue'
import TaskRegionEditorModal from '@/components/channels/TaskRegionEditorModal.vue'
import {
  createChannel,
  deleteChannel,
  getChannelAIStatus,
  getChannelFenceBrowserPlayUrl,
  getChannelPlayUrl,
  getChannelZonePreviewStatus,
  listAvailableTaskChannels,
  listChannels,
  listChannelZones,
  startChannelZonePreview,
  startChannelPlay,
  stopChannelPlay,
  syncWvpChannels,
  updateChannel,
  updateChannelAIConfig,
} from '@/api/channels'
import {
  addTaskAlgorithm,
  createTask,
  deleteTask,
  deleteTaskAlgorithm,
  getTask,
  getTaskStatus,
  listTaskAvailableModels,
  listTaskAlgorithms,
  listTasks,
  startTask,
  stopTask,
  updateTask,
  updateTaskAlgorithm,
} from '@/api/tasks'
import { listMediaNodes } from '@/api/mediaNodes'
import { listModels } from '@/api/models'
import { createSingleIntervalController, shouldPollChannelAiStatus } from '@/utils/single-interval-controller.js'

type PlayUrlMap = Record<string, string>

type FenceBrowserPlayInfo = {
  webrtc_api?: string
  hls?: string
}

type ZoneItem = {
  id: string
  name: string
  color?: string
  labels: string[]
  coordinates: number[][]
}

type ChannelAIConfigState = {
  enabled: boolean
  model_id: string
  labels: string[]
  conf: number
  frame_interval: number
  zone_ids: string[]
  save_snapshot: boolean
  models: ChannelAIModelConfig[]
}

type ChannelAIModelConfig = {
  local_id: string
  model_id: string
  enabled: boolean
  labels: string[]
  conf: number
  frame_interval: number
  zone_ids: string[]
  save_snapshot: boolean
}

type AIStatus = {
  status?: string
  is_running?: boolean
  last_error?: string | null
  last_analysis_at?: string | null
  last_event_at?: string | null
  frame_count?: number
  event_count?: number
  models?: Record<string, any>
}

type AIStatusMap = Record<string, AIStatus>

type ZonePreviewStatus = {
  enabled?: boolean
  status?: string
  stream?: string
  target?: string
  urls?: PlayUrlMap
  last_error?: string | null
  process_alive?: boolean
  zone_count?: number
  zone_source?: string
  owner_task_id?: string | null
}

type ChannelEditState = {
  name: string
  type: string
  source_url: string
  app?: string
  stream?: string
  media_node_id?: string
}

const tabs = [
  { key: 'preview', label: '预览' },
  { key: 'manage', label: '通道管理' },
  { key: 'tasks', label: '任务管理' },
  { key: 'fence', label: '电子围栏' },
] as const

const activeTab = ref<(typeof tabs)[number]['key']>('preview')
const loading = ref(false)
const submitting = ref(false)
const savingEdit = ref(false)
const creatingChannel = ref(false)
const items = ref<any[]>([])
const mediaNodes = ref<any[]>([])
const models = ref<any[]>([])
const playInfos = ref<Record<string, PlayUrlMap>>({})
const browserPlayInfos = ref<Record<string, PlayUrlMap>>({})
const previewPlaybackState = ref<Record<string, boolean>>({})
const zonePreviewPlayInfos = ref<Record<string, FenceBrowserPlayInfo>>({})
const zonePreviewPlaybackState = ref<Record<string, boolean>>({})
const aiStatuses = ref<AIStatusMap>({})
const zonePreviewStatuses = ref<Record<string, ZonePreviewStatus>>({})
const aiConfigs = ref<Record<string, ChannelAIConfigState>>({})
const zonesMap = ref<Record<string, ZoneItem[]>>({})
const tasks = ref<any[]>([])
const availableTaskChannels = ref<any[]>([])
const taskStatuses = ref<Record<string, any>>({})
const taskLoading = ref(false)
const selectedTaskChannelId = ref('')
const selectedTaskId = ref('')
const taskKeyword = ref('')
const debugResults = ref<Record<string, string>>({})
const rtcErrors = ref<Record<string, string>>({})
const zonePreviewDebug = ref<Record<string, string>>({})
const zonePreviewErrors = ref<Record<string, string>>({})
const message = ref('')
const messageType = ref<'success' | 'error'>('success')
const editingChannel = ref<any | null>(null)
const taskModalVisible = ref(false)
const algorithmModalVisible = ref(false)
const editingAlgorithm = ref<any | null>(null)
const algorithmModelOptions = ref<any[]>([])
const taskRegionEditor = ref({
  visible: false,
  taskId: '',
  taskName: '',
  channelId: '',
  algorithmId: '',
  algorithmName: '',
})

const zoneEditor = ref({
  visible: false,
  channelId: '',
  channelName: '',
  modelLabels: [] as string[],
})

const form = ref({
  name: '',
  type: 'rtsp',
  source_url: '',
  model_id: '',
  media_node_id: '',
  record_mode: 'none',
})

const editForm = ref<ChannelEditState>({
  name: '',
  type: 'rtsp',
  source_url: '',
  app: '',
  stream: '',
  media_node_id: '',
})

const videoRefs = new Map<string, HTMLVideoElement>()
const peerConnections = new Map<string, RTCPeerConnection>()
const playAttemptIds = new Map<string, number>()
const pendingBrowserPlayChannels = new Set<string>()
const zonePreviewVideoRefs = new Map<string, HTMLVideoElement>()
const zonePreviewPeerConnections = new Map<string, RTCPeerConnection>()
const zonePreviewAttemptIds = new Map<string, number>()
const previewCardRefs = new Map<string, HTMLElement>()
const fenceCardRefs = new Map<string, HTMLElement>()
const expandedPreview = ref<{ channelId: string; name: string; sourceUrl: string; mode?: 'channel' | 'zone' } | null>(null)
const expandedPreviewVideoRef = ref<HTMLVideoElement | null>(null)
const expandedPreviewPlaying = ref(false)
const expandedPreviewError = ref('')
const aiStatusPollController = createSingleIntervalController({
  schedule(callback: TimerHandler, intervalMs: number) {
    return window.setInterval(callback, intervalMs)
  },
  clear(timer: number) {
    window.clearInterval(timer)
  },
})
let pageDeactivated = false
let expandedPreviewPeerConnection: RTCPeerConnection | null = null
let expandedPreviewAttemptId = 0

const showMessage = (type: 'success' | 'error', text: string) => {
  messageType.value = type
  message.value = text
}

const channelNameById = (channelId?: string) => {
  if (!channelId) return ''
  return items.value.find((item) => item.id === channelId)?.name || ''
}

const filteredTasks = computed(() => {
  const keyword = taskKeyword.value.trim().toLowerCase()
  return tasks.value.filter((task) => {
    if (selectedTaskChannelId.value && task.channel_id !== selectedTaskChannelId.value) {
      return false
    }
    if (!keyword) return true
    const taskName = String(task.name || '').toLowerCase()
    const channelName = channelNameById(task.channel_id).toLowerCase()
    const channelId = String(task.channel_id || '').toLowerCase()
    return (
      taskName.includes(keyword)
      || channelName.includes(keyword)
      || channelId.includes(keyword)
    )
  })
})

const selectedTask = computed(() => {
  return filteredTasks.value.find((task) => task.id === selectedTaskId.value) || null
})

const selectedTaskStatus = computed(() => {
  return selectedTaskId.value ? taskStatuses.value[selectedTaskId.value] || null : null
})

const selectedTaskUsedModelIds = computed(() => {
  return (selectedTask.value?.algorithms || [])
    .map((item: any) => item?.model_id)
    .filter(Boolean)
})

const selectedTaskChannelTasks = computed(() => {
  if (!selectedTaskChannelId.value) return []
  return tasks.value.filter((task) => task.channel_id === selectedTaskChannelId.value)
})

const taskCreateDisabled = computed(() => {
  return Boolean(selectedTaskChannelId.value && selectedTaskChannelTasks.value.length > 0)
})

const taskCreateDisabledReason = computed(() => {
  if (!selectedTaskChannelId.value) {
    return ''
  }
  if (selectedTaskChannelTasks.value.length > 0) {
    return '当前通道已经绑定任务，请直接编辑现有任务。'
  }
  return '当前通道还没有任务，可以直接新建。'
})

const loadAvailableTaskChannels = async () => {
  const result = await listAvailableTaskChannels()
  availableTaskChannels.value = result.items || []
}

const resetCreateForm = () => {
  form.value = {
    name: '',
    type: 'rtsp',
    source_url: '',
    model_id: '',
    media_node_id: '',
    record_mode: 'none',
  }
}

const syncSelectedTask = () => {
  const available = filteredTasks.value
  if (!available.length) {
    selectedTaskId.value = ''
    return
  }
  if (!available.some((item) => item.id === selectedTaskId.value)) {
    selectedTaskId.value = available[0].id
  }
}

const loadTaskStatuses = async (taskIds: string[]) => {
  const statusEntries = await Promise.all(
    taskIds.map(async (taskId) => {
      try {
        const status = await getTaskStatus(taskId)
        return [taskId, status]
      } catch {
        return [taskId, null]
      }
    })
  )
  taskStatuses.value = Object.fromEntries(statusEntries.filter(([_, value]) => value))
}

const loadTasksForCurrentChannel = async () => {
  taskLoading.value = true
  try {
    const result = await listTasks()
    tasks.value = result.items || []
    await loadTaskStatuses(tasks.value.map((task) => task.id))
    syncSelectedTask()
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '加载任务失败')
  } finally {
    taskLoading.value = false
  }
}

const handleTaskChannelChange = async () => {
  taskKeyword.value = ''
  syncSelectedTask()
}

const handleTaskSearch = (value: string) => {
  taskKeyword.value = value
  syncSelectedTask()
}

const loadTaskDetail = async (taskId: string) => {
  const [task, algorithmResult] = await Promise.all([getTask(taskId), listTaskAlgorithms(taskId)])
  return {
    ...task,
    algorithms: algorithmResult?.items || [],
  }
}

const selectTask = async (taskId: string) => {
  selectedTaskId.value = taskId
  try {
    const [task, status] = await Promise.all([loadTaskDetail(taskId), getTaskStatus(taskId)])
    tasks.value = tasks.value.map((item) => (item.id === taskId ? task : item))
    taskStatuses.value = {
      ...taskStatuses.value,
      [taskId]: status,
    }
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '加载任务详情失败')
  }
}

const openTaskCreateModal = async () => {
  if (taskCreateDisabled.value) {
    showMessage('error', taskCreateDisabledReason.value || '当前通道已经存在任务')
    return
  }
  try {
    await loadAvailableTaskChannels()
    taskModalVisible.value = true
    if (!availableTaskChannels.value.length) {
      showMessage('error', '当前没有可用于新建任务的通道')
    }
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '加载可用通道失败')
  }
}

const closeTaskCreateModal = () => {
  taskModalVisible.value = false
}

const refreshModels = async () => {
  const modelResult = await listModels()
  models.value = modelResult.models || modelResult.items || []
  return models.value
}

const handleSyncWvpChannels = async () => {
  try {
    const result = await syncWvpChannels({ page: 1, count: 200 })
    await load()
    showMessage('success', `WVP 通道同步完成，新增或更新 ${result?.synced || 0} 路`)
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '同步 WVP 通道失败')
  }
}

const openAlgorithmCreateModal = async () => {
  if (!selectedTaskId.value) {
    showMessage('error', '请先选择任务')
    return
  }
  try {
    await refreshModels()
    const result = await listTaskAvailableModels(selectedTaskId.value)
    algorithmModelOptions.value = result.items || []
    if (!algorithmModelOptions.value.length) {
      showMessage('error', '当前任务下已没有可新增的模型')
      return
    }
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '加载可选模型失败')
    return
  }
  editingAlgorithm.value = null
  algorithmModalVisible.value = true
}

const openAlgorithmEditModal = async (algorithm: any) => {
  algorithmModelOptions.value = await refreshModels()
  editingAlgorithm.value = algorithm
  algorithmModalVisible.value = true
}

const closeAlgorithmModal = () => {
  algorithmModelOptions.value = []
  editingAlgorithm.value = null
  algorithmModalVisible.value = false
}

const openTaskRegionEditor = (algorithm: any) => {
  if (!selectedTask.value || !algorithm?.id) {
    showMessage('error', '请先选择任务和算法')
    return
  }
  taskRegionEditor.value = {
    visible: true,
    taskId: selectedTask.value.id,
    taskName: selectedTask.value.name || '',
    channelId: selectedTask.value.channel_id || selectedTaskChannelId.value,
    algorithmId: algorithm.id,
    algorithmName: algorithm.model_name || models.value.find((item) => item.model_id === algorithm.model_id)?.name || algorithm.model_id || '',
  }
}

const closeTaskRegionEditor = () => {
  taskRegionEditor.value.visible = false
}

const handleTaskRegionsUpdated = async (payload?: { channelId?: string; zones?: any[] }) => {
  if (!selectedTaskId.value) return
  await loadTasksForCurrentChannel()
  await selectTask(selectedTaskId.value)
  const channelId = payload?.channelId || selectedTask.value?.channel_id || taskRegionEditor.value.channelId
  if (channelId) {
    await loadZonePreviewStatus(channelId)
    await ensureZonePreviewPlayInfo(channelId, true).catch(() => undefined)
  }
}

const submitTaskCreate = async (payload: any) => {
  try {
    if (!payload.name || !payload.channel_id) {
      showMessage('error', '任务名称和通道不能为空')
      return
    }
    const created = await createTask({
      ...payload,
      channel_id: payload.channel_id,
    })
    selectedTaskChannelId.value = payload.channel_id
    taskModalVisible.value = false
    await loadTasksForCurrentChannel()
    selectedTaskId.value = created.id
    await selectTask(created.id)
    showMessage('success', '任务创建成功，请继续新增算法')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '创建任务失败')
  }
}

const saveTaskDraft = async (payload: any) => {
  try {
    await updateTask(payload.id, {
      name: payload.name,
      source_url: payload.source_url,
      frame_interval: payload.frame_interval,
    })
    await loadTasksForCurrentChannel()
    if (payload.id) {
      await selectTask(payload.id)
    }
    showMessage('success', '任务配置已保存')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '保存任务失败')
  }
}

const hasRunnableTaskAlgorithm = (task: any) =>
  Array.isArray(task?.algorithms)
  && task.algorithms.some((algorithm: any) => algorithm?.enabled !== false && algorithm?.model_id)

const startTaskDraft = async (payload: any) => {
  try {
    if (!hasRunnableTaskAlgorithm(payload)) {
      showMessage('error', '请先为任务新增至少一个已启用的算法，并选择模型后再启动任务')
      return
    }
    await updateTask(payload.id, {
      name: payload.name,
      source_url: payload.source_url,
      frame_interval: payload.frame_interval,
    })
    await startTask(payload.id)
    await loadTasksForCurrentChannel()
    await selectTask(payload.id)
    await refreshFenceChannelState(payload.channel_id || selectedTask.value?.channel_id)
    showMessage('success', '任务已启动')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '启动任务失败')
  }
}

const startTaskAction = async (taskId: string) => {
  try {
    const task = tasks.value.find((item) => item.id === taskId)
    if (!hasRunnableTaskAlgorithm(task)) {
      showMessage('error', '请先新增算法并选择模型，再启动任务')
      return
    }
    await startTask(taskId)
    await loadTasksForCurrentChannel()
    await selectTask(taskId)
    await refreshFenceChannelState(task?.channel_id || selectedTask.value?.channel_id)
    showMessage('success', '任务已启动')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '启动任务失败')
  }
}

const stopTaskAction = async (taskId: string) => {
  try {
    const task = tasks.value.find((item) => item.id === taskId)
    await stopTask(taskId)
    await loadTasksForCurrentChannel()
    await selectTask(taskId)
    await refreshFenceChannelState(task?.channel_id || selectedTask.value?.channel_id)
    showMessage('success', '任务已停止')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '停止任务失败')
  }
}

const deleteTaskAction = async (taskId: string) => {
  if (!confirm('确认删除该任务吗？')) return
  try {
    await deleteTask(taskId)
    if (selectedTaskId.value === taskId) {
      selectedTaskId.value = ''
    }
    await loadTasksForCurrentChannel()
    showMessage('success', '任务已删除')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '删除任务失败')
  }
}

const submitAlgorithm = async (payload: any) => {
  if (!selectedTaskId.value) {
    showMessage('error', '请先选择任务')
    return
  }
  if (!payload.model_id) {
    showMessage('error', '请选择模型')
    return
  }
  try {
    if (payload.id) {
      await updateTaskAlgorithm(selectedTaskId.value, payload.id, payload)
    } else {
      await addTaskAlgorithm(selectedTaskId.value, payload)
    }
    closeAlgorithmModal()
    await refreshModels()
    await loadTasksForCurrentChannel()
    await selectTask(selectedTaskId.value)
    showMessage('success', `${payload.id ? '算法已更新' : '算法已新增'}，可在算法卡片中继续进行区域配置`)
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '保存算法失败')
  }
}

const deleteAlgorithmAction = async (algorithm: any) => {
  if (!selectedTaskId.value || !algorithm?.id) return
  if (!confirm('确认删除该算法吗？')) return
  try {
    await deleteTaskAlgorithm(selectedTaskId.value, algorithm.id)
    await loadTasksForCurrentChannel()
    await selectTask(selectedTaskId.value)
    showMessage('success', '算法已删除')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '删除算法失败')
  }
}

const openCreateModal = () => {
  resetCreateForm()
  creatingChannel.value = true
}

const closeCreateModal = () => {
  creatingChannel.value = false
}

const isPreviewPlaying = (channelId: string) => Boolean(previewPlaybackState.value[channelId])
const isZonePreviewPlaying = (channelId: string) => Boolean(zonePreviewPlaybackState.value[channelId])
const makeLocalModelId = () => `local_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`

const createEmptyModelConfig = (): ChannelAIModelConfig => ({
  local_id: makeLocalModelId(),
  model_id: '',
  enabled: true,
  labels: [],
  conf: 0.25,
  frame_interval: 10,
  zone_ids: [],
  save_snapshot: true,
})

const modelDisplayName = (model: any) => {
  const labelCount = Array.isArray(model?.classes) ? model.classes.length : 0
  const primary = model?.name || model?.model_id || '-'
  const secondary = model?.name && model?.model_id ? ` (${model.model_id})` : ''
  return `${primary}${secondary}${labelCount > 0 ? ` (${labelCount} 类)` : ''}`
}

// const modelRuntimeEntries = (channelId: string) =>
//   Object.entries(aiStatuses.value[channelId]?.models || {})
//
// const modelRuntimeName = (modelId: string) => {
//   const model = getModelById(modelId)
//   return model ? modelDisplayName(model) : modelId
// }
//
// const joinLabels = (values?: string[]) => (values && values.length ? values.join('、') : '-')

const getModelById = (modelId: string | undefined) => {
  if (!modelId) return null
  return models.value.find((model) => model.model_id === modelId) || null
}

const normalizeChannelModelConfigs = (config: any): ChannelAIModelConfig[] => {
  if (Array.isArray(config?.models) && config.models.length) {
    return config.models
      .filter((item: any) => item && item.model_id)
      .map((item: any) => ({
        local_id: item.local_id || makeLocalModelId(),
        model_id: item.model_id || '',
        enabled: item.enabled !== false,
        labels: Array.isArray(item.labels) ? item.labels : [],
        conf: Number(item.conf ?? config.conf ?? 0.25),
        frame_interval: Number(item.frame_interval ?? config.frame_interval ?? 10),
        zone_ids: Array.isArray(item.zone_ids) ? item.zone_ids : [],
        save_snapshot: Boolean(item.save_snapshot ?? config.save_snapshot ?? true),
      }))
  }

  if (config?.model_id) {
    return [{
      local_id: makeLocalModelId(),
      model_id: config.model_id || '',
      enabled: Boolean(config.enabled),
      labels: Array.isArray(config.labels) ? config.labels : [],
      conf: Number(config.conf ?? 0.25),
      frame_interval: Number(config.frame_interval ?? 10),
      zone_ids: Array.isArray(config.zone_ids) ? config.zone_ids : [],
      save_snapshot: Boolean(config.save_snapshot ?? true),
    }]
  }

  return [createEmptyModelConfig()]
}

const channelZones = (channelId: string) => zonesMap.value[channelId] || []
const loadChannelZones = async (channelId: string) => {
  try {
    const result = await listChannelZones(channelId)
    zonesMap.value = {
      ...zonesMap.value,
      [channelId]: result.items || [],
    }
    const config = aiConfigs.value[channelId]
    if (config) {
      const validZoneIds = new Set((result.items || []).map((zone: any) => zone.id))
      config.zone_ids = config.zone_ids.filter((zoneId) => validZoneIds.has(zoneId))
    }
  } catch {
    zonesMap.value = {
      ...zonesMap.value,
      [channelId]: [],
    }
    const config = aiConfigs.value[channelId]
    if (config) {
      config.zone_ids = []
    }
  }
}

const zonePreviewLabel = (channelId: string) => {
  const status = zonePreviewStatuses.value[channelId]
  if (!status) return '未启动'
  if (status.status === 'running') return '运行中'
  if (status.status === 'starting') return '启动中'
  if (status.status === 'error') return '异常'
  return '未启动'
}

const zonePreviewRegionCount = (channelId: string) => {
  const status = zonePreviewStatuses.value[channelId] || {}
  if (typeof status.zone_count === 'number') {
    return status.zone_count
  }
  return channelZones(channelId).length
}

const zonePreviewSourceLabel = (channelId: string) => {
  const status = zonePreviewStatuses.value[channelId] || {}
  if (status.zone_source === 'task') {
    return '任务算法区域'
  }
  return '通道区域'
}

const taskForChannel = (channelId: string) =>
  tasks.value.find((task) => task.channel_id === channelId) || null

const isTaskRunningForChannel = (channelId: string) => {
  const task = taskForChannel(channelId)
  if (!task?.id) return false
  const status = taskStatuses.value[task.id]
  const runtime = status?.runtime || task?.runtime || {}
  const normalized = String(status?.status || runtime?.status || task?.status || '').toLowerCase()
  return Boolean(runtime?.is_running) || ['running', 'starting', 'restarting'].includes(normalized)
}

const canManualPlayZonePreview = (channelId: string) => isTaskRunningForChannel(channelId)

const zonePreviewMaskText = (channelId: string) => {
  if (!taskForChannel(channelId)) {
    return '当前通道还没有绑定任务'
  }
  if (!isTaskRunningForChannel(channelId)) {
    return '请先启动任务后再播放'
  }
  if (zonePreviewStatuses.value[channelId]?.status === 'error') {
    return '区域流生成异常，请检查任务或区域配置'
  }
  if (zonePreviewStatuses.value[channelId]?.status !== 'running') {
    return '任务已启动，正在准备区域流'
  }
  return '点击播放查看区域预览'
}

const refreshFenceChannelState = async (channelId?: string) => {
  if (!channelId) return
  await loadZonePreviewStatus(channelId)
  const status = zonePreviewStatuses.value[channelId]
  if (status?.status === 'running') {
    await ensureZonePreviewPlayInfo(channelId, true).catch(() => undefined)
  } else {
    await stopZonePreviewPlayer(channelId)
  }
}

const loadZonePreviewStatus = async (channelId: string) => {
  try {
    const result = await getChannelZonePreviewStatus(channelId)
    zonePreviewStatuses.value = {
      ...zonePreviewStatuses.value,
      [channelId]: result || {},
    }
  } catch {
    zonePreviewStatuses.value = {
      ...zonePreviewStatuses.value,
      [channelId]: {},
    }
  }
}

const loadAllZonePreviewStatuses = async () => {
  await Promise.all(items.value.map((item) => loadZonePreviewStatus(item.id)))
}

const loadAllZones = async () => {
  await Promise.all(items.value.map((item) => loadChannelZones(item.id)))
}

const loadAIStatuses = async () => {
  const entries = await Promise.all(
    items.value.map(async (item) => {
      try {
        const status = await getChannelAIStatus(item.id)
        return [item.id, status] as const
      } catch {
        return [item.id, null] as const
      }
    })
  )

  const nextMap: AIStatusMap = {}
  for (const [channelId, status] of entries) {
    if (status) nextMap[channelId] = status
  }
  aiStatuses.value = nextMap
}

const syncAIConfigsFromItems = () => {
  const nextConfigs: Record<string, ChannelAIConfigState> = {}
  for (const item of items.value) {
    const config = item?.ext?.ai_config || {}
    const models = normalizeChannelModelConfigs(config)
    nextConfigs[item.id] = {
      enabled: Boolean(config.enabled),
      model_id: config.model_id || '',
      labels: Array.isArray(config.labels) ? config.labels : [],
      conf: Number(config.conf ?? 0.25),
      frame_interval: Number(config.frame_interval ?? 10),
      zone_ids: Array.isArray(config.zone_ids) ? config.zone_ids : [],
      save_snapshot: Boolean(config.save_snapshot ?? true),
      models,
    }
  }
  aiConfigs.value = nextConfigs
}

const handleZonePreviewAdvancedToggle = async (channelId: string, event: Event) => {
  const target = event.target as HTMLDetailsElement | null
  if (!target?.open) return
  try {
    await ensureZonePreviewPlayInfo(channelId, true)
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '加载区域流地址失败')
  }
}

const copyZonePreviewUrl = async (channelId: string) => {
  try {
    const result = await getChannelFenceBrowserPlayUrl(channelId)
    const playUrls = result?.play_url || {}
    zonePreviewPlayInfos.value = {
      ...zonePreviewPlayInfos.value,
      [channelId]: playUrls,
    }
    zonePreviewStatuses.value = {
      ...zonePreviewStatuses.value,
      [channelId]: {
        ...(zonePreviewStatuses.value[channelId] || {}),
        ...(result || {}),
      },
    }
    const url = playUrls?.hls || playUrls?.webrtc_api
    if (!url) {
      throw new Error('当前没有可复制的电子围栏浏览器播放地址')
    }
    await navigator.clipboard.writeText(url)
    showMessage('success', '已复制电子围栏浏览器播放地址')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '复制电子围栏浏览器播放地址失败')
  }
}

const ensureZonePreviewPlayInfo = async (channelId: string, forceRefresh = false) => {
  if (!forceRefresh && zonePreviewPlayInfos.value[channelId]) {
    return zonePreviewPlayInfos.value[channelId] || {}
  }

  const result = await getChannelFenceBrowserPlayUrl(channelId)
  const urls = result?.play_url || {}
  zonePreviewPlayInfos.value = {
    ...zonePreviewPlayInfos.value,
    [channelId]: urls,
  }
  zonePreviewStatuses.value = {
    ...zonePreviewStatuses.value,
    [channelId]: {
      ...(zonePreviewStatuses.value[channelId] || {}),
      ...(result || {}),
    },
  }
  return urls
}

const stopZonePreviewPlayer = async (channelId: string, clearAttempt = true) => {
  if (clearAttempt) {
    nextZonePreviewAttempt(channelId)
  }

  const pc = zonePreviewPeerConnections.get(channelId)
  if (pc) {
    pc.getReceivers().forEach((receiver) => receiver.track?.stop())
    pc.close()
    zonePreviewPeerConnections.delete(channelId)
  }

  const video = zonePreviewVideoRefs.get(channelId)
  if (video) {
    video.pause()
    video.srcObject = null
    video.removeAttribute('src')
    video.load()
  }

  zonePreviewPlaybackState.value = {
    ...zonePreviewPlaybackState.value,
    [channelId]: false,
  }
}

const resetAllZonePreviewPlayers = async () => {
  const channelIds = new Set<string>([
    ...Object.keys(zonePreviewPlaybackState.value || {}),
    ...Array.from(zonePreviewPeerConnections.keys()),
  ])
  await Promise.all(Array.from(channelIds).map((channelId) => stopZonePreviewPlayer(channelId)))
  zonePreviewPlaybackState.value = {}
}

const startZonePreviewPlayer = async (channelId: string) => {
  if (!canManualPlayZonePreview(channelId)) {
    zonePreviewErrors.value[channelId] = zonePreviewMaskText(channelId)
    return
  }
  const attemptId = nextZonePreviewAttempt(channelId)
  let video: HTMLVideoElement | null = null

  try {
    zonePreviewErrors.value[channelId] = ''
    await stopZonePreviewPlayer(channelId, false)
    await nextTick()
    await loadZonePreviewStatus(channelId)

    if (zonePreviewStatuses.value[channelId]?.status !== 'running') {
      await startChannelZonePreview(channelId)
      await loadZonePreviewStatus(channelId)
    }

    const urls = await ensureZonePreviewPlayInfo(channelId, true)
    const apiUrl = urls.webrtc_api
    if (!apiUrl) {
      throw new Error('没有可用的区域流 WebRTC 地址')
    }

    video = await getZonePreviewVideoEl(channelId)
    if (!isActiveZonePreviewAttempt(channelId, attemptId)) return

    const pc = new RTCPeerConnection({ iceServers: [] })
    zonePreviewPeerConnections.set(channelId, pc)

    const remoteStream = new MediaStream()
    video.srcObject = remoteStream

    pc.addTransceiver('video', { direction: 'recvonly' })
    pc.addTransceiver('audio', { direction: 'recvonly' })

    pc.ontrack = (event) => {
      if (!isActiveZonePreviewAttempt(channelId, attemptId)) return
      if (event.streams?.length) {
        event.streams[0].getTracks().forEach((track) => remoteStream.addTrack(track))
      } else {
        remoteStream.addTrack(event.track)
      }
    }

    pc.onconnectionstatechange = () => {
      if (
        isActiveZonePreviewAttempt(channelId, attemptId) &&
        ['failed', 'disconnected', 'closed'].includes(pc.connectionState)
      ) {
        zonePreviewErrors.value[channelId] = `区域预览连接异常：${pc.connectionState}`
      }
    }

    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await waitIceComplete(pc)

    const response = await fetch(apiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain;charset=utf-8' },
      body: pc.localDescription?.sdp || offer.sdp || '',
    })

    if (!response.ok) throw new Error(await response.text())

    const answer = await response.json()
    if (answer.code !== 0 || !answer.sdp) {
      throw new Error(answer.msg || '区域预览 WebRTC 应答 SDP 缺失')
    }

    if (!isActiveZonePreviewAttempt(channelId, attemptId)) return

    await pc.setRemoteDescription({
      type: answer.type || 'answer',
      sdp: answer.sdp,
    })

    await safePlayWithCheck(video, channelId, attemptId, isActiveZonePreviewAttempt)
    if (isActiveZonePreviewAttempt(channelId, attemptId)) {
      zonePreviewPlaybackState.value = {
        ...zonePreviewPlaybackState.value,
        [channelId]: true,
      }
      zonePreviewDebug.value[channelId] = '区域流已通过 WebRTC 预览'
      zonePreviewErrors.value[channelId] = ''
    }
  } catch (error: any) {
    if (!isActiveZonePreviewAttempt(channelId, attemptId)) return
    const currentMessage = error?.message || '区域预览播放失败'
    zonePreviewErrors.value[channelId] = `WebRTC 自动播放失败：${currentMessage}`

    try {
      const urls = await ensureZonePreviewPlayInfo(channelId, true)
      const videoEl = video || (await getZonePreviewVideoEl(channelId))
      const hlsUrl = urls.hls
      if (!hlsUrl) {
        throw new Error('没有可用的区域流 HLS 地址')
      }
      videoEl.srcObject = null
      videoEl.src = hlsUrl
      videoEl.load()
      await safePlayWithCheck(videoEl, channelId, attemptId, isActiveZonePreviewAttempt)
      if (isActiveZonePreviewAttempt(channelId, attemptId)) {
        zonePreviewPlaybackState.value = {
          ...zonePreviewPlaybackState.value,
          [channelId]: true,
        }
        zonePreviewDebug.value[channelId] = '区域流 WebRTC 失败，已切换到 HLS 预览'
        zonePreviewErrors.value[channelId] = `WebRTC 自动播放失败：${currentMessage}；已切换到 HLS`
      }
    } catch (fallbackError: any) {
      if (isActiveZonePreviewAttempt(channelId, attemptId)) {
        zonePreviewErrors.value[channelId] =
          `WebRTC 自动播放失败：${currentMessage}；HLS 回退也失败：${fallbackError?.message || fallbackError}`
      }
    }
  }
}

const load = async () => {
  loading.value = true
  try {
    const [channelResult, mediaNodeResult] = await Promise.all([
      listChannels(),
      listMediaNodes(),
      refreshModels(),
    ])
    items.value = channelResult.items || []
    const validIds = new Set(items.value.map((item) => item.id))
    previewPlaybackState.value = Object.fromEntries(
      Object.entries(previewPlaybackState.value).filter(([channelId]) => validIds.has(channelId))
    )
    mediaNodes.value = mediaNodeResult.items || []
    if (
      selectedTaskChannelId.value
      && !items.value.some((item) => item.id === selectedTaskChannelId.value)
    ) {
      selectedTaskChannelId.value = ''
    }
    syncAIConfigsFromItems()
    await Promise.all([loadAllZones(), loadAIStatuses(), loadAllZonePreviewStatuses(), loadTasksForCurrentChannel()])
    if (activeTab.value === 'preview') {
      await nextTick()
      await restoreBrowserPlaybackForActiveChannels()
    }
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '加载通道失败')
  } finally {
    loading.value = false
  }
}

const restoreBrowserPlaybackForActiveChannels = async () => {
  if (activeTab.value !== 'preview') return
  await nextTick()
  const onlineItems = items.value.filter((item) => {
    if (!item.is_online) return false
    const card = previewCardRefs.get(item.id)
    if (!card) return true
    const rect = card.getBoundingClientRect()
    return rect.bottom >= -120 && rect.top <= window.innerHeight + 120
  })
  for (const item of onlineItems) {
    if (pendingBrowserPlayChannels.has(item.id) || isPreviewPlaying(item.id)) {
      continue
    }
    try {
      await startBrowserPlay(item.id)
    } catch (error: any) {
      debugResults.value[item.id] = error?.message || '恢复浏览器播放失败'
    }
  }
}

const initializePage = async () => {
  await load()
  await restoreBrowserPlaybackForActiveChannels()
}

const startAiStatusPolling = () => {
  if (!shouldPollChannelAiStatus(activeTab.value)) {
    return
  }
  aiStatusPollController.start(() => {
    if (shouldPollChannelAiStatus(activeTab.value) && items.value.length > 0) {
      void loadAIStatuses()
    }
  }, 5000)
}

const stopAiStatusPolling = () => {
  aiStatusPollController.stop()
}

const aiStatusLabel = (channelId: string) => {
  const status = aiStatuses.value[channelId]
  if (!status) return '未知'
  if (status.is_running) return '运行中'
  switch (status.status) {
    case 'starting':
      return '启动中'
    case 'running':
      return '运行中'
    case 'stopping':
      return '停止中'
    case 'error':
      return '异常'
    case 'stopped':
      return '已停止'
    default:
      return status.status || '未知'
  }
}

const setVideoRef = (channelId: string, el: HTMLVideoElement | null) => {
  if (el) {
    videoRefs.set(channelId, el)
  } else {
    videoRefs.delete(channelId)
  }
}

const setZonePreviewVideoRef = (channelId: string, el: HTMLVideoElement | null) => {
  if (el) {
    zonePreviewVideoRefs.set(channelId, el)
  } else {
    zonePreviewVideoRefs.delete(channelId)
  }
}

const setPreviewCardRef = (channelId: string, el: HTMLElement | null) => {
  if (el) {
    previewCardRefs.set(channelId, el)
  } else {
    previewCardRefs.delete(channelId)
  }
}

const setFenceCardRef = (channelId: string, el: HTMLElement | null) => {
  if (el) {
    fenceCardRefs.set(channelId, el)
  } else {
    fenceCardRefs.delete(channelId)
  }
}

const nextPlayAttempt = (channelId: string) => {
  const nextId = (playAttemptIds.get(channelId) || 0) + 1
  playAttemptIds.set(channelId, nextId)
  return nextId
}

const isActiveAttempt = (channelId: string, attemptId: number) => playAttemptIds.get(channelId) === attemptId

const nextZonePreviewAttempt = (channelId: string) => {
  const nextId = (zonePreviewAttemptIds.get(channelId) || 0) + 1
  zonePreviewAttemptIds.set(channelId, nextId)
  return nextId
}

const isActiveZonePreviewAttempt = (channelId: string, attemptId: number) =>
  zonePreviewAttemptIds.get(channelId) === attemptId

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

const waitIceComplete = (pc: RTCPeerConnection) => {
  if (pc.iceGatheringState === 'complete') return Promise.resolve()
  return new Promise<void>((resolve) => {
    const handler = () => {
      if (pc.iceGatheringState === 'complete') {
        pc.removeEventListener('icegatheringstatechange', handler)
        resolve()
      }
    }
    pc.addEventListener('icegatheringstatechange', handler)
  })
}

const fetchAiConfigUpdate = async (channelId: string, modelId: string) => {
  const model = getModelById(modelId)
  await updateChannelAIConfig(channelId, {
    enabled: true,
    model_id: modelId,
    labels: Array.isArray(model?.classes) ? model.classes : [],
    conf: 0.25,
    frame_interval: 10,
    zone_ids: [],
    save_snapshot: true,
  })
}

const submit = async () => {
  if (!form.value.name || !form.value.source_url) {
    showMessage('error', '通道名称和源地址不能为空')
    return
  }

  submitting.value = true
  message.value = ''
  try {
    const created = await createChannel({
      name: form.value.name,
      type: form.value.type,
      source_url: form.value.source_url,
      media_node_id: form.value.media_node_id || undefined,
      record_mode: form.value.record_mode,
    })

    if (form.value.model_id) {
      await fetchAiConfigUpdate(created.id, form.value.model_id)
    }

    resetCreateForm()

    await load()
    closeCreateModal()
    showMessage('success', '通道创建成功')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '创建通道失败')
  } finally {
    submitting.value = false
  }
}

const openEdit = (item: any) => {
  editingChannel.value = item
  editForm.value = {
    name: item.name || '',
    type: item.type || 'rtsp',
    source_url: item.source_url || '',
    app: item.app || '',
    stream: item.stream || '',
    media_node_id: item.media_node_id || '',
  }
}

const closeEdit = () => {
  editingChannel.value = null
}

const saveEdit = async () => {
  if (!editingChannel.value) return
  savingEdit.value = true
  try {
    await updateChannel(editingChannel.value.id, editForm.value)
    closeEdit()
    await load()
    showMessage('success', '通道更新成功')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '通道更新失败')
  } finally {
    savingEdit.value = false
  }
}

const removeChannel = async (channelId: string) => {
  if (!confirm(`确认删除通道 ${channelId} 吗？`)) return
  try {
    await stopBrowserPlay(channelId)
    await deleteChannel(channelId)
    await load()
    showMessage('success', '通道已删除')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '通道删除失败')
  }
}

const ensurePlayInfo = async (channelId: string, forceRefresh = false) => {
  if (!forceRefresh && (playInfos.value[channelId] || browserPlayInfos.value[channelId])) {
    return {
      urls: playInfos.value[channelId] || {},
      browser_urls: browserPlayInfos.value[channelId] || {},
    }
  }

  const result = await getChannelPlayUrl(channelId, 'channel')
  playInfos.value[channelId] = result.play_urls || result.urls || {}
  browserPlayInfos.value[channelId] = result.browser_play_urls || result.browser_urls || {}
  return {
    urls: playInfos.value[channelId] || {},
    browser_urls: browserPlayInfos.value[channelId] || {},
  }
}

const hasUsableBrowserUrls = (urls?: PlayUrlMap | null) => !!urls && Object.keys(urls).length > 0

const ensureBrowserPlaybackUrls = async (channelId: string) => {
  let playInfo = await ensurePlayInfo(channelId)
  if (hasUsableBrowserUrls(playInfo.browser_urls)) {
    return playInfo.browser_urls
  }

  const channel = items.value.find((item) => item.id === channelId)
  if (!channel?.is_online) {
    return null
  }

  const result = await startChannelPlay(channelId)
  playInfos.value[channelId] = result.play_urls || result.urls || {}
  browserPlayInfos.value[channelId] = result.browser_play_urls || result.browser_urls || {}
  playInfo = {
    urls: playInfos.value[channelId] || {},
    browser_urls: browserPlayInfos.value[channelId] || {},
  }

  if (hasUsableBrowserUrls(playInfo.browser_urls)) {
    return playInfo.browser_urls
  }

  for (let i = 0; i < 6; i += 1) {
    await sleep(500)
    playInfo = await ensurePlayInfo(channelId, true)
    if (hasUsableBrowserUrls(playInfo.browser_urls)) {
      return playInfo.browser_urls
    }
  }

  return null
}

const getVideoEl = async (channelId: string) => {
  for (let i = 0; i < 6; i += 1) {
    const video = videoRefs.get(channelId)
    if (video) return video
    await nextTick()
    if (i < 5) {
      await sleep(80)
    }
  }
  throw new Error('播放器尚未初始化')
}

const getZonePreviewVideoEl = async (channelId: string) => {
  for (let i = 0; i < 6; i += 1) {
    const video = zonePreviewVideoRefs.get(channelId)
    if (video) return video
    await nextTick()
    if (i < 5) {
      await sleep(80)
    }
  }
  throw new Error('区域预览播放器尚未初始化')
}

const safePlayWithCheck = async (
  video: HTMLVideoElement,
  channelId: string,
  attemptId: number,
  isActive: (channelId: string, attemptId: number) => boolean
) => {
  try {
    await video.play()
  } catch (error: any) {
    const text = String(error?.message || '')
    if (
      !isActive(channelId, attemptId) ||
      error?.name === 'AbortError' ||
      text.includes('interrupted by a call to pause') ||
      text.includes('interrupted by a new load request')
    ) {
      return
    }
    throw error
  }
}

const safePlay = async (video: HTMLVideoElement, channelId: string, attemptId: number) =>
  safePlayWithCheck(video, channelId, attemptId, isActiveAttempt)

const tryHlsFallback = async (
  channelId: string,
  urls: PlayUrlMap,
  attemptId: number,
  video?: HTMLVideoElement | null
) => {
  const targetVideo = video || (await getVideoEl(channelId))
  const hlsUrl = urls.browser_hls
  if (!hlsUrl) throw new Error('没有可用的 HLS-fMP4 地址')
  if (!isActiveAttempt(channelId, attemptId)) return
  targetVideo.srcObject = null
  targetVideo.src = hlsUrl
  targetVideo.load()
  await safePlay(targetVideo, channelId, attemptId)
  if (isActiveAttempt(channelId, attemptId)) {
    previewPlaybackState.value = {
      ...previewPlaybackState.value,
      [channelId]: true,
    }
    debugResults.value[channelId] = 'WebRTC 播放失败，已切换到 HLS-fMP4 回退播放'
  }
}

const nextExpandedPreviewAttempt = () => {
  expandedPreviewAttemptId += 1
  return expandedPreviewAttemptId
}

const isActiveExpandedPreviewAttempt = (attemptId: number) => expandedPreviewAttemptId === attemptId

const getExpandedPreviewVideoEl = async () => {
  for (let i = 0; i < 6; i += 1) {
    if (expandedPreviewVideoRef.value) return expandedPreviewVideoRef.value
    await nextTick()
    if (i < 5) {
      await sleep(80)
    }
  }
  throw new Error('放大播放器尚未初始化')
}

const stopExpandedPreviewPlayback = async (clearAttempt = true) => {
  if (clearAttempt) {
    nextExpandedPreviewAttempt()
  }

  if (expandedPreviewPeerConnection) {
    expandedPreviewPeerConnection.getReceivers().forEach((receiver) => receiver.track?.stop())
    expandedPreviewPeerConnection.close()
    expandedPreviewPeerConnection = null
  }

  const video = expandedPreviewVideoRef.value
  if (video) {
    video.pause()
    video.srcObject = null
    video.removeAttribute('src')
    video.load()
  }

  expandedPreviewPlaying.value = false
}

const startExpandedPreviewPlayback = async (channelId: string) => {
  const attemptId = nextExpandedPreviewAttempt()
  let autoStartedProxy = false
  const previewMode = expandedPreview.value?.mode || 'channel'

  try {
    expandedPreviewError.value = ''
    await stopExpandedPreviewPlayback(false)
    await nextTick()

    const selectedUrls =
      previewMode === 'zone'
        ? await ensureZonePreviewPlayInfo(channelId, true)
        : await ensureBrowserPlaybackUrls(channelId)
    const playUrls = selectedUrls || {}
    const apiUrl = playUrls?.browser_webrtc_api

    if (!apiUrl) throw new Error(previewMode === 'zone' ? '没有可用的 WebRTC API 地址' : '浏览器转码流尚未就绪')

    const video = await getExpandedPreviewVideoEl()
    if (!isActiveExpandedPreviewAttempt(attemptId)) return

    const pc = new RTCPeerConnection({ iceServers: [] })
    expandedPreviewPeerConnection = pc

    const remoteStream = new MediaStream()
    video.srcObject = remoteStream

    pc.addTransceiver('video', { direction: 'recvonly' })
    pc.addTransceiver('audio', { direction: 'recvonly' })

    pc.ontrack = (event) => {
      if (!isActiveExpandedPreviewAttempt(attemptId)) return
      if (event.streams?.length) {
        event.streams[0].getTracks().forEach((track) => remoteStream.addTrack(track))
      } else {
        remoteStream.addTrack(event.track)
      }
    }

    pc.onconnectionstatechange = () => {
      if (
        isActiveExpandedPreviewAttempt(attemptId) &&
        ['failed', 'disconnected', 'closed'].includes(pc.connectionState)
      ) {
        expandedPreviewError.value = `放大预览连接异常：${pc.connectionState}`
      }
    }

    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await waitIceComplete(pc)

    const response = await fetch(apiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain;charset=utf-8' },
      body: pc.localDescription?.sdp || offer.sdp || '',
    })

    if (!response.ok) throw new Error(await response.text())

    const answer = await response.json()
    if (answer.code !== 0 || !answer.sdp) {
      throw new Error(answer.msg || 'WebRTC 应答 SDP 缺失')
    }

    if (!isActiveExpandedPreviewAttempt(attemptId)) return

    await pc.setRemoteDescription({
      type: answer.type || 'answer',
      sdp: answer.sdp,
    })

    try {
      await video.play()
    } catch (error: any) {
      const text = String(error?.message || '')
      if (
        !isActiveExpandedPreviewAttempt(attemptId) ||
        error?.name === 'AbortError' ||
        text.includes('interrupted by a call to pause') ||
        text.includes('interrupted by a new load request')
      ) {
        return
      }
      throw error
    }

    if (isActiveExpandedPreviewAttempt(attemptId)) {
      expandedPreviewPlaying.value = true
    }
  } catch (error: any) {
    if (!isActiveExpandedPreviewAttempt(attemptId)) return
    const currentMessage = error?.message || '放大预览播放失败'
    const fallbackUrls =
      previewMode === 'zone'
        ? await ensureZonePreviewPlayInfo(channelId, true)
        : (browserPlayInfos.value[channelId] || {})

    if (!autoStartedProxy && previewMode === 'channel' && currentMessage.includes('stream not found')) {
      try {
        const result = await startChannelPlay(channelId)
        playInfos.value[channelId] = result.play_urls || result.urls || {}
        browserPlayInfos.value[channelId] = result.browser_play_urls || result.browser_urls || {}
        autoStartedProxy = true
        await sleep(800)
      } catch {
        // Ignore and continue with retry/fallback flow below.
      }
    }

    if (previewMode === 'channel' && Object.keys(browserPlayInfos.value[channelId] || {}).length > 0 && currentMessage.includes('stream not found')) {
      for (let i = 0; i < 6; i += 1) {
        if (!isActiveExpandedPreviewAttempt(attemptId)) return
        await sleep(500)
        try {
          const refreshed = await ensurePlayInfo(channelId, true)
          const retryUrls = refreshed.browser_urls || {}
          const retryApiUrl = retryUrls.browser_webrtc_api
          if (!retryApiUrl) continue

          const retryVideo = await getExpandedPreviewVideoEl()
          const retryPc = new RTCPeerConnection({ iceServers: [] })
          expandedPreviewPeerConnection = retryPc

          const remoteStream = new MediaStream()
          retryVideo.srcObject = remoteStream

          retryPc.addTransceiver('video', { direction: 'recvonly' })
          retryPc.addTransceiver('audio', { direction: 'recvonly' })

          retryPc.ontrack = (event) => {
            if (!isActiveExpandedPreviewAttempt(attemptId)) return
            if (event.streams?.length) {
              event.streams[0].getTracks().forEach((track) => remoteStream.addTrack(track))
            } else {
              remoteStream.addTrack(event.track)
            }
          }

          const offer = await retryPc.createOffer()
          await retryPc.setLocalDescription(offer)
          await waitIceComplete(retryPc)

          const response = await fetch(retryApiUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'text/plain;charset=utf-8' },
            body: retryPc.localDescription?.sdp || offer.sdp || '',
          })
          if (!response.ok) throw new Error(await response.text())

          const answer = await response.json()
          if (answer.code !== 0 || !answer.sdp) {
            throw new Error(answer.msg || 'WebRTC 应答 SDP 缺失')
          }

          await retryPc.setRemoteDescription({
            type: answer.type || 'answer',
            sdp: answer.sdp,
          })

          await retryVideo.play()
          if (isActiveExpandedPreviewAttempt(attemptId)) {
            expandedPreviewPlaying.value = true
            expandedPreviewError.value = ''
          }
          return
        } catch {
          continue
        }
      }
    }

    try {
      const video = await getExpandedPreviewVideoEl()
      const hlsUrl = fallbackUrls.browser_hls || fallbackUrls.hls
      if (!hlsUrl) throw new Error('没有可用的 HLS-fMP4 地址')
      video.srcObject = null
      video.src = hlsUrl
      video.load()
      await video.play()
      if (isActiveExpandedPreviewAttempt(attemptId)) {
        expandedPreviewPlaying.value = true
        expandedPreviewError.value = `WebRTC 播放失败：${currentMessage}；已切换到 HLS-fMP4`
      }
    } catch (fallbackError: any) {
      if (isActiveExpandedPreviewAttempt(attemptId)) {
        expandedPreviewError.value =
          `WebRTC 播放失败：${currentMessage}；HLS-fMP4 回退也失败：${fallbackError?.message || fallbackError}`
      }
    }
  }
}

const stopBrowserPlay = async (channelId: string, clearAttempt = true) => {
  if (clearAttempt) {
    nextPlayAttempt(channelId)
  }

  const pc = peerConnections.get(channelId)
  if (pc) {
    pc.getReceivers().forEach((receiver) => receiver.track?.stop())
    pc.close()
    peerConnections.delete(channelId)
  }

  const video = videoRefs.get(channelId)
  if (video) {
    video.pause()
    video.srcObject = null
    video.removeAttribute('src')
    video.load()
  }

  previewPlaybackState.value = {
    ...previewPlaybackState.value,
    [channelId]: false,
  }
  rtcErrors.value[channelId] = ''
}

const startBrowserPlay = async (channelId: string) => {
  if (pendingBrowserPlayChannels.has(channelId)) {
    return
  }

  pendingBrowserPlayChannels.add(channelId)
  const attemptId = nextPlayAttempt(channelId)
  let video: HTMLVideoElement | null = null
  let autoStartedProxy = false

  try {
    rtcErrors.value[channelId] = ''
    await stopBrowserPlay(channelId, false)
    await nextTick()

    const browserUrls = (await ensureBrowserPlaybackUrls(channelId)) || {}
    const apiUrl = browserUrls.browser_webrtc_api

    if (!apiUrl) throw new Error('浏览器转码流尚未就绪')

    video = await getVideoEl(channelId)
    if (!isActiveAttempt(channelId, attemptId)) return

    const pc = new RTCPeerConnection({ iceServers: [] })
    peerConnections.set(channelId, pc)

    const remoteStream = new MediaStream()
    video.srcObject = remoteStream

    pc.addTransceiver('video', { direction: 'recvonly' })
    pc.addTransceiver('audio', { direction: 'recvonly' })

    pc.ontrack = (event) => {
      if (!isActiveAttempt(channelId, attemptId)) return
      if (event.streams?.length) {
        event.streams[0].getTracks().forEach((track) => remoteStream.addTrack(track))
      } else {
        remoteStream.addTrack(event.track)
      }
    }

    pc.onconnectionstatechange = () => {
      if (
        isActiveAttempt(channelId, attemptId) &&
        ['failed', 'disconnected', 'closed'].includes(pc.connectionState)
      ) {
        rtcErrors.value[channelId] = `WebRTC 连接状态异常：${pc.connectionState}`
      }
    }

    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await waitIceComplete(pc)

    const response = await fetch(apiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain;charset=utf-8' },
      body: pc.localDescription?.sdp || offer.sdp || '',
    })

    if (!response.ok) throw new Error(await response.text())

    const answer = await response.json()
    if (answer.code !== 0 || !answer.sdp) {
      throw new Error(answer.msg || 'WebRTC 应答 SDP 缺失')
    }

    if (!isActiveAttempt(channelId, attemptId)) return

    await pc.setRemoteDescription({
      type: answer.type || 'answer',
      sdp: answer.sdp,
    })

    await safePlay(video, channelId, attemptId)
    if (isActiveAttempt(channelId, attemptId)) {
      previewPlaybackState.value = {
        ...previewPlaybackState.value,
        [channelId]: true,
      }
      debugResults.value[channelId] = '浏览器专用转码流已通过 WebRTC 播放'
    }
  } catch (error: any) {
    if (!isActiveAttempt(channelId, attemptId)) return
    const currentMessage = error?.message || '浏览器播放失败'
    const playInfo = await ensurePlayInfo(channelId, true)
    const browserUrls = playInfo.browser_urls || {}
    const fallbackUrls = browserUrls

    if (!autoStartedProxy && currentMessage.includes('stream not found')) {
      try {
        const result = await startChannelPlay(channelId)
        playInfos.value[channelId] = result.play_urls || result.urls || {}
        browserPlayInfos.value[channelId] = result.browser_play_urls || result.browser_urls || {}
        autoStartedProxy = true
        await sleep(800)
      } catch {
        // Ignore here and continue with the existing fallback flow below.
      }
    }

    if (currentMessage.includes('stream not found')) {
      for (let i = 0; i < 6; i += 1) {
        if (!isActiveAttempt(channelId, attemptId)) return
        await sleep(500)
        try {
          const refreshed = await ensurePlayInfo(channelId, true)
          const retryUrls = refreshed.browser_urls || {}
          const retryApiUrl = retryUrls.browser_webrtc_api
          if (!retryApiUrl) continue

          const retryVideo = video || (await getVideoEl(channelId))
          const retryPc = new RTCPeerConnection({ iceServers: [] })
          peerConnections.set(channelId, retryPc)

          const remoteStream = new MediaStream()
          retryVideo.srcObject = remoteStream

          retryPc.addTransceiver('video', { direction: 'recvonly' })
          retryPc.addTransceiver('audio', { direction: 'recvonly' })

          retryPc.ontrack = (event) => {
            if (!isActiveAttempt(channelId, attemptId)) return
            if (event.streams?.length) {
              event.streams[0].getTracks().forEach((track) => remoteStream.addTrack(track))
            } else {
              remoteStream.addTrack(event.track)
            }
          }

          const offer = await retryPc.createOffer()
          await retryPc.setLocalDescription(offer)
          await waitIceComplete(retryPc)

          const response = await fetch(retryApiUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'text/plain;charset=utf-8' },
            body: retryPc.localDescription?.sdp || offer.sdp || '',
          })
          if (!response.ok) throw new Error(await response.text())

          const answer = await response.json()
          if (answer.code !== 0 || !answer.sdp) {
            throw new Error(answer.msg || 'WebRTC 应答 SDP 缺失')
          }

          await retryPc.setRemoteDescription({
            type: answer.type || 'answer',
            sdp: answer.sdp,
          })

          await safePlay(retryVideo, channelId, attemptId)
          if (isActiveAttempt(channelId, attemptId)) {
            previewPlaybackState.value = {
              ...previewPlaybackState.value,
              [channelId]: true,
            }
            debugResults.value[channelId] = '浏览器专用转码流已通过 WebRTC 播放'
            rtcErrors.value[channelId] = ''
          }
          return
        } catch {
          continue
        }
      }
    }

    rtcErrors.value[channelId] = `WebRTC 自动播放失败：${currentMessage}`

    try {
      await tryHlsFallback(channelId, fallbackUrls, attemptId, video)
      if (isActiveAttempt(channelId, attemptId)) {
        rtcErrors.value[channelId] = `WebRTC 自动播放失败：${currentMessage}；已切换到 HLS-fMP4`
      }
    } catch (fallbackError: any) {
      if (isActiveAttempt(channelId, attemptId)) {
        rtcErrors.value[channelId] =
          `WebRTC 自动播放失败：${currentMessage}；HLS-fMP4 回退也失败：${fallbackError?.message || fallbackError}`
      }
    }
  } finally {
    pendingBrowserPlayChannels.delete(channelId)
  }
}

const startPlay = async (channelId: string) => {
  try {
    const result = await startChannelPlay(channelId)
    playInfos.value[channelId] = result.play_urls || result.urls || {}
    browserPlayInfos.value[channelId] = result.browser_play_urls || result.browser_urls || {}
    debugResults.value[channelId] = '通道已推流到 ZLMediaKit'
    await load()
    await nextTick()
    await startBrowserPlay(channelId)
  } catch (error: any) {
    debugResults.value[channelId] = error?.response?.data?.detail || error?.message || '启动播放失败'
  }
}

const stopPlay = async (channelId: string) => {
  try {
    await stopChannelPlay(channelId)
    await stopBrowserPlay(channelId)
    debugResults.value[channelId] = '通道播放已停止'
    await load()
  } catch (error: any) {
    debugResults.value[channelId] = error?.response?.data?.detail || error?.message || '停止播放失败'
  }
}

const closeZoneEditor = () => {
  zoneEditor.value.visible = false
}

const openPreviewTab = async (channelId?: string) => {
  activeTab.value = 'preview'
  await nextTick()
  if (channelId) {
    document.getElementById(`preview-card-${channelId}`)?.scrollIntoView({
      behavior: 'smooth',
      block: 'center',
    })
  }
  await restoreBrowserPlaybackForActiveChannels()
}

const openExpandedPreview = async (item: any) => {
  expandedPreview.value = {
    channelId: item.id,
    name: item.name,
    sourceUrl: item.source_url || '-',
    mode: 'channel',
  }
  expandedPreviewError.value = ''
  await nextTick()
  await startExpandedPreviewPlayback(item.id)
}

const openZonePreviewModal = async (item: any) => {
  const zoneUrls = await ensureZonePreviewPlayInfo(item.id, true)
  expandedPreview.value = {
    channelId: item.id,
    name: `${item.name} - 区域预览`,
    sourceUrl: zoneUrls.rtmp || zoneUrls.rtsp || zoneUrls.hls || '-',
    mode: 'zone',
  }
  expandedPreviewError.value = ''
  await nextTick()
  await startExpandedPreviewPlayback(item.id)
}

const closeExpandedPreview = async () => {
  await stopExpandedPreviewPlayback()
  expandedPreview.value = null
  expandedPreviewError.value = ''
}

const handleZonesUpdated = async () => {
  const channelId = zoneEditor.value.channelId
  if (channelId) {
    await loadChannelZones(channelId)
  }
}

onMounted(() => {
  void initializePage()
  startAiStatusPolling()
})

onActivated(() => {
  pageDeactivated = false
  startAiStatusPolling()
  if (items.value.length > 0) {
    void restoreBrowserPlaybackForActiveChannels()
  }
})

onDeactivated(() => {
  pageDeactivated = true
  stopAiStatusPolling()
  void resetAllZonePreviewPlayers()
})

watch(activeTab, async (tab) => {
  if (shouldPollChannelAiStatus(tab)) {
    void loadAIStatuses()
    startAiStatusPolling()
  } else {
    stopAiStatusPolling()
  }
  if (tab === 'preview') {
    await restoreBrowserPlaybackForActiveChannels()
  }
  if (tab === 'fence') {
    await loadAllZonePreviewStatuses()
  } else {
    await resetAllZonePreviewPlayers()
  }
})

onBeforeUnmount(() => {
  if (pageDeactivated) {
    return
  }
  stopAiStatusPolling()
  Array.from(peerConnections.keys()).forEach((channelId) => {
    void stopBrowserPlay(channelId)
  })
  Array.from(zonePreviewPeerConnections.keys()).forEach((channelId) => {
    void stopZonePreviewPlayer(channelId)
  })
  void stopExpandedPreviewPlayback()
  previewPlaybackState.value = {}
  zonePreviewPlaybackState.value = {}
})
</script>

<style scoped>
.channels-page {
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

.header-panel {
  padding-bottom: 1rem;
}

.panel-header,
.section-head,
.preview-card-head,
.ai-card-head,
.modal-header {
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
.preview-card-head h4,
.ai-card-head h4,
.modal-header h3 {
  margin: 0;
}

.section-head-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.panel-header p,
.preview-card-head p,
.ai-card-head p,
.section-head span,
.modal-header p {
  margin: 0.35rem 0 0;
  color: #64748b;
}

.tab-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.tab-btn {
  min-height: 42px;
  padding: 0.7rem 1rem;
  border-radius: 12px;
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #334155;
  cursor: pointer;
  font: inherit;
  font-weight: 600;
}

.tab-btn.active {
  background: #1d4ed8;
  color: #fff;
  border-color: #1d4ed8;
}

.preview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 1rem;
}

.preview-card,
.ai-card {
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  padding: 1rem;
  display: grid;
  gap: 0.9rem;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
}

.player-shell {
  position: relative;
  border-radius: 18px;
  overflow: hidden;
  background: #0f172a;
  border: 1px solid #dbe4ee;
}

.player {
  width: 100%;
  aspect-ratio: 16 / 9;
  display: block;
  background: #000;
}

.player-mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, rgba(15, 23, 42, 0.15), rgba(15, 23, 42, 0.3));
}

.offline-mask span {
  color: #e2e8f0;
  text-align: center;
  padding: 0 1rem;
  font-weight: 600;
}

.player-overlay-actions {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.play-circle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 68px;
  height: 68px;
  border: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.9);
  color: #0f172a;
  font-size: 1.65rem;
  cursor: pointer;
  pointer-events: auto;
  box-shadow: 0 14px 32px rgba(15, 23, 42, 0.28);
  transition: transform 0.18s ease, background 0.18s ease, box-shadow 0.18s ease;
}

.play-circle:hover {
  transform: scale(1.05);
  background: #ffffff;
  box-shadow: 0 18px 36px rgba(15, 23, 42, 0.34);
}

.source-line {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 0.75rem 0.9rem;
  color: #fff;
  background: linear-gradient(180deg, rgba(15, 23, 42, 0), rgba(15, 23, 42, 0.88));
  font-size: 0.92rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.preview-actions,
.table-actions,
.ai-tool-row,
.config-actions,
.modal-actions,
.status-pills {
  display: flex;
  gap: 0.65rem;
  flex-wrap: wrap;
}

.preview-actions {
  display: none;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.75rem;
}

.form-grid input,
.form-grid select,
.form-grid button,
.tab-btn,
.ghost-btn,
.primary-btn,
.warn-btn,
.danger-btn,
.ai-btn,
.ai-stop-btn,
.save-ai-btn {
  font: inherit;
}

.create-grid {
  margin-bottom: 1rem;
}

.table-wrap {
  overflow-x: auto;
}

.channel-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 980px;
}

.channel-table th,
.channel-table td {
  padding: 1rem 0.85rem;
  border-bottom: 1px solid #edf2f7;
  text-align: left;
  vertical-align: top;
}

.channel-table th {
  color: #0f172a;
  font-weight: 700;
}

.table-main {
  color: #0f172a;
  font-weight: 700;
}

.table-sub {
  margin-top: 0.3rem;
  color: #94a3b8;
  font-size: 0.9rem;
}

.td-break {
  max-width: 320px;
  word-break: break-all;
  color: #334155;
}

.ai-list {
  display: grid;
  gap: 1rem;
}

.zone-preview-headline {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1.25rem;
  color: #334155;
}

.ai-capability-grid {
  display: grid;
  grid-template-columns: minmax(360px, 420px) minmax(0, 1fr);
  gap: 1rem;
  align-items: start;
}

.ai-card-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.55rem;
  color: #334155;
}

.ai-experience-card {
  gap: 0.9rem;
}

.ai-setup-banner {
  display: flex;
  justify-content: space-between;
  gap: 0.9rem;
  align-items: center;
  flex-wrap: wrap;
  padding: 0.85rem 0.95rem;
  border-radius: 14px;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
  border: 1px solid #dbe4ee;
}

.ai-setup-steps {
  display: flex;
  gap: 0.45rem;
  flex-wrap: wrap;
}

.step-chip {
  padding: 0.35rem 0.7rem;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.84rem;
  font-weight: 600;
}

.ai-readiness-pill {
  padding: 0.38rem 0.8rem;
  border-radius: 999px;
  font-size: 0.84rem;
  font-weight: 700;
}

.ai-readiness-pill.ready {
  background: #dcfce7;
  color: #166534;
}

.ai-readiness-pill.pending {
  background: #fef3c7;
  color: #92400e;
}

.ai-config-box {
  display: grid;
  gap: 0.75rem;
  padding: 0.95rem;
  background: #f8fafc;
  border: 1px solid #dbe4ee;
  border-radius: 14px;
}

.zone-preview-box {
  display: grid;
  gap: 0.8rem;
  padding: 0.95rem;
  background: #fff;
  border: 1px solid #dbe4ee;
  border-radius: 14px;
  position: sticky;
  top: 1rem;
}

.zone-preview-head {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  align-items: flex-start;
}

.zone-preview-help {
  margin-top: 0.25rem;
  color: #64748b;
  font-size: 0.92rem;
  line-height: 1.6;
}

.zone-preview-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.55rem 1rem;
  color: #334155;
  font-size: 0.94rem;
  line-height: 1.65;
}

.zone-preview-actions {
  display: flex;
  gap: 0.65rem;
  flex-wrap: wrap;
}

.zone-preview-advanced {
  border-top: 1px solid #e2e8f0;
  padding-top: 0.9rem;
}

.zone-preview-advanced summary {
  list-style: none;
  cursor: pointer;
  color: #0f172a;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
}

.zone-preview-advanced summary::-webkit-details-marker {
  display: none;
}

.zone-preview-advanced summary::after {
  content: '▾';
  font-size: 0.82rem;
  color: #64748b;
  transition: transform 0.2s ease;
}

.zone-preview-advanced[open] summary::after {
  transform: rotate(180deg);
}

.zone-preview-advanced-body {
  display: grid;
  gap: 0.8rem;
  margin-top: 0.8rem;
  padding: 0.9rem;
  border-radius: 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}

.zone-preview-inline-error {
  color: #b91c1c;
  font-size: 0.92rem;
  line-height: 1.6;
}

.zone-preview-player-shell {
  position: relative;
  border-radius: 18px;
  overflow: hidden;
  background: #0f172a;
  border: 1px solid #dbe4ee;
}

.zone-preview-player {
  width: 100%;
  aspect-ratio: 16 / 9;
  display: block;
  background: #000;
}

/* .ai-debug-board {
  display: grid;
  gap: 0.75rem;
  padding: 0.95rem;
  background: #fffaf0;
  border: 1px solid #fcd34d;
  border-radius: 14px;
}

.ai-debug-help {
  color: #92400e;
  line-height: 1.6;
}

.ai-debug-list {
  display: grid;
  gap: 0.75rem;
}

.ai-debug-card {
  display: grid;
  gap: 0.7rem;
  padding: 0.9rem;
  background: #ffffff;
  border: 1px solid #fde68a;
  border-radius: 12px;
}

.ai-debug-head {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  align-items: center;
  flex-wrap: wrap;
}

.ai-debug-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.55rem 1rem;
  color: #334155;
} */

.section-title {
  font-weight: 700;
  color: #0f172a;
}

.ai-config-top {
  display: flex;
  justify-content: space-between;
  gap: 0.9rem;
  align-items: start;
  flex-wrap: wrap;
}

.ai-config-help {
  color: #64748b;
  line-height: 1.6;
}

.ai-config-form {
  display: grid;
  gap: 0.9rem;
}

.ai-config-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
}

.config-row {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 0.75rem;
  align-items: start;
  padding: 0.8rem 0.9rem;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #e2e8f0;
  min-width: 0;
}

.compact-field {
  min-height: 100%;
}

.span-2 {
  grid-column: 1 / -1;
}

.save-snapshot-row {
  grid-template-columns: 1fr;
  align-items: center;
}

.config-label {
  display: grid;
  gap: 0.35rem;
  color: #334155;
}

.config-label span {
  color: #64748b;
  line-height: 1.45;
  font-size: 0.88rem;
}

.config-control {
  display: grid;
  gap: 0.45rem;
  min-width: 0;
}

.config-control select,
.config-control input {
  min-height: 38px;
  padding: 0.55rem 0.75rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  width: 100%;
  box-sizing: border-box;
}

.check-line {
  min-height: 38px;
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
  color: #334155;
}

.checkbox-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.25rem;
  cursor: pointer;
}

.checkbox-item:hover {
  background: #f0f0f0;
  border-radius: 4px;
}

.ai-checkbox-item {
  min-height: 38px;
  color: #334155;
  width: fit-content;
}

.ai-checkbox-item input[type='checkbox'] {
  width: 16px;
  height: 16px;
  min-height: auto;
  padding: 0;
  border: none;
  border-radius: 0;
  box-sizing: border-box;
  flex: 0 0 auto;
}

.compact-select-panel {
  display: grid;
  gap: 0.5rem;
}

.select-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.45rem;
}

.select-summary {
  color: #64748b;
  font-size: 0.86rem;
}

.label-select-box,
.zone-select-box {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
  align-items: center;
}

.compact-chip-box {
  max-height: 150px;
  overflow: auto;
  padding-right: 0.2rem;
}

.label-chip,
.zone-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.28rem 0.58rem;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
  cursor: pointer;
}

.compact-chip {
  font-size: 0.86rem;
  line-height: 1.2;
}

.compact-chip input {
  width: 14px;
  height: 14px;
}

.compact-chip.active {
  background: #dbeafe;
  border-color: #60a5fa;
}

.zone-chip-color {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  flex-shrink: 0;
}

.inline-warning {
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fcd34d;
  border-radius: 10px;
  padding: 0.75rem 0.9rem;
}

.inline-muted {
  color: #64748b;
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  border-radius: 10px;
  padding: 0.75rem 0.9rem;
}

.multi-model-card {
  border: 1px solid #dbe4f0;
  border-radius: 12px;
  padding: 1rem;
  background: #f8fbff;
}

.multi-model-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.multi-model-title {
  display: grid;
  gap: 0.4rem;
}

.multi-model-tags {
  display: flex;
  gap: 0.45rem;
  flex-wrap: wrap;
}

.mini-pill {
  padding: 0.28rem 0.58rem;
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 0.8rem;
  font-weight: 600;
}

.multi-model-grid {
  display: grid;
  gap: 0.75rem;
}

.ai-action-bar {
  align-items: center;
}

.save-ai-btn {
  min-height: 46px;
  padding: 0.85rem 1.3rem;
  border: none;
  border-radius: 12px;
  background: linear-gradient(135deg, #2563eb 0%, #0f766e 100%);
  color: #fff;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 12px 24px rgba(37, 99, 235, 0.18);
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

.pill.ai-on {
  background: #ccfbf1;
  color: #0f766e;
}

.pill.muted {
  background: #e2e8f0;
  color: #475569;
}

.primary-btn,
.ghost-btn,
.warn-btn,
.danger-btn,
.ai-btn,
.ai-stop-btn {
  min-height: 40px;
  padding: 0.65rem 0.95rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
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

.warn-btn {
  background: #b45309;
  border-color: #b45309;
  color: #fff;
}

.danger-btn {
  background: #b91c1c;
  border-color: #b91c1c;
  color: #fff;
}

.ai-btn {
  background: #0f766e;
  border-color: #0f766e;
  color: #fff;
}

.ai-stop-btn {
  background: #7c2d12;
  border-color: #7c2d12;
  color: #fff;
}

.mini {
  min-height: 34px;
  padding: 0.45rem 0.7rem;
  font-size: 0.9rem;
}

.message,
.error-text,
.debug-text,
.empty-state {
  border-radius: 10px;
  padding: 0.8rem 1rem;
}

.message.success {
  background: #ecfdf3;
  color: #027a48;
  border: 1px solid #a6f4c5;
}

.message.error,
.error-text {
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}

.debug-text,
.empty-state {
  background: #f8fafc;
  color: #475569;
  border: 1px solid #e2e8f0;
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
  width: min(960px, 100%);
  background: #fff;
  border-radius: 16px;
  padding: 1rem;
  display: grid;
  gap: 1rem;
}

.preview-modal-dialog {
  width: min(1180px, 100%);
}

.preview-modal-player-shell {
  border-radius: 18px;
  overflow: hidden;
  background: #020617;
  border: 1px solid #1e293b;
}

.preview-modal-player {
  width: 100%;
  max-height: min(78vh, 820px);
  aspect-ratio: 16 / 9;
  display: block;
  background: #000;
}

.modal-grid .span-2 {
  grid-column: span 2;
}

@media (max-width: 900px) {
  .zone-preview-layout {
    grid-template-columns: 1fr;
  }

  .ai-capability-grid {
    grid-template-columns: 1fr;
  }

  .ai-config-grid {
    grid-template-columns: 1fr;
  }

  .config-row {
    grid-template-columns: 1fr;
    align-items: start;
  }

  .span-2 {
    grid-column: auto;
  }
}

@media (max-width: 768px) {
  .panel-header,
  .section-head,
  .preview-card-head,
  .ai-card-head,
  .modal-header {
    flex-direction: column;
  }

  .preview-grid {
    grid-template-columns: 1fr;
  }

  .modal-grid .span-2 {
    grid-column: span 1;
  }
}
</style>

