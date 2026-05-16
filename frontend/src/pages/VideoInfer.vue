<template>
  <div class="video-infer">
    <div class="card">
      <h2>视频推理</h2>
      
      <!-- 模型选择 -->
      <div class="form-group">
        <label>选择模型</label>
        <div class="select-with-refresh">
          <select v-model="selectedModel" :disabled="processing || loadingModels">
            <option value="">-- 请选择模型 --</option>
            <option v-for="model in models" :key="model.model_id" :value="model.model_id">
              {{ model.name || model.model_id }}{{ model.name ? ` (${model.model_id})` : '' }} ({{ model.classes?.length || 0 }} 类)
            </option>
          </select>
          <button type="button" @click="loadModels" class="refresh-btn" :disabled="loadingModels">
            <span v-if="loadingModels" class="loading-spinner"></span>
            {{ loadingModels ? '加载中...' : '刷新' }}
          </button>
        </div>
      </div>
      
      <!-- 置信度阈值 -->
      <div class="form-group">
        <label>置信度阈值: {{ confThreshold.toFixed(2) }}</label>
        <input 
          type="range" 
          v-model.number="confThreshold" 
          min="0.1" 
          max="0.9" 
          step="0.05"
          :disabled="processing"
        />
      </div>
      
      <!-- 视频上传（非摄像头模式显示） -->
      <div v-if="processMode !== 'camera'" class="form-group">
        <label>上传视频</label>
        <input 
          type="file" 
          accept="video/*" 
          @change="handleVideoSelect" 
          :disabled="processing"
        />
        <small>支持 MP4、AVI、MOV 等常见视频格式</small>
      </div>
      
      <!-- 视频预览（非摄像头模式显示） -->
      <div v-if="videoPreview && processMode !== 'camera'" class="video-preview">
        <h4>原始视频预览</h4>
        <video 
          :src="videoPreview" 
          controls 
          class="preview-video"
        ></video>
      </div>
      
      <!-- 处理模式选择 -->
      <div class="form-group">
        <label>处理模式</label>
        <div class="mode-select">
          <label class="radio-item">
            <input type="radio" v-model="processMode" value="full" :disabled="processing || isCameraActive" />
            <span>完整处理</span>
            <small>处理完整视频后返回结果（适合短视频）</small>
          </label>
          <label class="radio-item">
            <input type="radio" v-model="processMode" value="stream" :disabled="processing || isCameraActive" />
            <span>流式处理</span>
            <small>实时显示每帧处理结果（适合实时查看）</small>
          </label>
          <label class="radio-item camera-mode">
            <input type="radio" v-model="processMode" value="camera" :disabled="processing || isCameraActive" />
            <span>摄像头实时检测</span>
            <small>使用摄像头进行实时目标检测</small>
          </label>
        </div>
      </div>
      
      <!-- 开始处理按钮（非摄像头模式） -->
      <button 
        v-if="processMode !== 'camera'"
        @click="startProcessing" 
        :disabled="!selectedModel || !selectedVideo || processing"
      >
        <span v-if="processing" class="loading-spinner"></span>
        {{ processing ? '处理中...' : '开始推理' }}
      </button>
      
      <!-- 停止按钮（非摄像头模式） -->
      <button 
        v-if="processing && processMode === 'stream'"
        @click="stopProcessing" 
        class="danger"
        style="margin-left: 0.5rem;"
      >
        停止处理
      </button>
      
      <!-- 摄像头控制按钮 -->
      <div v-if="processMode === 'camera'" class="camera-controls">
        <button 
          v-if="!isCameraActive"
          @click="startCamera" 
          :disabled="!selectedModel"
          class="camera-btn start"
        >
          启动摄像头
        </button>
        <button 
          v-else
          @click="stopCamera" 
          class="camera-btn stop danger"
        >
          停止摄像头
        </button>
      </div>
      
      <!-- 摄像头错误提示 -->
      <div v-if="cameraError" class="camera-error">
        {{ cameraError }}
      </div>
    </div>
    
    <!-- 摄像头实时检测区域 -->
    <div v-if="processMode === 'camera'" class="card camera-section">
      <h2>摄像头实时检测</h2>
      
      <!-- 隐藏的video元素（用于获取摄像头流） -->
      <video 
        ref="cameraVideoRef" 
        autoplay 
        playsinline 
        muted
        class="hidden-video"
      ></video>
      
      <!-- 隐藏的canvas（用于捕获帧） -->
      <canvas ref="cameraCanvasRef" class="hidden-canvas"></canvas>
      
      <!-- 检测结果canvas -->
      <div class="camera-preview-container">
        <canvas 
          ref="detectionCanvasRef" 
          class="detection-canvas"
          :class="{ active: isCameraActive }"
        ></canvas>
        
        <div v-if="!isCameraActive && !cameraError" class="camera-placeholder">
          <div class="placeholder-icon">📷</div>
          <p>点击"启动摄像头"开始实时检测</p>
        </div>
      </div>
      
      <!-- 摄像头统计信息 -->
      <div v-if="isCameraActive" class="camera-stats">
        <div class="stat-item">
          <span class="stat-label">FPS</span>
          <span class="stat-value">{{ cameraStats.fps }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">已处理帧</span>
          <span class="stat-value">{{ cameraStats.framesProcessed }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">总检测数</span>
          <span class="stat-value">{{ cameraStats.totalDetections }}</span>
        </div>
      </div>
      
      <!-- 当前检测列表 -->
      <div v-if="isCameraActive && cameraDetections.length > 0" class="camera-detection-list">
        <h4>当前检测 ({{ cameraDetections.length }})</h4>
        <div class="detection-items">
          <div v-for="(det, idx) in cameraDetections" :key="idx" class="detection-item">
            <span class="class-name" :style="{ color: getClassColor(det.class_id) }">
              {{ det.class_name }}
            </span>
            <span class="conf">{{ (det.conf * 100).toFixed(1) }}%</span>
          </div>
        </div>
      </div>
      
      <div v-if="isCameraActive && cameraDetections.length === 0" class="no-detections">
        暂无检测到的目标
      </div>
    </div>
    
    <!-- 处理进度（非摄像头模式） -->
    <div v-if="processMode !== 'camera' && (processing || processComplete)" class="card">
      <h2>处理状态</h2>
      
      <!-- 进度条 -->
      <div v-if="videoInfo" class="progress-section">
        <div class="progress-info">
          <span>帧: {{ currentFrame }} / {{ videoInfo.total_frames }}</span>
          <span>{{ progressPercent.toFixed(1) }}%</span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
        </div>
      </div>
      
      <!-- 统计信息 -->
      <div v-if="stats" class="stats-section">
        <div class="stat-item">
          <span class="stat-label">总检测数</span>
          <span class="stat-value">{{ stats.totalDetections }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">有检测的帧数</span>
          <span class="stat-value">{{ stats.framesWithDetections }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">处理帧数</span>
          <span class="stat-value">{{ currentFrame }}</span>
        </div>
      </div>
    </div>
    
    <!-- 流式处理结果展示（仅流式模式） -->
    <div v-if="processMode === 'stream' && !isCameraActive && (processing || processComplete)" class="card">
      <h2>实时检测结果</h2>
      <div class="stream-result">
        <canvas ref="canvasRef" class="result-canvas"></canvas>
        <div v-if="currentDetections.length > 0" class="detection-list">
          <h4>当前帧检测 ({{ currentDetections.length }})</h4>
          <div v-for="(det, idx) in currentDetections" :key="idx" class="detection-item">
            <span class="class-name" :style="{ color: getClassColor(det.class_id) }">
              {{ det.class_name }}
            </span>
            <span class="conf">{{ (det.conf * 100).toFixed(1) }}%</span>
          </div>
        </div>
      </div>
    </div>
    
    <!-- 完整处理结果 -->
    <div v-if="processMode === 'full' && processComplete && resultVideoUrl" class="card">
      <h2>处理结果</h2>
      <div class="result-section">
        <video 
          :src="resultVideoUrl" 
          controls 
          class="result-video"
        ></video>
        <div class="result-actions">
          <a :href="resultVideoUrl" download="result.mp4" class="download-btn">
            下载处理后的视频
          </a>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onUnmounted } from 'vue'
import { listModels } from '@/api/models'
import { videoInference, videoInferenceStream, inference, type VideoFrameData } from '@/api/infer'

const models = ref<any[]>([])
const loadingModels = ref(false)
const selectedModel = ref('')
const selectedVideo = ref<File | null>(null)
const videoPreview = ref<string | null>(null)
const confThreshold = ref(0.25)
const processMode = ref<'full' | 'stream' | 'camera'>('stream')

const processing = ref(false)
const processComplete = ref(false)
const currentFrame = ref(0)
const videoInfo = ref<{fps: number, width: number, height: number, total_frames: number} | null>(null)
const stats = ref<{totalDetections: number, framesWithDetections: number} | null>(null)
const currentDetections = ref<{class_id: number, class_name: string, conf: number, bbox: number[]}[]>([])
const resultVideoUrl = ref<string | null>(null)

const canvasRef = ref<HTMLCanvasElement | null>(null)

// 摄像头相关状态
const cameraVideoRef = ref<HTMLVideoElement | null>(null)
const cameraCanvasRef = ref<HTMLCanvasElement | null>(null)
const detectionCanvasRef = ref<HTMLCanvasElement | null>(null)
const isCameraActive = ref(false)
const cameraError = ref<string | null>(null)
const cameraStats = ref<{totalDetections: number, framesProcessed: number, fps: number}>({ 
  totalDetections: 0, 
  framesProcessed: 0,
  fps: 0 
})
const cameraDetections = ref<{class_id: number, class_name: string, conf: number, x1: number, y1: number, x2: number, y2: number}[]>([])

let mediaStream: MediaStream | null = null
let cameraAnimationId: number | null = null
let lastFrameTime = 0
let frameCount = 0
let fpsUpdateTime = 0

let stopRequested = false

const progressPercent = computed(() => {
  if (!videoInfo.value || videoInfo.value.total_frames === 0) return 0
  return (currentFrame.value / videoInfo.value.total_frames) * 100
})

const loadModels = async () => {
  loadingModels.value = true
  try {
    const result = await listModels()
    models.value = result.models
  } catch (error: any) {
    console.error('加载模型失败:', error)
    alert('加载模型失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    loadingModels.value = false
  }
}

const handleVideoSelect = (event: Event) => {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (file) {
    selectedVideo.value = file
    videoPreview.value = URL.createObjectURL(file)
    // 重置状态
    processComplete.value = false
    resultVideoUrl.value = null
    currentFrame.value = 0
    videoInfo.value = null
    stats.value = null
  }
}

const startProcessing = async () => {
  if (!selectedModel.value || !selectedVideo.value) return
  
  processing.value = true
  processComplete.value = false
  stopRequested = false
  currentFrame.value = 0
  stats.value = { totalDetections: 0, framesWithDetections: 0 }
  currentDetections.value = []
  resultVideoUrl.value = null
  
  try {
    if (processMode.value === 'full') {
      // 完整处理模式
      const result = await videoInference(selectedModel.value, selectedVideo.value, confThreshold.value)
      
      if (result.error) {
        alert('处理失败: ' + result.error)
        return
      }
      
      videoInfo.value = result.video_info
      currentFrame.value = result.video_info.processed_frames
      stats.value = {
        totalDetections: result.summary.total_detections,
        framesWithDetections: result.summary.frames_with_detections
      }
      
      // 将base64视频转为URL
      const videoBlob = base64ToBlob(result.video_data, 'video/mp4')
      resultVideoUrl.value = URL.createObjectURL(videoBlob)
      
    } else {
      // 流式处理模式
      await videoInferenceStream(
        selectedModel.value,
        selectedVideo.value,
        confThreshold.value,
        (data: VideoFrameData) => {
          if (stopRequested) return
          
          if (data.type === 'info') {
            videoInfo.value = {
              fps: data.fps!,
              width: data.width!,
              height: data.height!,
              total_frames: data.total_frames!
            }
            // 初始化canvas尺寸
            if (canvasRef.value) {
              canvasRef.value.width = data.width!
              canvasRef.value.height = data.height!
            }
          } else if (data.type === 'frame') {
            currentFrame.value = data.frame_number!
            currentDetections.value = data.detections || []
            
            // 更新统计
            if (stats.value) {
              stats.value.totalDetections += currentDetections.value.length
              if (currentDetections.value.length > 0) {
                stats.value.framesWithDetections++
              }
            }
            
            // 绘制帧到canvas
            if (canvasRef.value && data.frame_data) {
              const ctx = canvasRef.value.getContext('2d')
              if (ctx) {
                const img = new Image()
                img.onload = () => {
                  ctx.drawImage(img, 0, 0)
                }
                img.src = 'data:image/jpeg;base64,' + data.frame_data
              }
            }
          } else if (data.type === 'complete') {
            currentFrame.value = data.processed_frames!
          }
        },
        (error) => {
          console.error('流式处理错误:', error)
          alert('处理失败: ' + error.message)
        },
        () => {
          console.log('流式处理完成')
        }
      )
    }
    
    processComplete.value = true
    
  } catch (error: any) {
    console.error('处理失败:', error)
    alert('处理失败: ' + (error.message || '未知错误'))
  } finally {
    processing.value = false
  }
}

const stopProcessing = () => {
  stopRequested = true
  processing.value = false
}

const base64ToBlob = (base64: string, mimeType: string): Blob => {
  const byteCharacters = atob(base64)
  const byteNumbers = new Array(byteCharacters.length)
  for (let i = 0; i < byteCharacters.length; i++) {
    byteNumbers[i] = byteCharacters.charCodeAt(i)
  }
  const byteArray = new Uint8Array(byteNumbers)
  return new Blob([byteArray], { type: mimeType })
}

const getClassColor = (classId: number): string => {
  const colors = [
    '#00ff00', '#ff0000', '#0000ff', '#ffff00', '#ff00ff',
    '#00ffff', '#ff8000', '#8000ff', '#0080ff', '#ff0080'
  ]
  return colors[classId % colors.length]
}

// ============ 摄像头相关函数 ============

// 启动摄像头
const startCamera = async () => {
  if (!selectedModel.value) {
    alert('请先选择模型')
    return
  }
  
  cameraError.value = null
  cameraStats.value = { totalDetections: 0, framesProcessed: 0, fps: 0 }
  cameraDetections.value = []
  
  try {
    // 请求摄像头权限
    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: {
        width: { ideal: 640 },
        height: { ideal: 480 },
        facingMode: 'environment' // 优先使用后置摄像头
      },
      audio: false
    })
    
    // 绑定视频流到video元素
    if (cameraVideoRef.value) {
      cameraVideoRef.value.srcObject = mediaStream
      await cameraVideoRef.value.play()
      
      // 初始化canvas尺寸
      const videoWidth = cameraVideoRef.value.videoWidth
      const videoHeight = cameraVideoRef.value.videoHeight
      
      if (cameraCanvasRef.value) {
        cameraCanvasRef.value.width = videoWidth
        cameraCanvasRef.value.height = videoHeight
      }
      if (detectionCanvasRef.value) {
        detectionCanvasRef.value.width = videoWidth
        detectionCanvasRef.value.height = videoHeight
      }
      
      isCameraActive.value = true
      lastFrameTime = performance.now()
      fpsUpdateTime = lastFrameTime
      frameCount = 0
      
      // 开始帧捕获循环
      captureLoop()
    }
  } catch (error: any) {
    console.error('启动摄像头失败:', error)
    if (error.name === 'NotAllowedError') {
      cameraError.value = '摄像头权限被拒绝，请允许访问摄像头'
    } else if (error.name === 'NotFoundError') {
      cameraError.value = '未检测到摄像头设备'
    } else if (error.name === 'NotReadableError') {
      cameraError.value = '摄像头被其他应用占用'
    } else {
      cameraError.value = '启动摄像头失败: ' + error.message
    }
  }
}

// 停止摄像头
const stopCamera = () => {
  isCameraActive.value = false
  
  // 停止动画循环
  if (cameraAnimationId !== null) {
    cancelAnimationFrame(cameraAnimationId)
    cameraAnimationId = null
  }
  
  // 停止所有视频轨道
  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop())
    mediaStream = null
  }
  
  // 清理video元素
  if (cameraVideoRef.value) {
    cameraVideoRef.value.srcObject = null
  }
  
  // 清空检测canvas
  if (detectionCanvasRef.value) {
    const ctx = detectionCanvasRef.value.getContext('2d')
    if (ctx) {
      ctx.clearRect(0, 0, detectionCanvasRef.value.width, detectionCanvasRef.value.height)
    }
  }
}

// 帧捕获循环
let isProcessingFrame = false
const captureLoop = () => {
  if (!isCameraActive.value) return
  
  // 如果上一帧还在处理中，跳过这一帧
  if (!isProcessingFrame) {
    captureAndProcessFrame()
  }
  
  cameraAnimationId = requestAnimationFrame(captureLoop)
}

// 捕获帧并进行推理
const captureAndProcessFrame = async () => {
  if (!cameraVideoRef.value || !cameraCanvasRef.value || !detectionCanvasRef.value) return
  if (!isCameraActive.value) return
  
  isProcessingFrame = true
  
  try {
    const video = cameraVideoRef.value
    const captureCanvas = cameraCanvasRef.value
    const captureCtx = captureCanvas.getContext('2d')
    
    if (!captureCtx) return
    
    // 从video捕获帧到canvas
    captureCtx.drawImage(video, 0, 0, captureCanvas.width, captureCanvas.height)
    
    // 将canvas转为Blob
    const blob = await new Promise<Blob | null>((resolve) => {
      captureCanvas.toBlob(resolve, 'image/jpeg', 0.8)
    })
    
    if (!blob) return
    
    // 创建File对象
    const file = new File([blob], 'frame.jpg', { type: 'image/jpeg' })
    
    // 调用推理API
    const result = await inference(selectedModel.value, file)
    
    if (result.error) {
      console.error('推理错误:', result.error)
      return
    }
    
    // 更新检测结果
    cameraDetections.value = result.detections || []
    
    // 更新统计
    cameraStats.value.framesProcessed++
    cameraStats.value.totalDetections += cameraDetections.value.length
    
    // 计算FPS
    frameCount++
    const now = performance.now()
    if (now - fpsUpdateTime >= 1000) {
      cameraStats.value.fps = Math.round(frameCount * 1000 / (now - fpsUpdateTime))
      frameCount = 0
      fpsUpdateTime = now
    }
    
    // 绘制检测结果
    drawDetections(result.detections, result.image_width, result.image_height)
    
  } catch (error: any) {
    console.error('帧处理错误:', error)
  } finally {
    isProcessingFrame = false
  }
}

// 绘制检测结果到canvas
const drawDetections = (
  detections: {class_id: number, class_name: string, conf: number, x1: number, y1: number, x2: number, y2: number}[],
  imageWidth: number,
  imageHeight: number
) => {
  if (!detectionCanvasRef.value || !cameraVideoRef.value) return
  
  const canvas = detectionCanvasRef.value
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  
  // 清空canvas
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  
  // 绘制视频帧
  ctx.drawImage(cameraVideoRef.value, 0, 0, canvas.width, canvas.height)
  
  // 计算缩放比例
  const scaleX = canvas.width / imageWidth
  const scaleY = canvas.height / imageHeight
  
  // 绘制检测框
  detections.forEach((det) => {
    const x1 = det.x1 * scaleX
    const y1 = det.y1 * scaleY
    const x2 = det.x2 * scaleX
    const y2 = det.y2 * scaleY
    const width = x2 - x1
    const height = y2 - y1
    
    const color = getClassColor(det.class_id)
    
    // 绘制边界框
    ctx.strokeStyle = color
    ctx.lineWidth = 3
    ctx.strokeRect(x1, y1, width, height)
    
    // 绘制标签背景
    const label = `${det.class_name}: ${(det.conf * 100).toFixed(1)}%`
    ctx.font = 'bold 14px Arial'
    const textMetrics = ctx.measureText(label)
    const textHeight = 18
    const padding = 4
    
    ctx.fillStyle = color
    ctx.fillRect(x1, y1 - textHeight - padding, textMetrics.width + padding * 2, textHeight + padding)
    
    // 绘制标签文字
    ctx.fillStyle = '#ffffff'
    ctx.fillText(label, x1 + padding, y1 - padding - 2)
  })
}

// 初始化加载模型
loadModels()

// 清理
onUnmounted(() => {
  // 清理视频文件URL
  if (videoPreview.value) {
    URL.revokeObjectURL(videoPreview.value)
  }
  if (resultVideoUrl.value) {
    URL.revokeObjectURL(resultVideoUrl.value)
  }
  
  // 清理摄像头资源
  if (isCameraActive.value) {
    stopCamera()
  }
  
  // 确保动画循环已停止
  if (cameraAnimationId !== null) {
    cancelAnimationFrame(cameraAnimationId)
    cameraAnimationId = null
  }
  
  // 确保媒体流已停止
  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop())
    mediaStream = null
  }
})
</script>

<style scoped>
.video-infer {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.select-with-refresh {
  display: flex;
  gap: 0.5rem;
}

.select-with-refresh select {
  flex: 1;
}

.refresh-btn {
  padding: 0.5rem 1rem;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}

.video-preview {
  margin-top: 1rem;
}

.preview-video,
.result-video {
  width: 100%;
  max-width: 800px;
  border-radius: 4px;
  background: #000;
}

.mode-select {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.radio-item {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.75rem;
  border: 1px solid #ddd;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.radio-item:hover {
  border-color: #3498db;
}

.radio-item input[type="radio"] {
  margin-top: 0.25rem;
}

.radio-item span {
  font-weight: 500;
}

.radio-item small {
  display: block;
  color: #7f8c8d;
  margin-top: 0.25rem;
}

.progress-section {
  margin-bottom: 1rem;
}

.progress-info {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.5rem;
  font-size: 0.875rem;
  color: #7f8c8d;
}

.progress-bar {
  height: 8px;
  background: #ecf0f1;
  border-radius: 4px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #3498db, #2ecc71);
  transition: width 0.3s;
}

.stats-section {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1rem;
}

.stat-item {
  padding: 1rem;
  background: #f8f9fa;
  border-radius: 4px;
  text-align: center;
}

.stat-label {
  display: block;
  font-size: 0.875rem;
  color: #7f8c8d;
  margin-bottom: 0.25rem;
}

.stat-value {
  display: block;
  font-size: 1.5rem;
  font-weight: 600;
  color: #2c3e50;
}

.stream-result {
  display: grid;
  grid-template-columns: 1fr 250px;
  gap: 1rem;
}

.result-canvas {
  width: 100%;
  height: auto;
  background: #000;
  border-radius: 4px;
}

.detection-list {
  max-height: 400px;
  overflow-y: auto;
}

.detection-list h4 {
  margin-bottom: 0.5rem;
  color: #2c3e50;
}

.detection-item {
  display: flex;
  justify-content: space-between;
  padding: 0.5rem;
  border-bottom: 1px solid #eee;
}

.class-name {
  font-weight: 500;
}

.conf {
  color: #7f8c8d;
}

.result-section {
  text-align: center;
}

.result-actions {
  margin-top: 1rem;
}

.download-btn {
  display: inline-block;
  padding: 0.75rem 1.5rem;
  background: #3498db;
  color: white;
  text-decoration: none;
  border-radius: 4px;
  transition: background 0.2s;
}

.download-btn:hover {
  background: #2980b9;
}

@media (max-width: 768px) {
  .stream-result {
    grid-template-columns: 1fr;
  }
  
  .detection-list {
    max-height: 200px;
  }
}

/* 摄像头相关样式 */
.camera-mode {
  border-color: #9b59b6;
}

.camera-mode:hover {
  border-color: #8e44ad;
}

.camera-controls {
  margin-top: 1rem;
}

.camera-btn {
  padding: 0.75rem 1.5rem;
  font-size: 1rem;
  border-radius: 4px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}

.camera-btn.start {
  background: linear-gradient(135deg, #9b59b6, #8e44ad);
  color: white;
  border: none;
}

.camera-btn.start:hover:not(:disabled) {
  background: linear-gradient(135deg, #8e44ad, #7d3c98);
}

.camera-btn.start:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.camera-btn.stop {
  background: #e74c3c;
  color: white;
  border: none;
}

.camera-btn.stop:hover {
  background: #c0392b;
}

.camera-error {
  margin-top: 1rem;
  padding: 0.75rem 1rem;
  background: #fdf2f2;
  border: 1px solid #f5c6cb;
  border-radius: 4px;
  color: #721c24;
}

.camera-section {
  background: linear-gradient(135deg, #f8f9fa, #fff);
}

.hidden-video,
.hidden-canvas {
  display: none;
}

.camera-preview-container {
  position: relative;
  width: 100%;
  max-width: 800px;
  margin: 1rem auto;
  background: #1e1e1e;
  border-radius: 8px;
  overflow: hidden;
  aspect-ratio: 4/3;
}

.detection-canvas {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.detection-canvas:not(.active) {
  display: none;
}

.camera-placeholder {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #7f8c8d;
}

.placeholder-icon {
  font-size: 4rem;
  margin-bottom: 1rem;
}

.camera-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  margin-top: 1rem;
}

.camera-stats .stat-item {
  padding: 0.75rem;
  background: linear-gradient(135deg, #9b59b6, #8e44ad);
  color: white;
  border-radius: 4px;
  text-align: center;
}

.camera-stats .stat-label {
  display: block;
  font-size: 0.75rem;
  opacity: 0.9;
  margin-bottom: 0.25rem;
}

.camera-stats .stat-value {
  display: block;
  font-size: 1.5rem;
  font-weight: 600;
}

.camera-detection-list {
  margin-top: 1rem;
  padding: 1rem;
  background: #f8f9fa;
  border-radius: 4px;
}

.camera-detection-list h4 {
  margin-bottom: 0.75rem;
  color: #2c3e50;
}

.detection-items {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 0.5rem;
}

.camera-detection-list .detection-item {
  display: flex;
  justify-content: space-between;
  padding: 0.5rem 0.75rem;
  background: white;
  border-radius: 4px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.no-detections {
  margin-top: 1rem;
  padding: 1rem;
  text-align: center;
  color: #7f8c8d;
  background: #f8f9fa;
  border-radius: 4px;
}

@media (max-width: 768px) {
  .camera-stats {
    grid-template-columns: repeat(3, 1fr);
    gap: 0.5rem;
  }
  
  .camera-stats .stat-item {
    padding: 0.5rem;
  }
  
  .camera-stats .stat-value {
    font-size: 1.25rem;
  }
  
  .detection-items {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
