import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

test('camera health api exposes independent algorithm keys and endpoints', () => {
  const source = fs.readFileSync(path.join(root, 'src/api/cameraHealth.ts'), 'utf8')

  assert.match(source, /camera_occlusion/)
  assert.match(source, /camera_view_shift/)
  assert.match(source, /camera-health\/configs/)
  assert.match(source, /camera-health\/channels/)
})

test('camera health page is independent from task algorithm configuration', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/CameraHealth.vue'), 'utf8')

  assert.match(source, /摄像头遮挡识别/)
  assert.match(source, /摄像头扭转角度识别/)
  assert.doesNotMatch(source, /task_algorithms|TaskEditorModal|算法区域/)
})

test('camera health menu is controlled by a disabled-by-default feature flag', () => {
  const appSource = fs.readFileSync(path.join(root, 'src/App.vue'), 'utf8')

  assert.match(appSource, /cameraHealth: import\.meta\.env\.VITE_SHOW_CAMERA_HEALTH_MENU === 'true'/)
  assert.match(appSource, /v-if="menuVisibility\[item\.key\]"/)
})
