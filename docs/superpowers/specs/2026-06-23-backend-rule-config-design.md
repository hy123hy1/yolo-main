# 后端统一算法规则配置设计

## 目标

将以下规则类型的业务阈值配置从前端/任务算法 `postprocess_config` 中收口到后端配置文件：

- `absence`
- `gathering`
- `congestion`
- `loitering`
- `fall_like`

保留 `backend/configs/config.toml` 原状不变，新增独立算法规则配置文件。

## 设计原则

- `detection` 类配置保持现状，不动
- 规则阈值由后端统一管理
- 前端不再展示这几类规则的业务参数
- 数据库中旧的 `postprocess_config_json` 可以保留，但运行时忽略

## 配置文件

新增：

- `backend/configs/algorithm_rules.toml`

建议按 `rule_type` 划分：

```toml
[absence]
absence_seconds = 5

[gathering]
count_threshold = 3
hold_seconds = 2

[congestion]
count_threshold = 5
hold_seconds = 2

[loitering]
linger_seconds = 10

[fall_like]
fall_ratio_threshold = 1.2
hold_seconds = 2
min_box_height = 40
```

## 运行时读取

新增独立读取模块，负责：

- 从 `backend/configs/algorithm_rules.toml` 加载
- 按 `rule_type` 返回对应配置
- 使用进程内缓存，避免分析线程逐帧重复读文件

配置变更策略：

- 修改配置文件后，重启后端服务生效

## 代码影响

### 后端

- `backend/src/core/algorithm_rule_config.py`
- `backend/src/services/analysis_service.py`

### 前端

- `frontend/src/components/channels/AlgorithmEditorModal.vue`

## 兼容策略

- `task_algorithms.postprocess_config_json` 表结构不变
- 旧数据不迁移
- 运行时完全忽略这 5 类规则在数据库里的旧参数
- 前端再次保存时，这 5 类规则的 `postprocess_config` 提交为空对象

## 验证点

1. 后端配置文件修改后可影响规则阈值
2. 数据库中的旧 `postprocess_config_json` 不再影响这 5 类规则
3. 前端不再展示这 5 类规则的业务参数
4. detection 类现有行为不受影响
