<template>
  <div id="app">
    <nav class="navbar">
      <div class="nav-brand">AI视频分析平台</div>
      <div class="nav-links">
        <template v-for="item in menuItems" :key="item.key">
          <router-link v-if="menuVisibility[item.key]" :to="item.path">{{ item.label }}</router-link>
        </template>
        <div class="menu-settings">
          <button
            class="menu-settings-button"
            type="button"
            :aria-expanded="menuSettingsOpen"
            aria-controls="menu-settings-panel"
            @click="menuSettingsOpen = !menuSettingsOpen"
          >
            菜单设置
          </button>
          <div v-if="menuSettingsOpen" id="menu-settings-panel" class="menu-settings-panel">
            <div class="menu-settings-panel__header">
              <strong>菜单显示</strong>
              <button class="menu-settings-close" type="button" aria-label="关闭菜单设置" @click="menuSettingsOpen = false">关闭</button>
            </div>
            <label v-for="item in menuItems" :key="`setting-${item.key}`" class="menu-toggle">
              <input v-model="menuVisibility[item.key]" type="checkbox" @change="persistMenuVisibility" />
              <span>{{ item.label }}</span>
            </label>
            <button class="menu-settings-reset" type="button" @click="resetMenuVisibility">恢复默认</button>
          </div>
        </div>
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
import { reactive, ref } from 'vue'

const MENU_VISIBILITY_STORAGE_KEY = 'yolo-platform-menu-visibility'

const menuItems = [
  { key: 'home', label: '首页', path: '/' },
  { key: 'datasets', label: '数据集', path: '/datasets' },
  { key: 'annotate', label: '标注', path: '/annotate' },
  { key: 'train', label: '训练', path: '/train' },
  { key: 'models', label: '模型管理', path: '/models' },
  { key: 'infer', label: '推理', path: '/infer' },
  { key: 'channels', label: '通道', path: '/channels' },
  { key: 'cameraHealth', label: '摄像头健康', path: '/camera-health' },
  { key: 'electronicSeal', label: '电子封条', path: '/electronic-seal' },
  { key: 'events', label: '事件', path: '/events' },
  { key: 'recordings', label: '录像', path: '/recordings' }
] as const

type MenuKey = (typeof menuItems)[number]['key']

const createDefaultMenuVisibility = (): Record<MenuKey, boolean> => ({
  home: true,
  datasets: true,
  annotate: true,
  train: true,
  models: true,
  infer: true,
  channels: true,
  cameraHealth: import.meta.env.VITE_SHOW_CAMERA_HEALTH_MENU === 'true',
  electronicSeal: true,
  events: true,
  recordings: true
})

const readMenuVisibility = (): Record<MenuKey, boolean> => {
  const defaults = createDefaultMenuVisibility()
  if (typeof window === 'undefined') return defaults

  try {
    const stored = window.localStorage.getItem(MENU_VISIBILITY_STORAGE_KEY)
    if (!stored) return defaults
    const parsed = JSON.parse(stored) as Record<string, unknown>
    for (const item of menuItems) {
      if (typeof parsed[item.key] === 'boolean') {
        defaults[item.key] = parsed[item.key] as boolean
      }
    }
  } catch {
    return defaults
  }
  return defaults
}

const menuVisibility = reactive(readMenuVisibility())
const menuSettingsOpen = ref(false)

const persistMenuVisibility = () => {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(MENU_VISIBILITY_STORAGE_KEY, JSON.stringify({ ...menuVisibility }))
}

const resetMenuVisibility = () => {
  Object.assign(menuVisibility, createDefaultMenuVisibility())
  persistMenuVisibility()
}
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
  align-items: center;
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

.menu-settings {
  position: relative;
}

.menu-settings-button,
.menu-settings-close,
.menu-settings-reset {
  border: 0;
  cursor: pointer;
  font: inherit;
}

.menu-settings-button {
  color: white;
  background: transparent;
  padding: 0.5rem 0.75rem;
  border-radius: 4px;
}

.menu-settings-button:hover,
.menu-settings-button[aria-expanded='true'] {
  background: #34495e;
}

.menu-settings-panel {
  position: absolute;
  z-index: 10;
  top: calc(100% + 0.5rem);
  right: 0;
  width: 220px;
  padding: 0.75rem;
  color: #1f2937;
  background: white;
  border: 1px solid #d8dee6;
  border-radius: 6px;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.18);
}

.menu-settings-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 0.5rem;
  margin-bottom: 0.25rem;
  border-bottom: 1px solid #edf0f3;
}

.menu-settings-close {
  color: #64748b;
  background: transparent;
  font-size: 0.8rem;
}

.menu-toggle {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0;
  color: #334155;
  cursor: pointer;
}

.menu-toggle input {
  width: 1rem;
  height: 1rem;
  accent-color: #2563eb;
}

.menu-settings-reset {
  width: 100%;
  padding: 0.45rem 0.6rem;
  margin-top: 0.5rem;
  color: white;
  background: #2563eb;
  border-radius: 4px;
}

.main-content {
  padding: 2rem;
  max-width: 1400px;
  margin: 0 auto;
}
</style>
