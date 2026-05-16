<template>
  <div id="app">
    <nav class="navbar">
      <div class="nav-brand">YOLO 训练平台</div>
      <div class="nav-links">
        <router-link to="/">首页</router-link>
        <router-link to="/datasets">数据集</router-link>
        <router-link to="/annotate">标注</router-link>
        <router-link to="/train">训练</router-link>
        <router-link to="/models">模型管理</router-link>
        <router-link to="/infer">推理</router-link>
        <router-link to="/channels">通道</router-link>
        <router-link to="/events">事件</router-link>
        <router-link to="/recordings">录像</router-link>
      </div>
    </nav>
    <main class="main-content">
      <router-view v-slot="{ Component, route }">
        <keep-alive :include="['Channels', 'Events']">
          <component :is="Component" :key="route.name" />
        </keep-alive>
      </router-view>
    </main>
  </div>
</template>

<script setup lang="ts">
</script>

<style scoped>
.navbar {
  background: #2c3e50;
  color: white;
  padding: 1rem 2rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.nav-brand {
  font-size: 1.5rem;
  font-weight: bold;
}

.nav-links {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
}

.nav-links a {
  color: white;
  text-decoration: none;
  padding: 0.5rem 0.75rem;
  border-radius: 4px;
  transition: background 0.3s;
}

.nav-links a:hover,
.nav-links a.router-link-active {
  background: #34495e;
}

.main-content {
  padding: 2rem;
  max-width: 1400px;
  margin: 0 auto;
}
</style>
