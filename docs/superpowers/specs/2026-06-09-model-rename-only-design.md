# 模型编辑仅改名设计

## 目标

将模型管理中的“编辑模型”收敛为“仅修改模型名称”。

## 范围

- 前端模型列表中的编辑动作只允许输入并提交 `name`
- 前端不再在编辑流程里提交 `description` 和 `tags`
- 后端 `PUT /models/{model_id}` 仅接收 `name`
- 模型详情中的 `description` 继续保留为只读展示

## 非目标

- 不修改模型上传逻辑
- 不清理已有模型文件中的 `description` / `tags`
- 不调整模型详情接口的返回结构

## 设计决策

### 前端

- 保留当前“编辑”按钮入口
- 点击编辑后只弹出一个名称输入框
- 提交时仅调用：

```json
{
  "name": "新模型名称"
}
```

### 后端

- `UpdateModelRequest` 仅保留 `name`
- `ModelService.update_model()` 仅更新 `name` 和 `updated_at`
- 已存在的 `description` / `tags` 字段继续原样保存在模型元数据中，不在编辑接口中修改

## 兼容性

- 旧模型数据不会丢失
- 模型详情页继续可以只读展示历史 `description`
- 第三方若继续向该接口传 `description` / `tags`，将不再被后端模型接收结构识别

## 验收标准

1. 点击模型编辑时，只出现名称输入
2. 保存时浏览器仅发送 `name`
3. 后端更新后，模型名称变化
4. 历史 `description` 在详情页仍可显示
5. `description` 和 `tags` 不再能通过编辑入口改动
