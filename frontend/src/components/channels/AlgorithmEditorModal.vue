<template>
  <div v-if="visible" class="modal-mask" @click.self="$emit('close')">
    <div class="modal-dialog">
      <div class="modal-header">
        <div>
          <h3>{{ isEdit ? '编辑算法' : '新增算法' }}</h3>
          <p>这里仅配置算法参数。区域由“区域配置”入口单独维护，不在此窗口内绘制。</p>
        </div>
        <button class="ghost-btn modal-close-btn" type="button" @click="$emit('close')">×</button>
      </div>

      <div class="form-grid">
        <label class="field">
          <span>模型</span>
          <select v-model="draft.model_id" @change="applyModelRecommendation(true)">
            <option value="">请选择模型</option>
            <option
              v-for="model in modelOptions"
              :key="model.model_id || model.id"
              :value="model.model_id || model.id"
              :disabled="isModelDisabled(model)"
            >
              {{ model.name || model.model_id || model.id }}{{ isModelDisabled(model) ? '（已绑定）' : '' }}
            </option>
          </select>
        </label>

        <label class="field">
          <span>规则类型</span>
          <input v-model.trim="draft.algorithm_type" type="text" placeholder="detection" />
        </label>

        <label v-if="availableScenes.length" class="field span-2">
          <span>推荐场景</span>
          <select v-model="draft.scene_key">
            <option value="">不使用推荐场景</option>
            <option v-for="scene in availableScenes" :key="scene.scene_key" :value="scene.scene_key">
              {{ scene.scene_name }}
            </option>
          </select>
        </label>

        <div v-if="selectedModel" class="field span-2 recommendation-card">
          <div class="recommendation-title">
            <strong>模型推荐</strong>
            <span class="badge">{{ strategyLabel }}</span>
          </div>
          <p v-if="selectedModel.recommended_labels?.length" class="recommendation-copy">
            推荐类别：{{ selectedModel.recommended_labels.join('、') }}
          </p>
          <p v-else class="recommendation-copy">
            当前模型没有内置推荐类别，你可以按业务手动选择检测类别。
          </p>
        </div>

        <label class="field">
          <span>置信度</span>
          <input v-model.number="draft.confidence" type="number" min="0.05" max="1" step="0.05" />
        </label>

        <label class="field">
          <span>告警间隔（秒）</span>
          <input v-model.number="draft.alert_interval" type="number" min="0" step="1" />
        </label>

        <label class="field">
          <span>视频时长</span>
          <div class="static-value">6 秒（前后各 3 秒）</div>
        </label>

        <label class="field">
          <span>算法状态</span>
          <div class="static-value">{{ draft.enabled ? '已启用' : '未启用' }}</div>
        </label>

        <label class="field span-2">
          <span>检测类别</span>
          <input
            v-model.trim="labelsText"
            type="text"
            placeholder="多个类别用英文逗号分隔，例如 person,helmet"
          />
        </label>

        <div v-if="selectedModel?.classes?.length" class="field span-2 label-tools">
          <button class="ghost-btn mini-btn" type="button" @click="applyRecommendedLabels">
            使用推荐类别
          </button>
          <button class="ghost-btn mini-btn" type="button" @click="applyAllModelLabels">
            选择全部类别
          </button>
          <button class="ghost-btn mini-btn" type="button" @click="clearLabels">
            清空类别
          </button>
          <span class="label-summary">当前 {{ currentLabelCount }} / {{ selectedModel.classes.length }} 个类别</span>
        </div>

        <div class="field checkbox-group">
          <label class="checkbox-item">
            <input v-model="draft.need_snapshot" type="checkbox" />
            <span>保存抓拍图</span>
          </label>
          <label class="checkbox-item">
            <input v-model="draft.need_video_clip" type="checkbox" />
            <span>保存告警视频</span>
          </label>
          <label class="checkbox-item">
            <input v-model="draft.enabled" type="checkbox" />
            <span>启用算法</span>
          </label>
        </div>
      </div>

      <div v-if="duplicateModelHint" class="inline-error">
        当前任务已经添加过这个模型，请直接编辑已有算法。
      </div>

      <div class="action-row">
        <button class="ghost-btn" type="button" @click="$emit('close')">取消</button>
        <button class="primary-btn" type="button" :disabled="duplicateModelHint" @click="submit">
          {{ isEdit ? '保存算法' : '新增算法' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

type SceneCandidate = {
  scene_key: string
  scene_name: string
  recommended_labels?: string[]
  rule_type?: string
  default_need_snapshot?: boolean
  default_need_video_clip?: boolean
}

type ModelOption = {
  model_id?: string
  id?: string
  name?: string
  algorithm_type?: string
  scene_key?: string
  scene_name?: string
  rule_type?: string
  enabled?: boolean
  classes?: string[]
  auto_label_strategy?: 'all' | 'recommended' | 'manual'
  recommended_labels?: string[]
  detect_all_classes?: boolean
  label_source?: string
  confidence?: number
  alert_interval?: number
  need_snapshot?: boolean
  need_video_clip?: boolean
  video_before_seconds?: number
  video_after_seconds?: number
  overlay_color?: string
  postprocess_config?: Record<string, any>
  scene_candidates?: SceneCandidate[]
}

const props = defineProps<{
  visible: boolean
  algorithm?: any | null
  modelOptions: ModelOption[]
  usedModelIds?: string[]
}>()

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'submit', payload: any): void
}>()

const createDefaultDraft = () => ({
  id: undefined,
  algorithm_type: 'detection',
  scene_key: '',
  scene_name: '',
  rule_type: 'detection',
  model_id: '',
  enabled: true,
  labels: [] as string[],
  detect_all_classes: false,
  label_source: 'manual',
  confidence: 0.25,
  alert_interval: 60,
  need_snapshot: true,
  need_video_clip: true,
  video_before_seconds: 3,
  video_after_seconds: 3,
  overlay_color: '#ff4d4f',
  postprocess_config: {} as Record<string, any>,
  regions: [] as any[],
})

const draft = ref<any>(createDefaultDraft())
const labelsText = ref('')
const isEdit = computed(() => Boolean(props.algorithm?.id))
const recommendationApplied = ref(false)

const currentAlgorithmModelId = computed(() => String(props.algorithm?.model_id || ''))

const selectedModel = computed<ModelOption | null>(() => {
  const modelId = draft.value.model_id
  if (!modelId) return null
  return props.modelOptions.find((item) => (item.model_id || item.id) === modelId) || null
})

const availableScenes = computed<SceneCandidate[]>(() => selectedModel.value?.scene_candidates || [])

const selectedScene = computed<SceneCandidate | null>(() => {
  if (!draft.value.scene_key) return null
  return availableScenes.value.find((item) => item.scene_key === draft.value.scene_key) || null
})

const strategyLabel = computed(() => {
  const strategy = selectedModel.value?.auto_label_strategy
  if (strategy === 'all') return '默认全选'
  if (strategy === 'recommended') return '智能推荐'
  return '手动选择'
})

const currentLabelCount = computed(() => {
  return labelsText.value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean).length
})

const backendManagedRuleTypes = new Set(['absence', 'gathering', 'congestion', 'loitering', 'fall_like'])

const isModelDisabled = (model: ModelOption) => {
  const modelId = String(model.model_id || model.id || '')
  if (!modelId) return false
  if (modelId === currentAlgorithmModelId.value) return false
  return (props.usedModelIds || []).includes(modelId)
}

const duplicateModelHint = computed(() => {
  const modelId = String(draft.value.model_id || '')
  if (!modelId) return false
  if (modelId === currentAlgorithmModelId.value) return false
  return (props.usedModelIds || []).includes(modelId)
})

const setLabels = (labels: string[]) => {
  labelsText.value = labels.join(',')
  draft.value.labels = [...labels]
}

const applyModelDefaults = (model: ModelOption) => {
  draft.value.algorithm_type = String(model.algorithm_type || 'detection')
  draft.value.rule_type = String(model.rule_type || model.algorithm_type || 'detection')
  draft.value.enabled = model.enabled ?? true
  draft.value.confidence = Number(model.confidence ?? 0.25)
  draft.value.alert_interval = Number(model.alert_interval ?? 60)
  draft.value.need_snapshot = Boolean(model.need_snapshot ?? true)
  draft.value.need_video_clip = Boolean(model.need_video_clip ?? true)
  draft.value.overlay_color = String(model.overlay_color || '#ff4d4f')
  draft.value.postprocess_config = { ...(model.postprocess_config || {}) }
  draft.value.video_before_seconds = draft.value.need_video_clip ? 3 : 0
  draft.value.video_after_seconds = draft.value.need_video_clip ? 3 : 0
}

const applySceneCandidate = (scene: SceneCandidate | null) => {
  if (!scene) {
    draft.value.scene_name = ''
    draft.value.rule_type = selectedModel.value?.rule_type || draft.value.algorithm_type || 'detection'
    if (!recommendationApplied.value) {
      draft.value.label_source = 'manual'
    }
    return
  }

  draft.value.scene_key = scene.scene_key
  draft.value.scene_name = scene.scene_name
  draft.value.rule_type = scene.rule_type || selectedModel.value?.rule_type || draft.value.algorithm_type || 'detection'
  draft.value.algorithm_type = selectedModel.value?.algorithm_type || draft.value.algorithm_type || 'detection'
  draft.value.need_snapshot = Boolean(scene.default_need_snapshot ?? true)
  draft.value.need_video_clip = Boolean(scene.default_need_video_clip ?? true)
  draft.value.detect_all_classes = false
  draft.value.label_source = 'recommended'
  setLabels(scene.recommended_labels || [])
  recommendationApplied.value = true
}

const applyModelRecommendation = (force = false) => {
  const model = selectedModel.value
  if (!model) return
  if (isEdit.value && !force) return

  const strategy = model.auto_label_strategy
  const classes = model.classes || []
  const recommendedLabels = model.recommended_labels || []
  const firstScene = (model.scene_candidates || [])[0] || null

  applyModelDefaults(model)

  if (strategy === 'all') {
    draft.value.detect_all_classes = true
    draft.value.label_source = 'all'
    setLabels(classes)
    if (firstScene) {
      applySceneCandidate(firstScene)
      draft.value.detect_all_classes = true
      draft.value.label_source = 'all'
      setLabels(classes)
    } else {
      draft.value.scene_key = ''
      draft.value.scene_name = ''
      draft.value.rule_type = model.rule_type || draft.value.algorithm_type || 'detection'
    }
    recommendationApplied.value = true
    return
  }

  if (strategy === 'recommended' && firstScene) {
    applySceneCandidate(firstScene)
    if (!firstScene.recommended_labels?.length && recommendedLabels.length) {
      setLabels(recommendedLabels)
    }
    draft.value.detect_all_classes = false
    draft.value.label_source = 'recommended'
    recommendationApplied.value = true
    return
  }

  draft.value.detect_all_classes = false
  draft.value.label_source = 'manual'
  draft.value.scene_key = model.scene_key || ''
  draft.value.scene_name = model.scene_name || ''
  draft.value.rule_type = model.rule_type || draft.value.algorithm_type || 'detection'
  if (!isEdit.value) {
    setLabels(recommendedLabels)
  }
  recommendationApplied.value = true
}

const applyRecommendedLabels = () => {
  if (selectedScene.value?.recommended_labels?.length) {
    draft.value.detect_all_classes = false
    draft.value.label_source = 'recommended'
    setLabels(selectedScene.value.recommended_labels)
    return
  }
  if (selectedModel.value?.recommended_labels?.length) {
    draft.value.detect_all_classes = false
    draft.value.label_source = selectedModel.value.auto_label_strategy === 'all' ? 'all' : 'recommended'
    setLabels(selectedModel.value.recommended_labels)
  }
}

const applyAllModelLabels = () => {
  const labels = selectedModel.value?.classes || []
  draft.value.detect_all_classes = true
  draft.value.label_source = 'all'
  setLabels(labels)
}

const clearLabels = () => {
  draft.value.detect_all_classes = false
  draft.value.label_source = 'manual'
  setLabels([])
}

watch(
  () => [props.visible, props.algorithm],
  () => {
    recommendationApplied.value = false
    const source = props.algorithm
      ? JSON.parse(JSON.stringify(props.algorithm))
      : createDefaultDraft()

    source.need_video_clip = Boolean(source.need_video_clip ?? true)
    source.video_before_seconds = source.need_video_clip ? 3 : 0
    source.video_after_seconds = source.need_video_clip ? 3 : 0
    source.detect_all_classes = Boolean(source.detect_all_classes ?? false)
    source.label_source = source.label_source || 'manual'
    source.scene_key = source.scene_key || ''
    source.scene_name = source.scene_name || ''
    source.rule_type = source.rule_type || source.algorithm_type || 'detection'
    source.algorithm_type = source.algorithm_type || 'detection'
    source.confidence = Number(source.confidence ?? 0.25)
    source.alert_interval = Number(source.alert_interval ?? 60)
    source.postprocess_config = source.postprocess_config || {}

    draft.value = source
    labelsText.value = (source.labels || []).join(',')
  },
  { immediate: true, deep: true },
)

watch(
  () => draft.value.model_id,
  (current, previous) => {
    if (!current || current === previous) return
    applyModelRecommendation(false)
  },
)

watch(
  () => draft.value.scene_key,
  (sceneKey) => {
    if (!sceneKey) {
      if (!isEdit.value) {
        draft.value.scene_name = ''
      }
      return
    }
    const scene = availableScenes.value.find((item) => item.scene_key === sceneKey) || null
    if (scene) {
      applySceneCandidate(scene)
    }
  },
)

watch(
  () => draft.value.need_video_clip,
  (enabled) => {
    draft.value.video_before_seconds = enabled ? 3 : 0
    draft.value.video_after_seconds = enabled ? 3 : 0
  },
)

const submit = () => {
  if (duplicateModelHint.value) {
    return
  }

  const labels = labelsText.value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)

  const ruleType = draft.value.rule_type || draft.value.algorithm_type || 'detection'
  const payload = {
    ...draft.value,
    labels,
    scene_key: draft.value.scene_key || undefined,
    scene_name: draft.value.scene_name || undefined,
    rule_type: ruleType,
    detect_all_classes: Boolean(draft.value.detect_all_classes),
    label_source: draft.value.label_source || 'manual',
    need_overlay: false,
    requires_region: false,
    postprocess_config: backendManagedRuleTypes.has(String(ruleType)) ? {} : (draft.value.postprocess_config || {}),
    video_before_seconds: draft.value.need_video_clip ? 3 : 0,
    video_after_seconds: draft.value.need_video_clip ? 3 : 0,
  }

  emit('submit', payload)
}
</script>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.62);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.25rem;
  z-index: 80;
}

.modal-dialog {
  width: min(900px, 100%);
  background: #fff;
  border-radius: 16px;
  padding: 1rem;
  display: grid;
  gap: 1rem;
}

.modal-header,
.action-row {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  align-items: flex-start;
  flex-wrap: wrap;
}

.modal-header h3 {
  margin: 0;
}

.modal-header p {
  margin: 0.35rem 0 0;
  color: #64748b;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.field {
  display: grid;
  gap: 0.4rem;
}

.field span {
  color: #334155;
  font-weight: 600;
}

.field input,
.field select,
.static-value {
  min-height: 40px;
  padding: 0.65rem 0.8rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  font: inherit;
  box-sizing: border-box;
}

.static-value {
  display: flex;
  align-items: center;
  background: #f8fafc;
  color: #475569;
}

.span-2 {
  grid-column: 1 / -1;
}

.recommendation-card {
  padding: 0.85rem 0.95rem;
  border: 1px solid #dbeafe;
  background: #f8fbff;
  border-radius: 12px;
}

.recommendation-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.recommendation-copy {
  margin: 0.4rem 0 0;
  color: #475569;
}

.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 24px;
  padding: 0 0.55rem;
  border-radius: 999px;
  background: #dbeafe;
  color: #1d4ed8;
  font-size: 0.82rem;
  font-weight: 600;
}

.label-tools {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  flex-wrap: wrap;
}

.label-summary {
  color: #64748b;
  font-size: 0.92rem;
}

.checkbox-group {
  grid-column: 1 / -1;
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
  align-items: center;
  padding: 0.85rem;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #f8fafc;
}

.checkbox-item {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  color: #334155;
}

.overlay-guide {
  grid-column: 1 / -1;
  padding: 0.85rem 0.95rem;
  border-radius: 12px;
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
  line-height: 1.5;
}

.inline-error {
  padding: 0.8rem 0.95rem;
  border-radius: 12px;
  background: #fff1f2;
  border: 1px solid #fecdd3;
  color: #be123c;
}

.primary-btn,
.ghost-btn {
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
.ghost-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.ghost-btn {
  background: #fff;
  color: #334155;
}

.mini-btn {
  min-height: 34px;
  padding: 0.45rem 0.8rem;
}

.modal-close-btn {
  min-width: 40px;
  padding: 0.4rem 0.75rem;
  font-size: 1.2rem;
  line-height: 1;
}

@media (max-width: 768px) {
  .form-grid {
    grid-template-columns: 1fr;
  }

  .span-2 {
    grid-column: auto;
  }
}
</style>
