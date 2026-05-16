<template>
  <div class="dashboard">
    <div class="card">
      <h1>欢迎使用 YOLO 训练平台</h1>
      <p>支持模型训练、推理，以及通道、事件、录像等视频 AI 能力管理。</p>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <h2>数据集管理</h2>
        <p>上传、准备和管理训练数据集</p>
        <router-link to="/datasets">
          <button>进入数据集</button>
        </router-link>
      </div>

      <div class="card">
        <h2>数据标注</h2>
        <p>使用 Canvas 进行 BBox 标注</p>
        <router-link to="/annotate">
          <button>开始标注</button>
        </router-link>
      </div>

      <div class="card">
        <h2>模型训练</h2>
        <p>配置训练参数并启动训练任务</p>
        <router-link to="/train">
          <button>开始训练</button>
        </router-link>
      </div>

      <div class="card">
        <h2>模型管理</h2>
        <p>上传、查看和管理训练好的模型</p>
        <router-link to="/models">
          <button>进入模型管理</button>
        </router-link>
      </div>

      <div class="card">
        <h2>模型推理</h2>
        <p>使用训练好的模型进行推理验证</p>
        <router-link to="/infer">
          <button>开始推理</button>
        </router-link>
      </div>

      <div class="card">
        <h2>通道管理</h2>
        <p>管理视频通道、AI 模型与区域配置</p>
        <router-link to="/channels">
          <button>进入通道</button>
        </router-link>
      </div>

      <div class="card">
        <h2>事件中心</h2>
        <p>查看 AI 检测事件与抓拍告警信息</p>
        <router-link to="/events">
          <button>查看事件</button>
        </router-link>
      </div>

      <div class="card">
        <h2>录像中心</h2>
        <p>查看录像记录、时长统计和时间分布</p>
        <router-link to="/recordings">
          <button>查看录像</button>
        </router-link>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
</script>

<style scoped>
.dashboard h1 {
  color: #2c3e50;
  margin-bottom: 0.5rem;
}

.dashboard p {
  color: #7f8c8d;
  margin-bottom: 1rem;
}

.card h2 {
  font-size: 1.5rem;
  margin-bottom: 0.5rem;
}

.card button {
  margin-top: 1rem;
  min-height: 44px;
  padding: 0.7rem 1.15rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
}
</style>
