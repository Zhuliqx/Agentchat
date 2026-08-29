# API 参考

## 基础信息

- 基础地址：`https://api.example-tech.com`
- 当前版本：`v2.3.0`
- 鉴权方式：请求头 `Authorization: Bearer <token>`，token 在控制台"API 密钥"页面生成
- 限流：默认 60 次/分钟，超出返回 429

## 主要接口

### POST /v1/chat

对话补全接口。请求体包含 `message`、`session_id`、`use_rag` 等字段；支持流式响应（SSE）。

### POST /v1/rag/search

知识库检索接口。返回排序后的命中文档块与来源信息，供二次开发集成。

## 超时

普通请求超时 120 秒；流式请求无固定超时，连接断开即结束。
