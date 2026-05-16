<template>
  <div class="channels-page">
    <section class="panel header-panel">
      <div class="panel-header">
        <div>
          <h2>通道</h2>
          <p>把预览、通道管理和 AI 能力拆开，避免所有操作堆在同一屏里。</p>
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
        <button class="primary-btn" @click="openCreateModal">创建通道</button>
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

    <section v-if="activeTab === 'ai'" class="panel">
      <div class="section-head">
        <h3>AI 能力</h3>
        <span>集中管理 AI 配置、区域配置和运行状态</span>
      </div>

      <div v-if="items.length === 0" class="empty-state">暂无通道可配置</div>
      <div v-else class="ai-list">
        <article v-for="item in items" :key="item.id" class="ai-card">
          <div class="ai-card-head">
            <div>
              <h4>{{ item.name }}</h4>
              <p>{{ item.id }}</p>
            </div>
            <div class="status-pills">
              <span class="pill type">{{ String(item.type || '').toUpperCase() }}</span>
              <span :class="['pill', aiStatuses[item.id]?.is_running ? 'ai-on' : 'muted']">
                AI {{ aiStatusLabel(item.id) }}
              </span>
            </div>
          </div>

          <div class="ai-card-summary">
            <div><strong>当前模型：</strong>{{ currentChannelModelLabel(item) }}</div>
            <div><strong>区域数：</strong>{{ channelZones(item.id).length }}</div>
            <div><strong>事件数：</strong>{{ aiStatuses[item.id]?.event_count ?? 0 }}</div>
            <div><strong>最近分析：</strong>{{ aiStatuses[item.id]?.last_analysis_at || '-' }}</div>
          </div>

          <!--
          <div
            v-if="modelRuntimeEntries(item.id).length"
            class="ai-debug-board"
          >
            <div class="section-title">调试状态</div>
            <div class="ai-debug-help">
              用来判断事件卡在模型检测、类别过滤、区域过滤还是冷却时间。
            </div>
            <div class="ai-debug-list">
              <div
                v-for="[modelId, runtime] in modelRuntimeEntries(item.id)"
                :key="`${item.id}-${modelId}`"
                class="ai-debug-card"
              >
                <div class="ai-debug-head">
                  <strong>{{ modelRuntimeName(modelId) }}</strong>
                  <span class="pill type">{{ runtime.debug_reason || '待分析' }}</span>
                </div>
                <div class="ai-debug-grid">
                  <div><strong>原始检测：</strong>{{ runtime.raw_detections ?? 0 }}</div>
                  <div><strong>类别过滤后：</strong>{{ runtime.label_filtered_detections ?? 0 }}</div>
                  <div><strong>区域过滤后：</strong>{{ runtime.zone_filtered_detections ?? 0 }}</div>
                  <div><strong>冷却拦截：</strong>{{ runtime.cooldown_blocked_detections ?? 0 }}</div>
                  <div><strong>本轮事件：</strong>{{ runtime.event_emitted_detections ?? 0 }}</div>
                  <div><strong>累计事件：</strong>{{ runtime.total_event_emitted_detections ?? 0 }}</div>
                  <div><strong>累计冷却拦截：</strong>{{ runtime.total_cooldown_blocked_detections ?? 0 }}</div>
                  <div><strong>最近模型分析：</strong>{{ runtime.last_analysis_at || '-' }}</div>
                  <div><strong>最近模型事件：</strong>{{ runtime.last_event_at || '-' }}</div>
                  <div><strong>已配类别：</strong>{{ joinLabels(runtime.configured_labels) }}</div>
                  <div><strong>已配区域：</strong>{{ joinLabels(runtime.configured_zone_ids) }}</div>
                  <div><strong>模型检出类别：</strong>{{ joinLabels(runtime.raw_classes) }}</div>
                  <div><strong>类别过滤后类别：</strong>{{ joinLabels(runtime.label_filtered_classes) }}</div>
                  <div><strong>区域过滤后类别：</strong>{{ joinLabels(runtime.zone_filtered_classes) }}</div>
                </div>
              </div>
            </div>
          </div>
          -->

          <div class="ai-tool-row">
            <button class="ghost-btn" @click="openZoneEditor(item)">区域配置</button>
            <button class="ai-btn" @click="startAI(item.id)">启动 AI</button>
            <button class="ai-stop-btn" @click="stopAI(item.id)">停止 AI</button>
          </div>

          <div class="ai-config-box">
            <div class="section-title">AI 配置</div>
            <div class="ai-config-help">
              同一个通道可配置多个 AI 模型，每个模型单独设置类别、置信度、区域和抓拍策略。
            </div>

            <div class="ai-config-form ai-config-grid">
              <div class="config-actions span-2">
                <button class="ghost-btn" type="button" @click="addModelConfig(item.id)">新增模型配置</button>
              </div>

              <div
                v-for="modelConfig in aiConfigs[item.id].models"
                :key="modelConfig.local_id"
                class="config-row span-2 multi-model-card"
              >
                <div class="multi-model-head">
                  <strong>{{ getModelById(modelConfig.model_id)?.name || modelConfig.model_id || '未选择模型' }}</strong>
                  <button class="danger-btn mini" type="button" @click="removeModelConfig(item.id, modelConfig.local_id)">
                    删除
                  </button>
                </div>

                <div class="multi-model-grid">
                  <div class="config-row compact-field">
                    <div class="config-label">
                      <strong>模型</strong>
                      <span>选择该条 AI 配置对应的模型。</span>
                    </div>
                    <div class="config-control">
                      <select v-model="modelConfig.model_id" @change="handleModelChange(item.id, modelConfig.local_id)">
                        <option value="">请选择模型</option>
                        <option v-for="model in models" :key="model.model_id" :value="model.model_id">
                          {{ modelDisplayName(model) }}
                        </option>
                      </select>
                    </div>
                  </div>

                  <div class="config-row compact-field">
                    <div class="config-label">
                      <strong>置信度</strong>
                      <span>低于该阈值的检测结果会被忽略。</span>
                    </div>
                    <div class="config-control">
                      <input
                        v-model.number="modelConfig.conf"
                        type="number"
                        min="0.05"
                        max="1"
                        step="0.05"
                        placeholder="例如 0.25"
                      />
                    </div>
                  </div>

                  <div class="config-row compact-field">
                    <div class="config-label">
                      <strong>帧数</strong>
                      <span>每隔多少帧分析一次，值越小越实时。</span>
                    </div>
                    <div class="config-control">
                      <input
                        v-model.number="modelConfig.frame_interval"
                        type="number"
                        min="1"
                        step="1"
                        placeholder="例如 10"
                      />
                    </div>
                  </div>

                  <div class="config-row compact-field save-snapshot-row">
                    <div class="config-control">
                      <label class="checkbox-item ai-checkbox-item">
                        <input v-model="modelConfig.save_snapshot" type="checkbox" />
                        <span>保存抓拍图（事件产生时保存图片，供事件中心查看）</span>
                      </label>
                    </div>
                  </div>

                  <div class="config-row span-2">
                    <div class="config-label">
                      <strong>类别</strong>
                      <span>根据已选模型自动读取可检测类别。</span>
                    </div>
                    <div class="config-control">
                      <div
                        v-if="modelConfig.model_id && !hasModel(modelConfig.model_id)"
                        class="inline-warning"
                      >
                        当前已保存的模型 ID 无效：{{ modelConfig.model_id }}，请重新选择正确模型。
                      </div>

                      <div v-else-if="modelLabelsForChannel(item.id, modelConfig.local_id).length" class="compact-select-panel">
                        <div class="select-toolbar">
                          <button class="ghost-btn mini" type="button" @click="selectAllChannelLabels(item.id, modelConfig.local_id)">
                            全选
                          </button>
                          <button class="ghost-btn mini" type="button" @click="clearAllChannelLabels(item.id, modelConfig.local_id)">
                            取消全选
                          </button>
                          <span class="select-summary">
                            已选 {{ modelConfig.labels.length }} / {{ modelLabelsForChannel(item.id, modelConfig.local_id).length }}
                          </span>
                        </div>

                        <div class="label-select-box compact-chip-box">
                          <label
                            v-for="label in modelLabelsForChannel(item.id, modelConfig.local_id)"
                            :key="`${item.id}-${modelConfig.local_id}-${label}`"
                            :class="['label-chip', 'compact-chip', { active: modelConfig.labels.includes(label) }]"
                          >
                            <input
                              :checked="modelConfig.labels.includes(label)"
                              type="checkbox"
                              @change="toggleChannelLabel(item.id, modelConfig.local_id, label)"
                            />
                            <span>{{ label }}</span>
                          </label>
                        </div>
                      </div>

                      <div v-else class="inline-muted">
                        当前模型没有可用类别列表，请先确认模型已正确提取 `classes`。
                      </div>
                    </div>
                  </div>

                  <div class="config-row span-2">
                    <div class="config-label">
                      <strong>区域</strong>
                      <span>仅对所选区域内的目标生效，不选则为全画面检测。</span>
                    </div>
                    <div class="config-control">
                      <div v-if="channelZones(item.id).length" class="compact-select-panel">
                        <div class="select-summary">
                          已选 {{ modelConfig.zone_ids.length }} / {{ channelZones(item.id).length }}
                        </div>

                        <div class="zone-select-box compact-chip-box">
                          <label
                            v-for="zone in channelZones(item.id)"
                            :key="`${modelConfig.local_id}-${zone.id}`"
                            :class="['zone-chip', 'compact-chip', { active: modelConfig.zone_ids.includes(zone.id) }]"
                          >
                            <input
                              type="checkbox"
                              :checked="modelConfig.zone_ids.includes(zone.id)"
                              @change="toggleChannelZone(item.id, modelConfig.local_id, zone.id)"
                            />
                            <span class="zone-chip-color" :style="{ background: zone.color || '#3b82f6' }"></span>
                            <span>{{ zone.name }}</span>
                          </label>
                        </div>
                      </div>
                      <div v-else class="inline-muted">
                        还没有区域，请先点击上方“区域配置”创建多边形区域。
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div class="config-actions span-2">
                <button class="save-ai-btn" @click="saveAIConfig(item.id)">保存 AI 配置</button>
              </div>
            </div>
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
  </div>
</template>

<script setup lang="ts">
import { nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import ZoneEditorModal from '@/components/channels/ZoneEditorModal.vue'
import {
  createChannel,
  deleteChannel,
  getChannelAIStatus,
  getChannelPlayUrl,
  listChannels,
  listChannelZones,
  startChannelAI,
  startChannelPlay,
  stopChannelAI,
  stopChannelPlay,
  updateChannel,
  updateChannelAIConfig,
} from '@/api/channels'
import { listMediaNodes } from '@/api/mediaNodes'
import { listModels } from '@/api/models'

type PlayUrlMap = Record<string, string>

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
  { key: 'ai', label: 'AI 能力' },
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
const aiStatuses = ref<AIStatusMap>({})
const aiConfigs = ref<Record<string, ChannelAIConfigState>>({})
const zonesMap = ref<Record<string, ZoneItem[]>>({})
const debugResults = ref<Record<string, string>>({})
const rtcErrors = ref<Record<string, string>>({})
const message = ref('')
const messageType = ref<'success' | 'error'>('success')
const editingChannel = ref<any | null>(null)

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
const previewCardRefs = new Map<string, HTMLElement>()
const expandedPreview = ref<{ channelId: string; name: string; sourceUrl: string } | null>(null)
const expandedPreviewVideoRef = ref<HTMLVideoElement | null>(null)
const expandedPreviewPlaying = ref(false)
const expandedPreviewError = ref('')
let aiStatusPollTimer: number | null = null
let pageDeactivated = false
let expandedPreviewPeerConnection: RTCPeerConnection | null = null
let expandedPreviewAttemptId = 0

const showMessage = (type: 'success' | 'error', text: string) => {
  messageType.value = type
  message.value = text
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

const openCreateModal = () => {
  resetCreateForm()
  creatingChannel.value = true
}

const closeCreateModal = () => {
  creatingChannel.value = false
}

const isPreviewPlaying = (channelId: string) => Boolean(previewPlaybackState.value[channelId])
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

const currentChannelModelLabel = (item: any) => {
  const configs = normalizeChannelModelConfigs(item?.ext?.ai_config || {})
  if (!configs.length) return '-'
  return configs
    .map((config) => {
      const model = getModelById(config.model_id)
      return model ? modelDisplayName(model) : config.model_id
    })
    .join(' / ')
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

const hasModel = (modelId: string | undefined) => {
  if (!modelId) return false
  return models.value.some((model) => model.model_id === modelId)
}

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

const getChannelModelConfig = (channelId: string, localId: string) => {
  const config = aiConfigs.value[channelId]
  return config?.models.find((item) => item.local_id === localId)
}

const modelLabelsForChannel = (channelId: string, localId: string) => {
  const config = getChannelModelConfig(channelId, localId)
  const model = getModelById(config?.model_id)
  return Array.isArray(model?.classes) ? model.classes : []
}

const channelZones = (channelId: string) => zonesMap.value[channelId] || []

const syncLabelsWithModel = (channelId: string, localId: string) => {
  const config = getChannelModelConfig(channelId, localId)
  if (!config) return
  const allowed = new Set(modelLabelsForChannel(channelId, localId))
  config.labels = config.labels.filter((label) => allowed.has(label))
}

const handleModelChange = (channelId: string, localId: string) => {
  const config = getChannelModelConfig(channelId, localId)
  if (!config) return
  config.labels = [...modelLabelsForChannel(channelId, localId)]
}

const selectAllChannelLabels = (channelId: string, localId: string) => {
  const config = getChannelModelConfig(channelId, localId)
  if (!config) return
  config.labels = [...modelLabelsForChannel(channelId, localId)]
}

const clearAllChannelLabels = (channelId: string, localId: string) => {
  const config = getChannelModelConfig(channelId, localId)
  if (!config) return
  config.labels = []
}

const toggleChannelLabel = (channelId: string, localId: string, label: string) => {
  const config = getChannelModelConfig(channelId, localId)
  if (!config) return
  if (config.labels.includes(label)) {
    config.labels = config.labels.filter((item) => item !== label)
    return
  }
  config.labels = [...config.labels, label]
}

const toggleChannelZone = (channelId: string, localId: string, zoneId: string) => {
  const config = getChannelModelConfig(channelId, localId)
  if (!config) return
  if (config.zone_ids.includes(zoneId)) {
    config.zone_ids = config.zone_ids.filter((item) => item !== zoneId)
    return
  }
  config.zone_ids = [...config.zone_ids, zoneId]
}

const addModelConfig = (channelId: string) => {
  const config = aiConfigs.value[channelId]
  if (!config) return
  config.models = [...config.models, createEmptyModelConfig()]
}

const removeModelConfig = (channelId: string, localId: string) => {
  const config = aiConfigs.value[channelId]
  if (!config) return
  config.models = config.models.filter((item) => item.local_id !== localId)
  if (!config.models.length) {
    config.models = [createEmptyModelConfig()]
  }
}

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

const load = async () => {
  loading.value = true
  try {
    const [channelResult, mediaNodeResult, modelResult] = await Promise.all([
      listChannels(),
      listMediaNodes(),
      listModels(),
    ])
    items.value = channelResult.items || []
    const validIds = new Set(items.value.map((item) => item.id))
    previewPlaybackState.value = Object.fromEntries(
      Object.entries(previewPlaybackState.value).filter(([channelId]) => validIds.has(channelId))
    )
    mediaNodes.value = mediaNodeResult.items || []
    models.value = modelResult.models || modelResult.items || []
    syncAIConfigsFromItems()
    await Promise.all([loadAllZones(), loadAIStatuses()])
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

const setPreviewCardRef = (channelId: string, el: HTMLElement | null) => {
  if (el) {
    previewCardRefs.set(channelId, el)
  } else {
    previewCardRefs.delete(channelId)
  }
}

const nextPlayAttempt = (channelId: string) => {
  const nextId = (playAttemptIds.get(channelId) || 0) + 1
  playAttemptIds.set(channelId, nextId)
  return nextId
}

const isActiveAttempt = (channelId: string, attemptId: number) => playAttemptIds.get(channelId) === attemptId

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

  const result = await getChannelPlayUrl(channelId)
  playInfos.value[channelId] = result.urls || {}
  browserPlayInfos.value[channelId] = result.browser_urls || {}
  return {
    urls: playInfos.value[channelId] || {},
    browser_urls: browserPlayInfos.value[channelId] || {},
  }
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

const safePlay = async (video: HTMLVideoElement, channelId: string, attemptId: number) => {
  try {
    await video.play()
  } catch (error: any) {
    const text = String(error?.message || '')
    if (
      !isActiveAttempt(channelId, attemptId) ||
      error?.name === 'AbortError' ||
      text.includes('interrupted by a call to pause') ||
      text.includes('interrupted by a new load request')
    ) {
      return
    }
    throw error
  }
}

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

  try {
    expandedPreviewError.value = ''
    await stopExpandedPreviewPlayback(false)
    await nextTick()

    const playInfo = await ensurePlayInfo(channelId)
    const browserUrls = playInfo.browser_urls || {}
    const normalUrls = playInfo.urls || {}
    const selectedUrls = Object.keys(browserUrls).length > 0 ? browserUrls : normalUrls
    const apiUrl = selectedUrls.browser_webrtc_api

    if (!apiUrl) throw new Error('没有可用的 WebRTC API 地址')

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
    const playInfo = await ensurePlayInfo(channelId, true)
    const browserUrls = playInfo.browser_urls || {}
    const normalUrls = playInfo.urls || {}
    const fallbackUrls = Object.keys(browserUrls).length > 0 ? browserUrls : normalUrls

    if (!autoStartedProxy && currentMessage.includes('stream not found')) {
      try {
        const result = await startChannelPlay(channelId)
        playInfos.value[channelId] = result.urls || {}
        browserPlayInfos.value[channelId] = result.browser_urls || {}
        autoStartedProxy = true
        await sleep(800)
      } catch {
        // Ignore and continue with retry/fallback flow below.
      }
    }

    if (Object.keys(browserUrls).length > 0 && currentMessage.includes('stream not found')) {
      for (let i = 0; i < 6; i += 1) {
        if (!isActiveExpandedPreviewAttempt(attemptId)) return
        await sleep(500)
        try {
          const refreshed = await ensurePlayInfo(channelId, true)
          const retryUrls =
            Object.keys(refreshed.browser_urls || {}).length > 0 ? refreshed.browser_urls || {} : refreshed.urls || {}
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
      const hlsUrl = fallbackUrls.browser_hls
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
  const attemptId = nextPlayAttempt(channelId)
  let video: HTMLVideoElement | null = null
  let autoStartedProxy = false

  try {
    rtcErrors.value[channelId] = ''
    await stopBrowserPlay(channelId, false)
    await nextTick()

    const playInfo = await ensurePlayInfo(channelId)
    const browserUrls = playInfo.browser_urls || {}
    const normalUrls = playInfo.urls || {}
    const selectedUrls = Object.keys(browserUrls).length > 0 ? browserUrls : normalUrls
    const apiUrl = selectedUrls.browser_webrtc_api

    if (!apiUrl) throw new Error('没有可用的 WebRTC API 地址')

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
      debugResults.value[channelId] =
        Object.keys(browserUrls).length > 0
          ? '浏览器专用转码流已通过 WebRTC 播放'
          : '原始流已通过 WebRTC 播放'
    }
  } catch (error: any) {
    if (!isActiveAttempt(channelId, attemptId)) return
    const currentMessage = error?.message || '浏览器播放失败'
    const playInfo = await ensurePlayInfo(channelId, true)
    const browserUrls = playInfo.browser_urls || {}
    const normalUrls = playInfo.urls || {}
    const fallbackUrls = Object.keys(browserUrls).length > 0 ? browserUrls : normalUrls

    if (!autoStartedProxy && currentMessage.includes('stream not found')) {
      try {
        const result = await startChannelPlay(channelId)
        playInfos.value[channelId] = result.urls || {}
        browserPlayInfos.value[channelId] = result.browser_urls || {}
        autoStartedProxy = true
        await sleep(800)
      } catch {
        // Ignore here and continue with the existing fallback flow below.
      }
    }

    if (Object.keys(browserUrls).length > 0 && currentMessage.includes('stream not found')) {
      for (let i = 0; i < 6; i += 1) {
        if (!isActiveAttempt(channelId, attemptId)) return
        await sleep(500)
        try {
          const refreshed = await ensurePlayInfo(channelId, true)
          const retryUrls =
            Object.keys(refreshed.browser_urls || {}).length > 0 ? refreshed.browser_urls || {} : refreshed.urls || {}
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
  }
}

const startPlay = async (channelId: string) => {
  try {
    const result = await startChannelPlay(channelId)
    playInfos.value[channelId] = result.urls || {}
    browserPlayInfos.value[channelId] = result.browser_urls || {}
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

const saveAIConfig = async (channelId: string) => {
  try {
    const config = aiConfigs.value[channelId]
    const activeModels = (config?.models || []).filter((item) => item.model_id)
    if (!activeModels.length) {
      throw new Error('请至少选择一个 AI 模型')
    }
    for (const modelConfig of activeModels) {
      if (!hasModel(modelConfig.model_id)) {
        throw new Error(`模型不存在：${modelConfig.model_id}`)
      }
      syncLabelsWithModel(channelId, modelConfig.local_id)
    }
    await updateChannelAIConfig(channelId, {
      enabled: config.enabled,
      model_id: activeModels[0]?.model_id,
      labels: activeModels[0]?.labels || [],
      conf: activeModels[0]?.conf,
      frame_interval: activeModels[0]?.frame_interval,
      zone_ids: activeModels[0]?.zone_ids || [],
      save_snapshot: activeModels[0]?.save_snapshot,
      models: activeModels.map(({ local_id, ...rest }) => rest),
    })
    await load()
    debugResults.value[channelId] = 'AI 配置已保存'
  } catch (error: any) {
    debugResults.value[channelId] = error?.response?.data?.detail || error?.message || '保存 AI 配置失败'
  }
}

const startAI = async (channelId: string) => {
  try {
    const channel = items.value.find((entry) => entry.id === channelId)
    const modelConfigs = normalizeChannelModelConfigs(channel?.ext?.ai_config || {}).filter((item) => item.model_id)
    if (!modelConfigs.length) {
      throw new Error('请先为该通道配置至少一个 AI 模型')
    }
    const invalidModel = modelConfigs.find((item) => !hasModel(item.model_id))
    if (invalidModel) {
      throw new Error(`模型不存在：${invalidModel.model_id}，请重新保存正确模型`)
    }
    const result = await startChannelAI(channelId)
    aiConfigs.value[channelId].enabled = true
    debugResults.value[channelId] = `AI 状态：${result.status || 'starting'}`
    showMessage('success', `${channel?.name || channelId} 已启动 AI 能力`)
    await loadAIStatuses()
  } catch (error: any) {
    debugResults.value[channelId] = error?.response?.data?.detail || error?.message || '启动 AI 失败'
  }
}

const stopAI = async (channelId: string) => {
  try {
    const channel = items.value.find((entry) => entry.id === channelId)
    const result = await stopChannelAI(channelId)
    if (aiConfigs.value[channelId]) {
      aiConfigs.value[channelId].enabled = false
    }
    debugResults.value[channelId] = `AI 状态：${result.status || 'stopping'}`
    showMessage('success', `${channel?.name || channelId} 已停止 AI 能力`)
    await loadAIStatuses()
  } catch (error: any) {
    debugResults.value[channelId] = error?.response?.data?.detail || error?.message || '停止 AI 失败'
  }
}

const openZoneEditor = (item: any) => {
  const mergedLabels = Array.from(
    new Set(
      (aiConfigs.value[item.id]?.models || [])
        .flatMap((config) => modelLabelsForChannel(item.id, config.local_id))
        .filter(Boolean)
    )
  )
  zoneEditor.value = {
    visible: true,
    channelId: item.id,
    channelName: item.name,
    modelLabels: mergedLabels,
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
})

onActivated(() => {
  pageDeactivated = false
  if (aiStatusPollTimer === null) {
    aiStatusPollTimer = window.setInterval(() => {
      if (items.value.length > 0) {
        void loadAIStatuses()
      }
    }, 5000)
  }
  if (items.value.length > 0) {
    void restoreBrowserPlaybackForActiveChannels()
  }
})

onDeactivated(() => {
  pageDeactivated = true
  if (aiStatusPollTimer !== null) {
    window.clearInterval(aiStatusPollTimer)
    aiStatusPollTimer = null
  }
})

watch(activeTab, async (tab) => {
  if (tab === 'preview') {
    await restoreBrowserPlaybackForActiveChannels()
  }
})

onBeforeUnmount(() => {
  if (pageDeactivated) {
    return
  }
  if (aiStatusPollTimer !== null) {
    window.clearInterval(aiStatusPollTimer)
    aiStatusPollTimer = null
  }
  Array.from(peerConnections.keys()).forEach((channelId) => {
    void stopBrowserPlay(channelId)
  })
  void stopExpandedPreviewPlayback()
  previewPlaybackState.value = {}
})

aiStatusPollTimer = window.setInterval(() => {
  if (items.value.length > 0) {
    void loadAIStatuses()
  }
}, 5000)
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

.ai-card-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.55rem;
  color: #334155;
}

.ai-config-box {
  display: grid;
  gap: 0.75rem;
  padding: 0.95rem;
  background: #f8fafc;
  border: 1px solid #dbe4ee;
  border-radius: 14px;
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

.multi-model-grid {
  display: grid;
  gap: 0.75rem;
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

