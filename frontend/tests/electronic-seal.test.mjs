import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

test('electronic seal api uses an independent port proxy and exposes required operations', () => {
  const source = fs.readFileSync(path.join(root, 'src/api/electronicSeal.ts'), 'utf8')
  assert.match(source, /electronic-seal-api/)
  assert.match(source, /configs/)
  assert.match(source, /recalibrate/)
  assert.match(source, /evidence/)
  assert.match(source, /retry/)
})

test('electronic seal page renders line, region, status, evidence, and upload controls', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/ElectronicSeal.vue'), 'utf8')
  assert.match(source, /电子封条/)
  assert.match(source, /虚拟线配置/)
  assert.match(source, /区域配置/)
  assert.match(source, /证据|截图|视频/)
  assert.match(source, /重试上传/)
  assert.match(source, /canvas/)
  assert.match(source, /coordinate_space: 'normalized'/)
  assert.match(source, /复用检测模型/)
})

test('electronic seal preview accepts the compact channel play response', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/ElectronicSeal.vue'), 'utf8')
  assert.match(source, /result\?\.final_browser_stream/)
  assert.match(source, /result\?\.final_browser_play_urls/)
  assert.match(source, /result\?\.browser_play_urls/)
  assert.match(source, /result\?\.browser_urls/)
})

test('electronic seal preview uses WebRTC and waits for the first frame', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/ElectronicSeal.vue'), 'utf8')
  assert.match(source, /browser_webrtc_api/)
  assert.match(source, /new RTCPeerConnection/)
  assert.match(source, /waitForVideoFrame/)
})

test('electronic seal preview refreshes the browser stream before using cached play urls', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/ElectronicSeal.vue'), 'utf8')
  assert.match(source, /startChannelPlay/)
  assert.match(source, /browser_urls|browser_play_urls/)
})

test('electronic seal preview renders playback errors instead of leaving a silent black box', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/ElectronicSeal.vue'), 'utf8')
  assert.match(source, /v-if="previewError"/)
})

test('electronic seal overlay stays above the browser video layer', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/ElectronicSeal.vue'), 'utf8')
  assert.match(source, /\.preview-video\s*\{[^}]*z-index:\s*1/)
  assert.match(source, /\.preview-overlay\s*\{[^}]*z-index:\s*2/)
})

test('electronic seal auto-loads preview after selecting a channel and keeps retry control', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/ElectronicSeal.vue'), 'utf8')
  assert.match(source, /await Promise\.all\(\[refreshStatus\(\), loadEvents\(\)\]\)\s*await startPreview\(\)/)
  assert.match(source, /重新加载预览|加载原始流/)
})

test('electronic seal redraws saved geometry after Vue updates the configuration state', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/ElectronicSeal.vue'), 'utf8')
  assert.match(source, /import \{ computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch \} from 'vue'/)
  assert.match(source, /const syncDraft = async \(\) => \{[\s\S]*?await nextTick\(\)[\s\S]*?resizeCanvas\(\)/)
  assert.match(source, /watch\(\[selectedChannelId, selectedAlgorithm\], \(\) => \{\s*void syncDraft\(\)\s*\}, \{ flush: 'post' \}\)/)
})

test('electronic seal redraw ignores an empty line config and still renders the saved region', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/ElectronicSeal.vue'), 'utf8')
  assert.match(source, /const parseGeometryPoints = \(value: unknown,?\s*canvasWidth/)
  assert.match(source, /const linePoints = parseGeometryPoints\(line,/)
  assert.match(source, /const regionPoints = parseGeometryPoints\(region,/)
  assert.match(source, /if \(linePoints\.length === 2\)/)
  assert.match(source, /if \(regionPoints\.length >= 3\)/)
  assert.match(source, /ElectronicSealCanvas.*JSON\.stringify\(payload\)/)
})

test('electronic seal uses the same blue region color as generated evidence', () => {
  const source = fs.readFileSync(path.join(root, 'src/pages/ElectronicSeal.vue'), 'utf8')
  assert.match(source, /const REGION_COLOR = '#0000ff'/)
  assert.match(source, /context\.strokeStyle = REGION_COLOR; drawShape\(context, regionPoints, true\)/)
  assert.doesNotMatch(source, /context\.strokeStyle = '#22c55e'/)
})

test('frontend routes and proxies include the isolated electronic seal module', () => {
  const router = fs.readFileSync(path.join(root, 'src/router/index.ts'), 'utf8')
  const app = fs.readFileSync(path.join(root, 'src/App.vue'), 'utf8')
  const vite = fs.readFileSync(path.join(root, 'vite.config.ts'), 'utf8')
  const nginx = fs.readFileSync(path.join(root, '..', 'docker', 'frontend', 'nginx.conf'), 'utf8')
  assert.match(router, /electronic-seal/)
  assert.match(app, /电子封条/)
  assert.match(vite, /ELECTRONIC_SEAL_API_TARGET|electronic-seal-api/)
  assert.match(nginx, /electronic-seal-worker:9001/)
})
