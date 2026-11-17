import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = await readFile(new URL('../src/pages/Channels.vue', import.meta.url), 'utf8')

test('电子围栏放大预览使用带 canvas 的 fence 模式', () => {
  assert.match(
    source,
    /@click="openExpandedFencePreview\(item\)"/,
    '电子围栏放大预览按钮必须进入 fence 模式'
  )
  assert.match(
    source,
    /class="expanded-fence-overlay-canvas"/,
    '电子围栏放大预览必须存在独立的 canvas 叠加层'
  )
  assert.match(
    source,
    /mode\?: 'channel' \| 'zone' \| 'fence'/,
    '放大预览状态必须区分电子围栏模式'
  )
})

test('电子围栏小窗使用独立 video ref，避免切换页面时引用互相清理', () => {
  assert.match(source, /const fenceVideoRefs = new Map<string, HTMLVideoElement>\(\)/)
  assert.match(source, /setFenceVideoRef\(item\.id, el as HTMLVideoElement \| null\)/)
  assert.match(source, /const video = \(activeTab\.value === 'fence'\s*\? fenceVideoRefs\s*:\s*videoRefs\)\.get\(channelId\)/)
})

test('WebRTC 收到 track 后直接绑定远端 stream 并触发播放', () => {
  assert.match(source, /const attachRemoteStreamToVideo = \(/)
  assert.match(source, /video\.srcObject = stream/)
  assert.match(source, /attachRemoteStreamToVideo\(video, remoteStream, event\)/)
})

test('播放器只有收到首帧后才算播放成功', () => {
  assert.match(source, /const waitForVideoFrame = \(/)
  assert.match(source, /await waitForVideoFrame\(video\)/)
  assert.match(source, /video\.videoWidth > 0 && video\.videoHeight > 0/)
})
