import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

test('navigation exposes a persistent switch for every menu item', () => {
  const source = fs.readFileSync(path.join(root, 'src/App.vue'), 'utf8')

  assert.match(source, /菜单设置/)
  assert.match(source, /localStorage/)
  assert.match(source, /menuVisibility/)
  assert.match(source, /menuSettingsOpen/)
  for (const label of ['首页', '数据集', '标注', '训练', '模型管理', '推理', '通道', '摄像头健康', '电子封条', '事件', '录像']) {
    assert.match(source, new RegExp(label))
  }
})
