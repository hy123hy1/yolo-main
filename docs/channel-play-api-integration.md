# 通道播放地址统一接口说明

本文档用于前端、后端以及第三方平台对接统一播放地址接口。

目标：
- 一个接口返回通道播放所需地址
- 同时支持原始流、区域流、自动选择
- 明确区分“源地址”和“最终应播放地址”
- 同时提供浏览器可直接访问的完整地址

## 1. 推荐接口

统一使用：

`GET /channels/{channel_id}/play-url`

支持查询参数：
- `mode=auto`
- `mode=channel`
- `mode=zone`
- `compact=true|false`

说明：
- `channel`：返回原始通道流
- `zone`：返回带框区域流
- `auto`：如果当前存在区域流，则优先返回区域流，否则返回原始流
- `compact=true`：返回精简结构，只保留最核心的几组流地址

示例：

```http
GET /channels/34020000001110000121_34020000001320000002/play-url?mode=auto
GET /channels/34020000001110000121_34020000001320000002/play-url?mode=channel
GET /channels/34020000001110000121_34020000001320000002/play-url?mode=zone
GET /channels/34020000001110000121_34020000001320000002/play-url?mode=auto&compact=true
```

## 2. 返回字段说明

### 2.1 核心控制字段

- `requested_mode`
  调用方请求的模式。

- `resolved_mode`
  后端最终解析出的模式。
  比如请求 `auto`，若当前有区域流，则这里会返回 `zone`。

- `source`
  当前最终使用的是哪一类流。
  可选值：
  - `channel`
  - `zone`

- `status`
  当前通道在线状态。

### 2.2 新推荐字段

- `source_urls`
  当前 `source` 对应的标准流地址集合。
  如果 `source=channel`，这里就是原始流。
  如果 `source=zone`，这里就是区域流。

- `source_browser_urls`
  当前 `source` 对应的浏览器播放地址集合。

- `final_play_urls`
  当前这次请求最终应使用的标准流地址集合。
  一般第三方平台优先取这一组。

- `final_browser_play_urls`
  当前这次请求最终应使用的浏览器地址集合。
  一般前端页面优先取这一组。

### 2.3 兼容保留字段

下面这些字段保留给历史前端和旧调用方：

- `urls`
  原始通道流地址集合。

- `browser_urls`
  原始通道对应的浏览器地址集合。

- `zone_preview_urls`
  区域流地址集合。

- `play_urls`
  兼容字段，等价于 `final_play_urls`。

- `browser_play_urls`
  兼容字段，等价于 `final_browser_play_urls`。

## 3. 地址字段说明

每一组地址里通常会包含以下字段：

- `rtsp`
- `rtmp`
- `hls`
- `httpflv`
- `webrtc`
- `browser_hls`
- `browser_httpflv`
- `browser_webrtc_api`
- `browser_hls_url`
- `browser_httpflv_url`
- `browser_webrtc_api_url`

说明：

- `rtsp/rtmp/hls/httpflv/webrtc`
  标准播放地址，可给第三方系统、播放器或其他服务使用。

- `browser_hls/browser_httpflv/browser_webrtc_api`
  相对路径代理地址，主要给当前前端站点同域使用。
  例如：
  - `/proxy/sms/live/101/hls.fmp4.m3u8`

- `browser_hls_url/browser_httpflv_url/browser_webrtc_api_url`
  完整浏览器访问地址，适合第三方 Web 页面或外部系统直接使用。
  例如：
  - `http://192.168.1.61:8010/proxy/sms/live/101/hls.fmp4.m3u8`

## 4. 返回示例

```json
{
  "channel_id": "ch_20260527_153119_de0c0878",
  "requested_mode": "auto",
  "resolved_mode": "zone",
  "source": "zone",
  "status": "online",
  "source_urls": {
    "rtsp": "rtsp://192.168.1.61:554/live/101_zone",
    "rtmp": "rtmp://192.168.1.61:1935/live/101_zone",
    "hls": "http://192.168.1.61:8010/live/101_zone/hls.fmp4.m3u8",
    "httpflv": "http://192.168.1.61:8010/live/101_zone.live.flv",
    "webrtc": "webrtc://192.168.1.61:8010/index/api/webrtc?app=live&stream=101_zone&type=play",
    "browser_hls": "/proxy/sms/live/101_zone/hls.fmp4.m3u8",
    "browser_httpflv": "/proxy/sms/live/101_zone.live.flv",
    "browser_webrtc_api": "/proxy/sms/index/api/webrtc?app=live&stream=101_zone&type=play",
    "browser_hls_url": "http://192.168.1.61:8010/proxy/sms/live/101_zone/hls.fmp4.m3u8",
    "browser_httpflv_url": "http://192.168.1.61:8010/proxy/sms/live/101_zone.live.flv",
    "browser_webrtc_api_url": "http://192.168.1.61:8010/proxy/sms/index/api/webrtc?app=live&stream=101_zone&type=play"
  },
  "final_play_urls": {
    "rtsp": "rtsp://192.168.1.61:554/live/101_zone",
    "rtmp": "rtmp://192.168.1.61:1935/live/101_zone",
    "hls": "http://192.168.1.61:8010/live/101_zone/hls.fmp4.m3u8",
    "httpflv": "http://192.168.1.61:8010/live/101_zone.live.flv",
    "webrtc": "webrtc://192.168.1.61:8010/index/api/webrtc?app=live&stream=101_zone&type=play",
    "browser_hls": "/proxy/sms/live/101_zone/hls.fmp4.m3u8",
    "browser_httpflv": "/proxy/sms/live/101_zone.live.flv",
    "browser_webrtc_api": "/proxy/sms/index/api/webrtc?app=live&stream=101_zone&type=play",
    "browser_hls_url": "http://192.168.1.61:8010/proxy/sms/live/101_zone/hls.fmp4.m3u8",
    "browser_httpflv_url": "http://192.168.1.61:8010/proxy/sms/live/101_zone.live.flv",
    "browser_webrtc_api_url": "http://192.168.1.61:8010/proxy/sms/index/api/webrtc?app=live&stream=101_zone&type=play"
  }
}
```

### 4.1 精简返回示例

如果调用：

```http
GET /channels/{channel_id}/play-url?mode=auto&compact=true
```

则只返回最核心字段：

```json
{
  "channel_id": "ch_20260527_153119_de0c0878",
  "requested_mode": "auto",
  "resolved_mode": "zone",
  "source": "zone",
  "status": "online",
  "channel_stream": {},
  "browser_stream": {},
  "zone_stream": {},
  "final_stream": {},
  "final_browser_stream": {}
}
```

说明：

- `channel_stream`
  原始通道流

- `browser_stream`
  原始通道对应的浏览器流

- `zone_stream`
  带框区域流

- `final_stream`
  当前最终应该使用的标准播放流

- `final_browser_stream`
  当前最终应该使用的浏览器播放流

## 5. 不同场景怎么取

### 5.1 前端页面播放

优先取：

- `final_browser_play_urls.browser_webrtc_api_url`

失败后回退：

- `final_browser_play_urls.browser_hls_url`

如果前端本身就在同域代理下，也可以继续用相对路径：

- `final_browser_play_urls.browser_webrtc_api`
- `final_browser_play_urls.browser_hls`

### 5.2 第三方平台只想拿“当前该播哪一路”

优先取：

- `final_play_urls.rtsp`
- 或 `final_play_urls.hls`

同时结合：

- `resolved_mode`
- `source`

即可判断当前返回的是原始流还是区域流。

### 5.3 强制拿原始流

使用：

- `mode=channel`

然后取：

- `final_play_urls`
- 或 `final_browser_play_urls`

### 5.4 强制拿区域流

使用：

- `mode=zone`

然后取：

- `final_play_urls`
- 或 `final_browser_play_urls`

## 6. 旧接口兼容

旧区域流地址接口仍保留兼容：

`GET /channels/{channel_id}/zone-preview/play-url`

但新接入统一推荐改用：

`GET /channels/{channel_id}/play-url?mode=zone`

## 7. 对接建议

建议前端和第三方统一遵循下面规则：

1. 优先只调统一接口 `/channels/{channel_id}/play-url`
2. 显式传 `mode`
3. 页面播放优先取 `final_browser_play_urls`
4. 第三方标准协议播放优先取 `final_play_urls`
5. 若需要完整浏览器地址，优先取 `browser_hls_url/browser_httpflv_url/browser_webrtc_api_url`
6. 旧字段仅作为兼容过渡，不建议新系统继续依赖
