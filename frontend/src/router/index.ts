import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Dashboard',
    component: () => import('../pages/Dashboard.vue')
  },
  {
    path: '/datasets',
    name: 'DatasetUpload',
    component: () => import('../pages/DatasetUpload.vue')
  },
  {
    path: '/annotate',
    name: 'DatasetAnnotate',
    component: () => import('../pages/DatasetAnnotate.vue')
  },
  {
    path: '/train',
    name: 'Train',
    component: () => import('../pages/Train.vue')
  },
  {
    path: '/models',
    name: 'Models',
    component: () => import('../pages/Models.vue')
  },
  {
    path: '/infer',
    name: 'Infer',
    component: () => import('../pages/Infer.vue')
  },
  {
    path: '/video-infer',
    name: 'VideoInfer',
    component: () => import('../pages/VideoInfer.vue')
  },
  {
    path: '/media-nodes',
    name: 'MediaNodes',
    component: () => import('../pages/MediaNodes.vue')
  },
  {
    path: '/devices',
    name: 'Devices',
    component: () => import('../pages/Devices.vue')
  },
  {
    path: '/channels',
    name: 'Channels',
    component: () => import('../pages/Channels.vue')
  },
  {
    path: '/events',
    name: 'Events',
    component: () => import('../pages/Events.vue')
  },
  {
    path: '/recordings',
    name: 'Recordings',
    component: () => import('../pages/Recordings.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
