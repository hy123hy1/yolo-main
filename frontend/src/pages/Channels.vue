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
          <span>围绕通道创建和运行 AI 任务，并统一展示检测状态。</span>
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
        <span>当前页直接播放通道原始流，并在前端叠加围栏区域；第三方如需带框流，可展开下方地址。</span>
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
              <span :class="['pill', isTaskRunningForChannel(item.id) ? 'ok' : 'muted']">
                {{ zonePreviewLabel(item.id) }}
              </span>
            </div>
          </div>

          <div class="zone-preview-headline">
            <div><strong>当前区域数：</strong>{{ zonePreviewRegionCount(item.id) }}</div>
            <div><strong>区域来源：</strong>{{ zonePreviewSourceLabel(item.id) }}</div>
            <div><strong>绑定任务：</strong>{{ taskForChannel(item.id)?.name || '-' }}</div>
          </div>

          <div class="zone-preview-box">
            <div class="zone-preview-head">
              <div>
                <div class="section-title">区域预览</div>
                <div class="zone-preview-help">当前页直接播放通道原始流，围栏线框由前端 canvas 叠加显示，不再依赖后台生成区域预览流。</div>
              </div>
            </div>

            <div class="zone-preview-player-shell fence-preview-shell">
              <video
                :ref="(el) => setVideoRef(item.id, el as HTMLVideoElement | null)"
                class="zone-preview-player fence-preview-player"
                autoplay
                muted
                playsinline
                controls
                @loadedmetadata="handleFenceVideoReady(item.id)"
                @loadeddata="handleFenceVideoReady(item.id)"
                @play="handleFenceVideoReady(item.id)"
              />
              <canvas
                :ref="(el) => setFenceCanvasRef(item.id, el as HTMLCanvasElement | null)"
                class="fence-overlay-canvas"
              />
              <div v-if="!isPreviewPlaying(item.id)" class="player-mask">
                <div class="zone-preview-mask-content">
                  <button
                    class="play-circle"
                    :class="{ disabled: !canManualPlayFencePreview(item.id) }"
                    type="button"
                    :disabled="!canManualPlayFencePreview(item.id)"
                    @click="startFencePreview(item.id)"
                  >
                    ▶
                  </button>
                  <span class="zone-preview-mask-text">{{ fencePreviewMaskText(item.id) }}</span>
                </div>
              </div>
              <div v-if="!item.is_online" class="player-mask offline-mask">
                <span>通道离线时不会自动预览</span>
              </div>
              <div class="source-line">{{ item.source_url }}</div>
            </div>

            <div class="zone-preview-actions">
              <template v-if="canManualPlayFencePreview(item.id)">
                <button class="ghost-btn" type="button" @click="openExpandedPreview(item)">放大预览</button>
                <button class="ghost-btn" type="button" @click="redrawFenceOverlay(item.id)">重绘围栏</button>
              </template>
            </div>

            <details class="zone-preview-advanced" @toggle="handleZonePreviewAdvancedToggle(item.id, $event)">
              <summary>第三方带框流地址</summary>
              <div class="zone-preview-advanced-body">
                <div class="zone-preview-help">
                  只有第三方平台需要直接取后台生成的带框流时，才需要使用这里的地址。
                </div>
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

          <div v-if="rtcErrors[item.id]" class="error-text">
            {{ rtcErrors[item.id] }}
          </div>
          <div v-if="zonePreviewErrors[item.id]" class="error-text">
            {{ zonePreviewErrors[item.id] }}
          </div>
          <div v-if="debugResults[item.id]" class="debug-text">
            {{ debugResults[item.id] }}
          </div>
        </article>
      </div>
    </section>

    <div v-if="creatingChannel" class="modal-mask" @click.self="closeCreateModal">
      <div class="modal-dialog">
        <div class="modal-header">
          <div>
            <h3>创建通道</h3>
            <p>填写通道名称、源地址，并可选同步创建首个任务算法。</p>
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
import { createSingleIntervalController, shouldPollTaskStatus } from '@/utils/single-interval-controller.js'

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

type AIStatus = {
  task_id?: string
  task_name?: string
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

type FenceRenderableRegion = {
  id: string
  name: string
  color: string
  coordinates: number[][]
  source: 'task' | 'channel'
  algorithmName?: string
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
const fenceCanvasRefs = new Map<string, HTMLCanvasElement>()
const fenceOverlayFrameIds = new Map<string, number>()
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
const taskStatusPollController = createSingleIntervalController({
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

const buildInitialAlgorithmPayload = (modelOption: any) => ({
  model_id: String(modelOption?.model_id || modelOption?.id || ''),
  algorithm_type: String(modelOption?.algorithm_type || modelOption?.task || 'detection'),
  scene_key: modelOption?.scene_key || undefined,
  scene_name: modelOption?.scene_name || undefined,
  rule_type: modelOption?.rule_type || modelOption?.algorithm_type || modelOption?.task || 'detection',
  enabled: modelOption?.enabled !== false,
  labels: Array.isArray(modelOption?.recommended_labels)
    ? [...modelOption.recommended_labels]
    : Array.isArray(modelOption?.classes)
      ? [...modelOption.classes]
      : [],
  detect_all_classes: Boolean(modelOption?.detect_all_classes),
  label_source: modelOption?.label_source || (modelOption?.detect_all_classes ? 'all' : 'manual'),
  confidence: Number(modelOption?.confidence ?? 0.25),
  alert_interval: Number(modelOption?.alert_interval ?? 60),
  need_snapshot: Boolean(modelOption?.need_snapshot ?? true),
  need_video_clip: Boolean(modelOption?.need_video_clip ?? true),
  video_before_seconds: Number(modelOption?.video_before_seconds ?? 10),
  video_after_seconds: Number(modelOption?.video_after_seconds ?? 10),
  overlay_color: String(modelOption?.overlay_color || '#ff4d4f'),
  postprocess_config: modelOption?.postprocess_config || {},
  regions: [],
})

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

const syncAIStatusesFromTasks = () => {
  const nextMap: AIStatusMap = {}

  for (const item of items.value) {
    nextMap[item.id] = {
      status: 'stopped',
      is_running: false,
      frame_count: 0,
      event_count: 0,
      models: {},
    }
  }

  for (const task of tasks.value) {
    const channelId = String(task?.channel_id || '')
    if (!channelId) continue

    const status = taskStatuses.value[task.id]
    const runtime = status?.runtime || task?.runtime || {}
    const normalizedStatus = String(status?.status || runtime?.status || task?.status || 'stopped').toLowerCase()
    const isRunning = Boolean(runtime?.is_running) || ['running', 'starting', 'restarting'].includes(normalizedStatus)
    const current = nextMap[channelId]

    if (current?.is_running && !isRunning) {
      continue
    }
    if (current?.task_id && !current?.is_running && !isRunning) {
      continue
    }

    nextMap[channelId] = {
      task_id: task.id,
      task_name: task.name,
      status: normalizedStatus,
      is_running: isRunning,
      last_error: runtime?.last_error ?? null,
      last_analysis_at: runtime?.last_analysis_at ?? null,
      last_event_at: runtime?.last_event_at ?? null,
      frame_count: Number(runtime?.frame_count ?? 0),
      event_count: Number(runtime?.event_count ?? 0),
      models: runtime?.models || {},
    }
  }

  aiStatuses.value = nextMap
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
  syncAIStatusesFromTasks()
}

const loadTasksForCurrentChannel = async (silent = false) => {
  if (!silent) {
    taskLoading.value = true
  }
  try {
    const result = await listTasks()
    tasks.value = result.items || []
    await loadTaskStatuses(tasks.value.map((task) => task.id))
    if (activeTab.value === 'fence') {
      await hydrateFenceTasks()
      await nextTick()
      redrawAllFenceOverlays()
    }
    syncSelectedTask()
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '加载任务失败')
  } finally {
    if (!silent) {
      taskLoading.value = false
    }
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
    if (activeTab.value === 'fence') {
      await nextTick()
      redrawAllFenceOverlays()
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
    if (activeTab.value === 'fence') {
      await nextTick()
      redrawFenceOverlay(channelId)
    }
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

const createTaskWithInitialAlgorithm = async (channel: any, modelId: string) => {
  const createdTask = await createTask({
    name: `${channel.name}任务`,
    channel_id: channel.id,
    source_url: channel.source_url,
    source_type: String(channel.type || '').toLowerCase().startsWith('rtmp') ? 'rtmp' : 'rtsp',
    frame_interval: 10,
  })

  try {
    const availableModels = await listTaskAvailableModels(createdTask.id)
    const modelOption = (availableModels?.items || []).find(
      (item: any) => String(item?.model_id || item?.id || '') === modelId
    ) || models.value.find((item) => String(item?.model_id || item?.id || '') === modelId)

    if (!modelOption) {
      throw new Error('未找到可用于初始化任务的模型配置')
    }

    await addTaskAlgorithm(createdTask.id, buildInitialAlgorithmPayload(modelOption))
    return createdTask
  } catch (error) {
    await deleteTask(createdTask.id).catch(() => undefined)
    throw error
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

const modelDisplayName = (model: any) => {
  const labelCount = Array.isArray(model?.classes) ? model.classes.length : 0
  const primary = model?.name || model?.model_id || '-'
  const secondary = model?.name && model?.model_id ? ` (${model.model_id})` : ''
  return `${primary}${secondary}${labelCount > 0 ? ` (${labelCount} 类)` : ''}`
}

const channelZones = (channelId: string) => zonesMap.value[channelId] || []
const loadChannelZones = async (channelId: string) => {
  try {
    const result = await listChannelZones(channelId)
    zonesMap.value = {
      ...zonesMap.value,
      [channelId]: result.items || [],
    }
  } catch {
    zonesMap.value = {
      ...zonesMap.value,
      [channelId]: [],
    }
  } finally {
    if (activeTab.value === 'fence') {
      redrawFenceOverlay(channelId)
    }
  }
}

const fencePalette = ['#2563eb', '#ef4444', '#f59e0b', '#10b981', '#8b5cf6', '#ec4899']

const withAlpha = (color: string, alpha: number) => {
  const normalized = String(color || '#2563eb').trim()
  if (normalized.startsWith('rgba(') || normalized.startsWith('rgb(')) {
    return normalized.replace(/^rgba?\((.+)\)$/i, (_, content) => {
      const parts = String(content).split(',').map((item) => item.trim())
      const [r = '37', g = '99', b = '235'] = parts
      return `rgba(${r}, ${g}, ${b}, ${alpha})`
    })
  }
  const hex = normalized.replace('#', '')
  const full = hex.length === 3 ? hex.split('').map((item) => `${item}${item}`).join('') : hex
  const numeric = Number.parseInt(full, 16)
  const r = (numeric >> 16) & 255
  const g = (numeric >> 8) & 255
  const b = numeric & 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

const normalizeCoordinates = (coordinates: any): number[][] =>
  Array.isArray(coordinates)
    ? coordinates
      .map((point: any) => [Number(point?.[0]), Number(point?.[1])])
      .filter((point: number[]) => Number.isFinite(point[0]) && Number.isFinite(point[1]))
    : []

const taskAlgorithmDisplayName = (algorithm: any) =>
  String(
    algorithm?.model_name
    || models.value.find((item) => item.model_id === algorithm?.model_id)?.name
    || algorithm?.scene_name
    || algorithm?.model_id
    || '算法区域'
  )

const needsFenceTaskHydration = (task: any) =>
  !Array.isArray(task?.algorithms)
  || task.algorithms.some((algorithm: any) => algorithm?.regions === undefined)

const hydrateFenceTasks = async () => {
  const targetTasks = tasks.value.filter((task) => task?.channel_id && needsFenceTaskHydration(task))
  if (!targetTasks.length) return

  const detailEntries = await Promise.all(
    targetTasks.map(async (task) => {
      try {
        return [task.id, await loadTaskDetail(task.id)] as const
      } catch {
        return [task.id, null] as const
      }
    })
  )

  const detailMap = new Map(
    detailEntries.filter((entry): entry is readonly [string, any] => Boolean(entry[1]))
  )
  if (!detailMap.size) return

  tasks.value = tasks.value.map((task) => detailMap.get(task.id) || task)
}

const buildTaskFenceRegions = (channelId: string): FenceRenderableRegion[] => {
  const task = taskForChannel(channelId)
  if (!task || !Array.isArray(task.algorithms)) {
    return []
  }

  const regions: FenceRenderableRegion[] = []
  task.algorithms.forEach((algorithm: any, algorithmIndex: number) => {
    const sourceRegions = Array.isArray(algorithm?.regions) ? algorithm.regions : []
    const algorithmColor = String(algorithm?.overlay_color || fencePalette[algorithmIndex % fencePalette.length])
    const algorithmName = taskAlgorithmDisplayName(algorithm)
    sourceRegions.forEach((region: any, regionIndex: number) => {
      const coordinates = normalizeCoordinates(region?.coordinates)
      if (coordinates.length < 2) {
        return
      }
      regions.push({
        id: String(region?.id || `${algorithm?.id || algorithmIndex}-${regionIndex}`),
        name: String(region?.name || `${algorithmName}-${regionIndex + 1}`),
        color: String(region?.color || algorithmColor),
        coordinates,
        source: 'task',
        algorithmName,
      })
    })
  })

  const unique = new Map<string, FenceRenderableRegion>()
  regions.forEach((region) => {
    const key = region.id || `${region.name}:${JSON.stringify(region.coordinates)}`
    if (!unique.has(key)) {
      unique.set(key, region)
    }
  })
  return Array.from(unique.values())
}

const buildChannelFenceRegions = (channelId: string): FenceRenderableRegion[] =>
  channelZones(channelId).map((zone, index) => ({
    id: String(zone.id || `channel-zone-${index}`),
    name: String(zone.name || `区域${index + 1}`),
    color: String(zone.color || fencePalette[index % fencePalette.length]),
    coordinates: normalizeCoordinates(zone.coordinates),
    source: 'channel' as const,
  })).filter((zone) => zone.coordinates.length >= 2)

const fenceRegionsForChannel = (channelId: string): FenceRenderableRegion[] => {
  const taskRegions = buildTaskFenceRegions(channelId)
  if (taskRegions.length) {
    return taskRegions
  }
  return buildChannelFenceRegions(channelId)
}

const zonePreviewLabel = (channelId: string) => {
  const task = taskForChannel(channelId)
  if (!task) return '未绑定任务'
  const status = zonePreviewStatuses.value[channelId]
  if (isTaskRunningForChannel(channelId)) return '任务运行中'
  if (status?.status === 'error') return '区域流异常'
  return '任务未启动'
}

const zonePreviewRegionCount = (channelId: string) => {
  const regions = fenceRegionsForChannel(channelId)
  if (regions.length) {
    return regions.length
  }
  const status = zonePreviewStatuses.value[channelId] || {}
  if (typeof status.zone_count === 'number') {
    return status.zone_count
  }
  return 0
}

const zonePreviewSourceLabel = (channelId: string) => {
  const taskRegions = buildTaskFenceRegions(channelId)
  if (taskRegions.length) {
    return '任务算法区域'
  }
  if (channelZones(channelId).length) {
    return '通道区域'
  }
  return '未配置'
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

const canManualPlayFencePreview = (channelId: string) => {
  const channel = items.value.find((item) => item.id === channelId)
  return Boolean(channel?.is_online)
}

const fencePreviewMaskText = (channelId: string) => {
  const channel = items.value.find((item) => item.id === channelId)
  if (!channel?.is_online) {
    return '通道离线，暂时无法预览'
  }
  if (!taskForChannel(channelId)) {
    return '当前通道还没有绑定任务，可先查看原始流'
  }
  if (!zonePreviewRegionCount(channelId)) {
    return '当前通道还没有配置围栏区域，可先查看原始流'
  }
  if (!isTaskRunningForChannel(channelId)) {
    return '任务未启动，也可以先查看原始流'
  }
  return '点击播放查看原始流上的围栏叠加效果'
}

const canManualPlayZonePreview = (channelId: string) => canManualPlayFencePreview(channelId)
const zonePreviewMaskText = (channelId: string) => fencePreviewMaskText(channelId)

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

const refreshTaskBackedAiStatuses = async () => {
  await loadTasksForCurrentChannel(true)
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
    await Promise.all([loadAllZones(), loadTasksForCurrentChannel(), loadAllZonePreviewStatuses()])
    if (activeTab.value === 'preview') {
      await nextTick()
      await restoreBrowserPlaybackForActiveChannels()
    }
    if (activeTab.value === 'fence') {
      await nextTick()
      await restoreFencePlaybackForActiveChannels()
    }
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '加载通道失败')
  } finally {
    loading.value = false
  }
}

const resetAllBrowserPlayers = async () => {
  const channelIds = new Set<string>([
    ...Object.keys(previewPlaybackState.value || {}),
    ...Array.from(peerConnections.keys()),
    ...Array.from(videoRefs.keys()),
  ])
  await Promise.all(Array.from(channelIds).map((channelId) => stopBrowserPlay(channelId)))
  previewPlaybackState.value = {}
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

const restoreFencePlaybackForActiveChannels = async () => {
  if (activeTab.value !== 'fence') return
  await hydrateFenceTasks()
  await nextTick()
  const onlineItems = items.value.filter((item) => {
    if (!item.is_online) return false
    const card = fenceCardRefs.get(item.id)
    if (!card) return true
    const rect = card.getBoundingClientRect()
    return rect.bottom >= -120 && rect.top <= window.innerHeight + 120
  })
  for (const item of onlineItems) {
    if (pendingBrowserPlayChannels.has(item.id) || isPreviewPlaying(item.id)) {
      redrawFenceOverlay(item.id)
      continue
    }
    try {
      await startBrowserPlay(item.id)
      redrawFenceOverlay(item.id)
    } catch (error: any) {
      debugResults.value[item.id] = error?.message || '恢复电子围栏预览失败'
    }
  }
}

const initializePage = async () => {
  await load()
  if (activeTab.value === 'preview') {
    await restoreBrowserPlaybackForActiveChannels()
  }
  if (activeTab.value === 'fence') {
    await restoreFencePlaybackForActiveChannels()
  }
}

const startTaskStatusPolling = () => {
  if (!shouldPollTaskStatus(activeTab.value)) {
    return
  }
  taskStatusPollController.start(() => {
    if (shouldPollTaskStatus(activeTab.value) && items.value.length > 0) {
      void refreshTaskBackedAiStatuses()
    }
  }, 5000)
}

const stopTaskStatusPolling = () => {
  taskStatusPollController.stop()
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

const setFenceCanvasRef = (channelId: string, el: HTMLCanvasElement | null) => {
  if (el) {
    fenceCanvasRefs.set(channelId, el)
    window.requestAnimationFrame(() => {
      redrawFenceOverlay(channelId)
    })
  } else {
    fenceCanvasRefs.delete(channelId)
    const frameId = fenceOverlayFrameIds.get(channelId)
    if (frameId) {
      window.cancelAnimationFrame(frameId)
      fenceOverlayFrameIds.delete(channelId)
    }
  }
}

const resizeFenceCanvas = (canvas: HTMLCanvasElement) => {
  const rect = canvas.getBoundingClientRect()
  const width = Math.max(Math.round(rect.width), 1)
  const height = Math.max(Math.round(rect.height), 1)
  const dpr = Math.max(window.devicePixelRatio || 1, 1)

  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr)
    canvas.height = Math.round(height * dpr)
  }
  canvas.style.width = `${width}px`
  canvas.style.height = `${height}px`

  const ctx = canvas.getContext('2d')
  if (ctx) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  }
  return { width, height }
}

const drawFenceLabel = (
  ctx: CanvasRenderingContext2D,
  text: string,
  color: string,
  x: number,
  y: number,
  maxWidth: number
) => {
  const content = text.trim()
  if (!content) return

  ctx.font = '12px sans-serif'
  const paddingX = 8
  const measured = ctx.measureText(content)
  const labelWidth = Math.min(Math.ceil(measured.width) + paddingX * 2, Math.max(maxWidth - x - 8, 96))
  const labelX = Math.max(8, Math.min(x, Math.max(maxWidth - labelWidth - 8, 8)))
  const labelY = Math.max(8, y - 28)

  ctx.fillStyle = withAlpha(color, 0.92)
  ctx.fillRect(labelX, labelY, labelWidth, 22)
  ctx.fillStyle = '#ffffff'
  ctx.textBaseline = 'middle'
  ctx.fillText(content, labelX + paddingX, labelY + 11, labelWidth - paddingX * 2)
}

const renderFenceOverlay = (channelId: string) => {
  const canvas = fenceCanvasRefs.get(channelId)
  if (!canvas) return

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const { width, height } = resizeFenceCanvas(canvas)
  ctx.clearRect(0, 0, width, height)

  const regions = fenceRegionsForChannel(channelId)
  if (!regions.length) {
    return
  }

  const video = videoRefs.get(channelId)
  const maxCoordinateX = Math.max(...regions.flatMap((region) => region.coordinates.map((point) => point[0])), width)
  const maxCoordinateY = Math.max(...regions.flatMap((region) => region.coordinates.map((point) => point[1])), height)
  const sourceWidth = Math.max(Number(video?.videoWidth || 0), maxCoordinateX, 1)
  const sourceHeight = Math.max(Number(video?.videoHeight || 0), maxCoordinateY, 1)
  const scaleX = width / sourceWidth
  const scaleY = height / sourceHeight

  ctx.lineJoin = 'round'
  ctx.lineCap = 'round'

  regions.forEach((region) => {
    const coordinates = region.coordinates
      .map((point) => [point[0] * scaleX, point[1] * scaleY] as const)
      .filter((point) => Number.isFinite(point[0]) && Number.isFinite(point[1]))

    if (coordinates.length < 2) {
      return
    }

    ctx.beginPath()
    ctx.moveTo(coordinates[0][0], coordinates[0][1])
    coordinates.slice(1).forEach((point) => {
      ctx.lineTo(point[0], point[1])
    })
    ctx.closePath()

    ctx.fillStyle = withAlpha(region.color, 0.12)
    ctx.strokeStyle = region.color
    ctx.lineWidth = 2
    ctx.fill()
    ctx.stroke()

    const anchor = coordinates.reduce(
      (current, point) => ({
        x: Math.min(current.x, point[0]),
        y: Math.min(current.y, point[1]),
      }),
      { x: coordinates[0][0], y: coordinates[0][1] }
    )
    drawFenceLabel(ctx, region.name, region.color, anchor.x, anchor.y, width)
  })
}

const redrawFenceOverlay = (channelId: string) => {
  const previous = fenceOverlayFrameIds.get(channelId)
  if (previous) {
    window.cancelAnimationFrame(previous)
  }
  const frameId = window.requestAnimationFrame(() => {
    fenceOverlayFrameIds.delete(channelId)
    renderFenceOverlay(channelId)
  })
  fenceOverlayFrameIds.set(channelId, frameId)
}

const redrawAllFenceOverlays = () => {
  if (activeTab.value !== 'fence') return
  items.value.forEach((item) => {
    redrawFenceOverlay(item.id)
  })
}

const handleFenceVideoReady = (channelId: string) => {
  if (activeTab.value !== 'fence') return
  redrawFenceOverlay(channelId)
}

const startFencePreview = async (channelId: string) => {
  if (!canManualPlayFencePreview(channelId)) {
    rtcErrors.value[channelId] = fencePreviewMaskText(channelId)
    return
  }
  rtcErrors.value[channelId] = ''
  await hydrateFenceTasks()
  await startBrowserPlay(channelId)
  redrawFenceOverlay(channelId)
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

    let createdTaskId = ''
    if (form.value.model_id) {
      const createdTask = await createTaskWithInitialAlgorithm(created, form.value.model_id)
      createdTaskId = createdTask.id
      selectedTaskChannelId.value = created.id
    }

    resetCreateForm()

    await load()
    if (createdTaskId) {
      selectedTaskId.value = createdTaskId
      await selectTask(createdTaskId)
    }
    closeCreateModal()
    showMessage('success', createdTaskId ? '通道和默认任务算法已创建' : '通道创建成功')
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

// 保留后台区域流相关调试入口，当前电子围栏页内预览已改为原始流 + 前端叠加。
void isZonePreviewPlaying
void setZonePreviewVideoRef
void startZonePreviewPlayer
void openZonePreviewModal

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
  startTaskStatusPolling()
  window.addEventListener('resize', redrawAllFenceOverlays)
})

onActivated(() => {
  pageDeactivated = false
  startTaskStatusPolling()
  if (items.value.length > 0) {
    if (activeTab.value === 'preview') {
      void restoreBrowserPlaybackForActiveChannels()
    }
    if (activeTab.value === 'fence') {
      void restoreFencePlaybackForActiveChannels()
    }
  }
})

onDeactivated(() => {
  pageDeactivated = true
  stopTaskStatusPolling()
  void resetAllBrowserPlayers()
  void resetAllZonePreviewPlayers()
})

watch(activeTab, async (tab) => {
  if (shouldPollTaskStatus(tab)) {
    void refreshTaskBackedAiStatuses()
    startTaskStatusPolling()
  } else {
    stopTaskStatusPolling()
  }
  if (tab === 'preview') {
    await resetAllBrowserPlayers()
    await restoreBrowserPlaybackForActiveChannels()
  }
  if (tab === 'fence') {
    await resetAllBrowserPlayers()
    await loadAllZonePreviewStatuses()
    await restoreFencePlaybackForActiveChannels()
  } else {
    await resetAllZonePreviewPlayers()
  }
  if (tab !== 'preview' && tab !== 'fence') {
    await resetAllBrowserPlayers()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', redrawAllFenceOverlays)
  if (pageDeactivated) {
    return
  }
  stopTaskStatusPolling()
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

.fence-preview-shell {
  isolation: isolate;
}

.zone-preview-player {
  width: 100%;
  aspect-ratio: 16 / 9;
  display: block;
  background: #000;
}

.fence-preview-player {
  position: relative;
  z-index: 1;
}

.fence-overlay-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 2;
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

.span-2 {
  grid-column: 1 / -1;
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

