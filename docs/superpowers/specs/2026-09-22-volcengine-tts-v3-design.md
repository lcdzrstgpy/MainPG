# 火山语音 V3 配音设计

## 目标

把 ClipForge 的付费配音配置改为火山引擎豆包语音合成 V3，供 MainPG 的 AI 视频模块生成完整 MP3 后交给现有合成链路使用。

## 边界

- 仅改配音配置与配音请求；不改变豆包/速创的生图、生视频和脚本链路。
- 删除设置页内 Atlas、MiniMax、fal.ai 与错误的“火山方舟 OpenAI 兼容”配音入口。
- 保留现有缓存、重试、熔断和 FFmpeg 合成逻辑。
- 不读取、不记录、不测试用户截图中已暴露的旧 Key。

## 设计

采用火山 V3 HTTP Chunked 单向接口：`POST https://openspeech.bytedance.com/api/v3/tts/unidirectional`。它适合服务端收齐音频字节后缓存并合成，且无需引入 WebSocket 依赖。

设置页只展示“火山语音 V3（豆包 Seed TTS）”，配置 API Key、资源 ID、引擎、音色和语速。默认资源 ID 为 `seed-tts-2.0`，默认引擎为 `seed-tts-2.0-expressive`，音色由用户从本人控制台授权列表粘贴。语速按官方范围 `-50..100` 保存。

调用层向接口发送 `X-Api-Key`、`X-Api-Resource-Id`、`X-Api-Request-Id` 与 JSON body；增量解析 HTTP chunked 返回的 JSON/SSE `data:` 记录，拼接 code 为 0 的 base64 音频，并在完成码 `20000000` 后返回 MP3。任一非成功业务码、非 2xx HTTP 状态、格式错误或无音频均抛出包含可操作原因的错误。

## 验证

- 单元测试覆盖：请求头和 body 的 V3 映射、拆分的 SSE/JSON 流解析、API 业务错误、无音频响应。
- 现有 TTS 测试和构建通过。
- 用户轮换为语音控制台新 Key 后，在设置页使用已授权音色进行真实试听。
