<template>
  <div class="media-nodes-page">
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>媒体节点</h2>
          <p>统一管理 ZLMediaKit 节点，查看健康状态、播放地址配置和浏览器转码推流目标。</p>
        </div>
        <button class="ghost-btn" @click="load" :disabled="loading">
          {{ loading ? '刷新中...' : '刷新' }}
        </button>
      </div>

      <div v-if="message" :class="['message', messageType]">
        {{ message }}
      </div>

      <div class="form-grid">
        <input v-model.trim="form.name" placeholder="节点名称，如 本地 ZLM" />
        <input v-model.trim="form.type" placeholder="节点类型，默认 zlm" />
        <input v-model.trim="form.api_base" placeholder="后端 API 地址，如 http://zlm-server:80" />
        <input v-model.trim="form.play_base" placeholder="前端播放地址，如 http://localhost:8080" />
        <input v-model.trim="form.webrtc_base" placeholder="前端 WebRTC 地址，如 webrtc://localhost:8080" />
        <input
          v-model.trim="form.transcode_rtmp_host"
          placeholder="浏览器转码推流主机，如 zlm-server"
        />
        <input v-model.trim="form.public_http_host" placeholder="HTTP 播放主机，如 localhost" />
        <input v-model.trim="form.public_rtsp_host" placeholder="RTSP 播放主机，如 localhost" />
        <input v-model.trim="form.public_rtmp_host" placeholder="RTMP 播放主机，如 localhost" />
        <input v-model.number="form.http_port" type="number" placeholder="HTTP 端口" />
        <input v-model.number="form.https_port" type="number" placeholder="HTTPS 端口" />
        <input v-model.number="form.rtsp_port" type="number" placeholder="RTSP 端口" />
        <input v-model.number="form.rtmp_port" type="number" placeholder="RTMP 端口" />
        <input v-model.trim="form.hls_file" placeholder="HLS 文件名，如 hls.fmp4.m3u8" />
        <input v-model.trim="form.httpflv_suffix" placeholder="HTTP-FLV 后缀，如 .live.flv" />
        <input v-model.trim="form.webrtc_path" placeholder="WebRTC 信令路径，如 /index/api/webrtc" />
        <input v-model.trim="form.secret" placeholder="ZLM secret，可留空" />
        <button class="primary-btn" @click="submit" :disabled="submitting">
          {{ submitting ? '提交中...' : '新增媒体节点' }}
        </button>
      </div>
    </section>

    <section class="panel">
      <div v-if="loading" class="empty-state">正在加载媒体节点...</div>
      <div v-else-if="items.length === 0" class="empty-state">暂无媒体节点</div>
      <div v-else class="node-list">
        <article v-for="item in items" :key="item.id" class="node-card">
          <div class="node-top">
            <div>
              <h3>{{ item.name }}</h3>
              <p class="meta-line">{{ item.id }}</p>
            </div>
            <div class="status-pills">
              <span class="pill type">{{ String(item.type || '').toUpperCase() }}</span>
              <span :class="['pill', healthStatus(item.id) === 'online' ? 'ok' : 'muted']">
                {{ healthStatusLabel(item.id, item.status) }}
              </span>
              <span v-if="item.is_default" class="pill default">默认节点</span>
            </div>
          </div>

          <div class="info-grid">
            <div><strong>后端 API</strong>{{ item.api_base || '-' }}</div>
            <div><strong>前端播放</strong>{{ item.play_base || '-' }}</div>
            <div><strong>WebRTC</strong>{{ item.webrtc_base || '-' }}</div>
            <div><strong>转码推流主机</strong>{{ item.transcode_rtmp_host || '-' }}</div>
            <div><strong>HTTP 主机</strong>{{ item.public_http_host || '-' }}:{{ item.http_port || '-' }}</div>
            <div><strong>RTSP 主机</strong>{{ item.public_rtsp_host || '-' }}:{{ item.rtsp_port || '-' }}</div>
            <div><strong>RTMP 主机</strong>{{ item.public_rtmp_host || '-' }}:{{ item.rtmp_port || '-' }}</div>
            <div><strong>HLS 文件</strong>{{ item.hls_file || '-' }}</div>
            <div><strong>WebRTC 路径</strong>{{ item.webrtc_path || '-' }}</div>
            <div><strong>创建时间</strong>{{ item.created_at || '-' }}</div>
            <div><strong>更新时间</strong>{{ item.updated_at || '-' }}</div>
          </div>

          <div v-if="healthByNode[item.id]" class="detail-box">
            <strong>健康检查</strong>
            <div class="detail-grid">
              <div><strong>状态</strong>{{ healthByNode[item.id].status }}</div>
              <div><strong>检查时间</strong>{{ healthByNode[item.id].checked_at }}</div>
              <div><strong>API 地址</strong>{{ healthByNode[item.id].api_base || '-' }}</div>
              <div><strong>播放地址</strong>{{ healthByNode[item.id].play_base || '-' }}</div>
              <div><strong>WebRTC 地址</strong>{{ healthByNode[item.id].webrtc_base || '-' }}</div>
            </div>
          </div>

          <div v-if="serverConfigByNode[item.id]" class="detail-box">
            <strong>服务配置摘要</strong>
            <div class="detail-grid">
              <div><strong>期望 HLS 文件</strong>{{ serverConfigByNode[item.id].expected?.hls_file || '-' }}</div>
              <div><strong>期望 WebRTC 路径</strong>{{ serverConfigByNode[item.id].expected?.webrtc_path || '-' }}</div>
              <div><strong>ZLM HTTP 端口</strong>{{ serverConfigByNode[item.id].zlm?.['http.port'] || '-' }}</div>
              <div><strong>ZLM HTTPS 端口</strong>{{ serverConfigByNode[item.id].zlm?.['http.sslport'] || '-' }}</div>
              <div><strong>ZLM RTMP 端口</strong>{{ serverConfigByNode[item.id].zlm?.['rtmp.port'] || '-' }}</div>
              <div><strong>ZLM RTSP 端口</strong>{{ serverConfigByNode[item.id].zlm?.['rtsp.port'] || '-' }}</div>
              <div><strong>HLS-FMP4</strong>{{ serverConfigByNode[item.id].zlm?.['protocol.enable_hls_fmp4'] || '-' }}</div>
              <div><strong>WebRTC 端口</strong>{{ serverConfigByNode[item.id].zlm?.['rtc.port'] || '-' }}</div>
            </div>
          </div>

          <div class="actions">
            <button class="ghost-btn" @click="checkHealth(item.id)" :disabled="actionLoading[item.id] === 'health'">
              {{ actionLoading[item.id] === 'health' ? '检查中...' : '健康检查' }}
            </button>
            <button
              class="ghost-btn"
              @click="loadServerConfig(item.id)"
              :disabled="actionLoading[item.id] === 'config'"
            >
              {{ actionLoading[item.id] === 'config' ? '读取中...' : '查看服务配置' }}
            </button>
            <button class="ghost-btn" @click="openEdit(item)">编辑</button>
            <button class="danger-btn" @click="remove(item)" :disabled="item.is_default">删除</button>
          </div>
        </article>
      </div>
    </section>

    <div v-if="editingNode" class="modal-mask" @click.self="closeEdit">
      <div class="modal-dialog">
        <div class="modal-header">
          <div>
            <h3>编辑媒体节点</h3>
            <p>{{ editingNode.id }}</p>
          </div>
          <button class="ghost-btn modal-close-btn" @click="closeEdit" aria-label="关闭">×</button>
        </div>

        <div class="form-grid modal-grid">
          <input v-model.trim="editForm.name" placeholder="节点名称" />
          <input v-model.trim="editForm.type" placeholder="节点类型" />
          <input v-model.trim="editForm.api_base" placeholder="后端 API 地址" />
          <input v-model.trim="editForm.play_base" placeholder="前端播放地址" />
          <input v-model.trim="editForm.webrtc_base" placeholder="前端 WebRTC 地址" />
          <input v-model.trim="editForm.transcode_rtmp_host" placeholder="浏览器转码推流主机" />
          <input v-model.trim="editForm.public_http_host" placeholder="HTTP 播放主机" />
          <input v-model.trim="editForm.public_rtsp_host" placeholder="RTSP 播放主机" />
          <input v-model.trim="editForm.public_rtmp_host" placeholder="RTMP 播放主机" />
          <input v-model.number="editForm.http_port" type="number" placeholder="HTTP 端口" />
          <input v-model.number="editForm.https_port" type="number" placeholder="HTTPS 端口" />
          <input v-model.number="editForm.rtsp_port" type="number" placeholder="RTSP 端口" />
          <input v-model.number="editForm.rtmp_port" type="number" placeholder="RTMP 端口" />
          <input v-model.trim="editForm.hls_file" placeholder="HLS 文件名" />
          <input v-model.trim="editForm.httpflv_suffix" placeholder="HTTP-FLV 后缀" />
          <input v-model.trim="editForm.webrtc_path" placeholder="WebRTC 信令路径" />
          <input v-model.trim="editForm.secret" placeholder="ZLM secret" />
        </div>

        <div class="modal-actions">
          <button class="ghost-btn" @click="closeEdit">取消</button>
          <button class="primary-btn" @click="saveEdit" :disabled="savingEdit">
            {{ savingEdit ? '保存中...' : '保存修改' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  createMediaNode,
  deleteMediaNode,
  getMediaNodeHealth,
  getMediaNodeServerConfig,
  listMediaNodes,
  updateMediaNode,
  type MediaNodeItem,
  type MediaNodePayload,
} from '@/api/mediaNodes'

type NodeHealth = {
  id: string
  name: string
  status: string
  checked_at: string
  api_base?: string
  play_base?: string
  webrtc_base?: string
  transcode_rtmp_host?: string
}

type NodeServerConfig = {
  node: MediaNodeItem
  zlm?: Record<string, any>
  expected?: Record<string, any>
}

const loading = ref(false)
const submitting = ref(false)
const savingEdit = ref(false)
const items = ref<MediaNodeItem[]>([])
const message = ref('')
const messageType = ref<'success' | 'error'>('success')
const editingNode = ref<MediaNodeItem | null>(null)
const healthByNode = ref<Record<string, NodeHealth>>({})
const serverConfigByNode = ref<Record<string, NodeServerConfig>>({})
const actionLoading = ref<Record<string, 'health' | 'config' | undefined>>({})

const defaultForm = (): MediaNodePayload => ({
  name: '',
  type: 'zlm',
  api_base: 'http://zlm-server:80',
  play_base: 'http://localhost:8080',
  webrtc_base: 'webrtc://localhost:8080',
  transcode_rtmp_host: 'zlm-server',
  public_http_host: 'localhost',
  public_rtsp_host: 'localhost',
  public_rtmp_host: 'localhost',
  http_port: 8080,
  https_port: 8443,
  rtsp_port: 8554,
  rtmp_port: 1935,
  hls_file: 'hls.fmp4.m3u8',
  httpflv_suffix: '.live.flv',
  webrtc_path: '/index/api/webrtc',
  secret: '',
})

const form = ref<MediaNodePayload>(defaultForm())
const editForm = ref<Partial<MediaNodePayload>>({})

const showMessage = (type: 'success' | 'error', text: string) => {
  messageType.value = type
  message.value = text
}

const load = async () => {
  loading.value = true
  try {
    const result = await listMediaNodes()
    items.value = result.items || []
  } finally {
    loading.value = false
  }
}

const submit = async () => {
  if (!form.value.name || !form.value.api_base || !form.value.play_base) {
    showMessage('error', '请先填写节点名称、后端 API 地址和前端播放地址')
    return
  }
  submitting.value = true
  message.value = ''
  try {
    await createMediaNode(form.value)
    form.value = defaultForm()
    await load()
    showMessage('success', '媒体节点新增成功')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '媒体节点新增失败')
  } finally {
    submitting.value = false
  }
}

const openEdit = (item: MediaNodeItem) => {
  editingNode.value = item
  editForm.value = {
    name: item.name,
    type: item.type,
    api_base: item.api_base,
    play_base: item.play_base,
    webrtc_base: item.webrtc_base,
    transcode_rtmp_host: item.transcode_rtmp_host,
    public_http_host: item.public_http_host,
    public_rtsp_host: item.public_rtsp_host,
    public_rtmp_host: item.public_rtmp_host,
    http_port: item.http_port,
    https_port: item.https_port,
    rtsp_port: item.rtsp_port,
    rtmp_port: item.rtmp_port,
    hls_file: item.hls_file,
    httpflv_suffix: item.httpflv_suffix,
    webrtc_path: item.webrtc_path,
    secret: item.secret,
  }
}

const closeEdit = () => {
  editingNode.value = null
  editForm.value = {}
}

const saveEdit = async () => {
  if (!editingNode.value) return
  savingEdit.value = true
  try {
    await updateMediaNode(editingNode.value.id, editForm.value)
    closeEdit()
    await load()
    showMessage('success', '媒体节点更新成功')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '媒体节点更新失败')
  } finally {
    savingEdit.value = false
  }
}

const remove = async (item: MediaNodeItem) => {
  if (!confirm(`确认删除媒体节点 ${item.name} 吗？`)) return
  try {
    await deleteMediaNode(item.id)
    delete healthByNode.value[item.id]
    delete serverConfigByNode.value[item.id]
    await load()
    showMessage('success', '媒体节点已删除')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '媒体节点删除失败')
  }
}

const checkHealth = async (nodeId: string) => {
  actionLoading.value[nodeId] = 'health'
  try {
    const result = await getMediaNodeHealth(nodeId)
    healthByNode.value[nodeId] = result
    showMessage('success', '健康检查完成')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '健康检查失败')
  } finally {
    actionLoading.value[nodeId] = undefined
  }
}

const loadServerConfig = async (nodeId: string) => {
  actionLoading.value[nodeId] = 'config'
  try {
    const result = await getMediaNodeServerConfig(nodeId)
    serverConfigByNode.value[nodeId] = result
    showMessage('success', '服务配置读取成功')
  } catch (error: any) {
    showMessage('error', error?.response?.data?.detail || error?.message || '服务配置读取失败')
  } finally {
    actionLoading.value[nodeId] = undefined
  }
}

const healthStatus = (nodeId: string) => healthByNode.value[nodeId]?.status || 'unknown'

const healthStatusLabel = (nodeId: string, fallback?: string) => {
  const status = healthByNode.value[nodeId]?.status || fallback || 'unknown'
  if (status === 'online') return '在线'
  if (status === 'offline') return '离线'
  return '未检查'
}

onMounted(load)
</script>

<style scoped>
.media-nodes-page {
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

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
  margin-bottom: 1rem;
}

.panel-header h2,
.node-top h3,
.modal-header h3 {
  margin: 0;
}

.panel-header p,
.meta-line,
.modal-header p {
  margin: 0.35rem 0 0;
  color: #64748b;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.75rem;
}

.form-grid input,
.form-grid button {
  min-height: 42px;
  padding: 0.75rem 0.9rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  font: inherit;
}

.node-list {
  display: grid;
  gap: 1rem;
}

.node-card {
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 1rem;
  display: grid;
  gap: 0.9rem;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
}

.node-top {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
}

.status-pills {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
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

.pill.muted {
  background: #e2e8f0;
  color: #475569;
}

.pill.default {
  background: #fef3c7;
  color: #92400e;
}

.info-grid,
.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 0.45rem;
  color: #334155;
  word-break: break-all;
}

.detail-box {
  display: grid;
  gap: 0.65rem;
  padding: 0.9rem;
  border: 1px solid #dbe4ee;
  border-radius: 12px;
  background: #f8fafc;
}

.actions,
.modal-actions {
  display: flex;
  gap: 0.65rem;
  flex-wrap: wrap;
}

.primary-btn,
.ghost-btn,
.danger-btn {
  min-height: 42px;
  padding: 0.75rem 1rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  font: inherit;
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

.danger-btn {
  background: #b91c1c;
  color: #fff;
  border-color: #b91c1c;
}

.empty-state,
.message {
  border-radius: 10px;
  padding: 0.8rem 1rem;
}

.empty-state {
  background: #f8fafc;
  color: #475569;
  border: 1px solid #e2e8f0;
}

.message.success {
  background: #ecfdf3;
  color: #027a48;
  border: 1px solid #a6f4c5;
}

.message.error {
  background: #fef3f2;
  color: #b42318;
  border: 1px solid #fecdca;
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
  width: min(980px, 100%);
  background: #fff;
  border-radius: 16px;
  padding: 1rem;
  display: grid;
  gap: 1rem;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
}

@media (max-width: 768px) {
  .panel-header,
  .node-top,
  .modal-header {
    flex-direction: column;
  }
}
</style>
