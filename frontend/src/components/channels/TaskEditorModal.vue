<template>
  <div v-if="visible" class="modal-mask" @click.self="$emit('close')">
    <div class="modal-dialog">
      <div class="modal-header">
        <div>
          <h3>新建任务</h3>
          <p>先建立任务基础信息，创建后再继续配置算法。</p>
        </div>
        <button class="ghost-btn modal-close-btn" type="button" @click="$emit('close')">x</button>
      </div>

      <div class="form-grid">
        <label class="field">
          <span>任务名称</span>
          <input v-model.trim="draft.name" type="text" placeholder="例如：安全帽检测任务" />
        </label>
        <label class="field">
          <span>选择通道</span>
          <select v-model="draft.channel_id">
            <option v-for="channel in channelOptions" :key="channel.id" :value="channel.id">
              {{ channel.name }}
            </option>
          </select>
        </label>
        <label class="field span-2">
          <span>视频源</span>
          <input v-model.trim="draft.source_url" type="text" placeholder="将根据所选通道自动带出" readonly />
        </label>
        <label class="field">
          <span>帧间隔</span>
          <input v-model.number="draft.frame_interval" type="number" min="1" step="1" />
        </label>
      </div>

      <div class="action-row">
        <button class="ghost-btn" type="button" @click="$emit('close')">取消</button>
        <button class="primary-btn" type="button" @click="$emit('submit', draft)">创建任务</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{
  visible: boolean
  channelId: string
  sourceUrl: string
  channelOptions: Array<{
    id: string
    name: string
    source_url?: string
    type?: string
  }>
}>()

defineEmits<{
  (event: 'close'): void
  (event: 'submit', payload: any): void
}>()

const draft = ref({
  name: '',
  source_url: '',
  frame_interval: 10,
  channel_id: '',
  source_type: 'rtsp',
})

watch(
  () => [props.visible, props.channelId, props.sourceUrl, props.channelOptions],
  () => {
    const currentChannel =
      props.channelOptions.find((item) => item.id === props.channelId)
      || props.channelOptions[0]
    draft.value = {
      name: '',
      source_url: currentChannel?.source_url || props.sourceUrl || '',
      frame_interval: 10,
      channel_id: currentChannel?.id || props.channelId,
      source_type: String(currentChannel?.type || props.sourceUrl || '').startsWith('rtmp') ? 'rtmp' : 'rtsp',
    }
  },
  { immediate: true }
)

watch(
  () => draft.value.channel_id,
  (channelId) => {
    const currentChannel = props.channelOptions.find((item) => item.id === channelId)
    draft.value.source_url = currentChannel?.source_url || ''
    draft.value.source_type = String(currentChannel?.type || '').toLowerCase().startsWith('rtmp') ? 'rtmp' : 'rtsp'
  }
)
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
  width: min(720px, 100%);
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
.field select {
  min-height: 40px;
  padding: 0.65rem 0.8rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  font: inherit;
}

.span-2 {
  grid-column: 1 / -1;
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

.ghost-btn {
  background: #fff;
  color: #334155;
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
