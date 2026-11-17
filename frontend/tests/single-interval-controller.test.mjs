import test from 'node:test'
import assert from 'node:assert/strict'

import {
  createSingleIntervalController,
  shouldPollChannelAiStatus,
} from '../src/utils/single-interval-controller.js'

test('start only schedules one interval while already running', () => {
  const scheduled = []
  const controller = createSingleIntervalController({
    schedule(callback, intervalMs) {
      const timer = { callback, intervalMs, id: scheduled.length + 1 }
      scheduled.push(timer)
      return timer
    },
    clear() {},
  })

  const firstStarted = controller.start(() => {}, 5000)
  const secondStarted = controller.start(() => {}, 5000)

  assert.equal(firstStarted, true)
  assert.equal(secondStarted, false)
  assert.equal(scheduled.length, 1)
  assert.equal(controller.isRunning(), true)
})

test('stop clears current interval and allows scheduling again', () => {
  const cleared = []
  const scheduled = []
  const controller = createSingleIntervalController({
    schedule(callback, intervalMs) {
      const timer = { callback, intervalMs, id: scheduled.length + 1 }
      scheduled.push(timer)
      return timer
    },
    clear(timer) {
      cleared.push(timer.id)
    },
  })

  controller.start(() => {}, 5000)
  const stopped = controller.stop()
  const restarted = controller.start(() => {}, 5000)

  assert.equal(stopped, true)
  assert.deepEqual(cleared, [1])
  assert.equal(restarted, true)
  assert.equal(scheduled.length, 2)
})

test('only the manage tab should poll channel ai status', () => {
  assert.equal(shouldPollChannelAiStatus('manage'), true)
  assert.equal(shouldPollChannelAiStatus('preview'), false)
  assert.equal(shouldPollChannelAiStatus('tasks'), false)
  assert.equal(shouldPollChannelAiStatus('fence'), false)
})
