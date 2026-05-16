import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'
import { fileURLToPath } from 'node:url'

const __dirname = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const apiBase = env.VITE_API_BASE || '/dev-api'
  const apiTarget = env.VITE_API_TARGET || 'http://localhost:8000'

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src')
      }
    },
    server: {
      port: 3000,
      proxy: {
        [apiBase]: {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (value) => value.replace(new RegExp(`^${apiBase}`), ''),
          ws: true,
          configure: (proxy) => {
            proxy.on('error', (err) => {
              console.log('proxy error', err)
            })
          }
        },
        '/static': {
          target: apiTarget,
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('error', (err) => {
              console.log('static proxy error', err)
            })
          }
        },
        '/proxy': {
          target: apiTarget,
          changeOrigin: true,
          ws: true,
          configure: (proxy) => {
            proxy.on('error', (err) => {
              console.log('media proxy error', err)
            })
          }
        }
      }
    }
  }
})
