import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = await readFile(new URL('../src/pages/Channels.vue', import.meta.url), 'utf8')

const functionBody = (name, nextName) => {
  const start = source.indexOf(`const ${name} =`)
  const end = source.indexOf(`const ${nextName} =`, start)
  assert.notEqual(start, -1, `${name} must exist`)
  assert.notEqual(end, -1, `${nextName} must follow ${name}`)
  return source.slice(start, end)
}

test('task start and stop handlers do not refresh playback preview state', () => {
  const draftBody = functionBody('startTaskDraft', 'startTaskAction')
  const startBody = functionBody('startTaskAction', 'stopTaskAction')
  const stopBody = functionBody('stopTaskAction', 'deleteTaskAction')

  assert.doesNotMatch(draftBody, /refreshFenceChannelState/)
  assert.doesNotMatch(startBody, /refreshFenceChannelState/)
  assert.doesNotMatch(stopBody, /refreshFenceChannelState/)
})

test('manual browser playback remains available independently', () => {
  assert.match(source, /const startBrowserPlay = async \(channelId: string\)/)
  assert.match(source, /const ensureBrowserPlaybackUrls = async \(channelId: string\)/)
})
