# im-bridge（企微优先、多通道可扩展）设计

日期：2026-07-29  
状态：已批准，待 im-bridge Agent 按实现计划执行  
范围：MaxKB 社区版自建 IM 桥接；第一版仅企业微信智能机器人（群 @）+ 文本 + 流式回复

## 背景与目标

公司使用 MaxKB 社区版搭建知识库，需要在企业微信群内通过 `@机器人` 问答。社区版无官方企微接入（X-Pack），因此自建独立桥接服务。

成功标准：

1. 企微智能机器人 API 回调 URL 可保存通过验证
2. 内部群 `@机器人` 发文字，收到流式逐渐完整的回答
3. 同一人同群在会话 TTL 内追问有上下文
4. MaxKB 不可用时用户可见失败提示，日志可定位
5. 相同 `msg_id` 不重复回答
6. `/health`、`/ready` 可用；可用 `msg_id`/`stream_id` 串起全链路日志

## 非目标（第一版）

- 飞书、WhatsApp 等通道的具体实现（仅预留接口与目录）
- 图片、文件、语音消息
- Redis / 多实例部署（文档标明限制；接口预留）
- 管理后台 UI
- 修改 MaxKB 上游源码

## 决策摘要

| 项 | 选择 |
|----|------|
| 部署形态 | 独立服务（不嵌入 MaxKB 进程） |
| 技术栈 | Python + FastAPI |
| 企微交互 | HTTP 回调 + 流式消息刷新（官方推荐路径） |
| 会话 | `channel + chat_id + user_id`，TTL 默认 1800s |
| 消息类型 | 仅文本 |
| 扩展性 | Channel Adapter 模式，后期加飞书/WhatsApp |
| 可维护性 | 结构化日志、关联 ID、分 stage、健康检查 |

代码落地位置：`D:\Document\Cursor\LLMRAG\im-bridge\`（与同目录下 `MaxKB/` 平级），不修改 MaxKB 源码。设计与实现计划均在本仓库 `docs/superpowers/` 下。

## 架构

```text
企微 /（后期）飞书 / WhatsApp
        │
        ▼
   im-bridge (FastAPI)
   ┌─────────────────────────────┐
   │ HTTP 入口（按通道路由）        │
   │  /wecom/*  /feishu/*  ...    │
   ├─────────────────────────────┤
   │ Channel Adapter（可插拔）      │
   │  验签/加解密 → 统一入站事件    │
   │  统一出站块 → 平台协议回包     │
   ├─────────────────────────────┤
   │ Core（与平台无关）             │
   │  session / maxkb_client       │
   │  stream_tasks / dedupe        │
   └─────────────────────────────┘
                │
                ▼
        MaxKB OpenAI 兼容 API
        {MAXKB_BASE_URL}/chat/completions
```

### 统一内部模型

入站 `IncomingMessage`：

- `channel`: 如 `wecom`
- `chat_id`: 群 ID 或单聊约定键
- `user_id`: 发送者
- `text`: 已去掉 @ 的纯文本
- `msg_id`: 平台消息唯一 ID（去重）
- `raw`: 可选，原始明文（仅调试，默认不落盘）

出站：

- `OutgoingChunk`: `stream_id`, `text`（累计或增量，由 WeCom adapter 按协议组装）, `done: bool`
- 非流式兜底：`OutgoingReply{ text }`（用于「暂不支持」等即时短回复）

### 目录结构

```text
im-bridge/
  app/
    main.py                 # FastAPI 应用、路由挂载、中间件
    logging_setup.py        # JSON 日志配置
    config.py               # 环境变量
    core/
      models.py             # IncomingMessage / OutgoingChunk / SessionKey
      session.py
      dedupe.py
      stream_tasks.py
      maxkb_client.py
    channels/
      base.py               # Channel 协议/抽象基类
      wecom/
        router.py           # GET/POST 回调
        crypto.py           # 加解密与验签
        adapter.py          # 归一化 + 流式被动回复组装
        protocol.py         # 企微 JSON 字段常量/构造
      # feishu/             # 后期
      # whatsapp/           # 后期
  tests/
  .env.example
  README.md
  pyproject.toml / requirements.txt
  Dockerfile                # 可选，第一版可后补
```

## 核心模块

| 模块 | 职责 | 排障关键词 |
|------|------|------------|
| `channels/wecom` | URL 校验、加解密、流式被动回复 | `stage=wecom.verify\|decrypt\|reply` |
| `core` 归一化入口 | 平台消息 → `IncomingMessage` | `stage=normalize` |
| `core/session` | 会话映射与 TTL | `stage=session` |
| `core/maxkb_client` | MaxKB 流式 chat/completions | `stage=maxkb` |
| `core/stream_tasks` | 流式任务缓冲与状态 | `stage=stream` |
| `core/dedupe` | `msg_id` 去重 | `stage=dedupe` |

### 企微主数据流

1. **GET 回调（URL 验证）**  
   验签 → 解密 `echostr` → 1 秒内返回明文。

2. **POST 用户文本消息**  
   验签解密 → `msg_id` 去重 → 归一化为 `IncomingMessage` → 取/建会话 → 创建 `stream_task` 并启动后台协程调 MaxKB → HTTP 响应加密的流式消息占位（可含已有文本或「正在思考」）。

3. **POST 流式刷新**  
   按协议中的流式 ID 读取 `stream_tasks` 当前累计文本与 `done` → 加密回包；`done=true` 结束刷新。

4. **后台 MaxKB 任务**  
   使用 OpenAI 兼容流式接口，持续把 token 追加到 `stream_tasks`；异常时写入错误提示并 `done=true`。

会话键：`SessionKey(channel, chat_id, user_id)`。存储：第一版进程内 dict + 过期清理；README 标明多实例需 Redis。

MaxKB 调用约定：

- URL：`{MAXKB_BASE_URL}/chat/completions`（`MAXKB_BASE_URL` 已含 application id 路径段）
- Header：`Authorization: Bearer {MAXKB_API_KEY}`
- Body：OpenAI 风格 `messages`；`stream: true`
- 会话默认模式 `MAXKB_MODE=openai_messages`：由 `session` 模块在桥接侧保存最近 `SESSION_MAX_TURNS` 轮 user/assistant 文本，每次请求带上完整 messages 窗口
- 可选模式 `MAXKB_MODE=session_api`：若联调确认社区版 API Key 的 open/chat 会话接口可用，则改存 MaxKB `chat_id`，不再本地堆 messages（第一版实现可先做 `openai_messages`，`session_api` 留接口分支）

## 可观测性与维护性

- 结构化 JSON 日志字段至少包含：  
  `timestamp`, `level`, `request_id`, `channel`, `msg_id`, `stream_id`, `chat_id`, `user_id`, `stage`, `latency_ms`, `message`
- HTTP 中间件生成/透传 `request_id`；企微回调内把 `msg_id`/`stream_id` 写入 contextvar，后续日志自动带上
- 错误不吞栈：`logger.exception`；用户可见文案与日志错误码分离
- 耗时打点：decrypt、maxkb_ttfb、maxkb_total、callback_total
- `GET /health`：进程存活
- `GET /ready`：配置齐全且能对 MaxKB base 做轻量探测（如 TCP/HTTP 可达或 OPTIONS；失败则 503）
- 启动日志打印非敏感配置摘要（base URL、app 路径掩码、TTL、log level）；密钥只显示后四位
- README 提供排障表：签名失败 / 401 / 一直思考中 / 重复回复 → 对应 stage 与检查项

## 配置

### 通用

| 变量 | 含义 | 默认 |
|------|------|------|
| `MAXKB_BASE_URL` | 至 application 的 chat API 前缀 | 必填 |
| `MAXKB_API_KEY` | Bearer Token | 必填 |
| `MAXKB_MODE` | `openai_messages` 或 `session_api` | `openai_messages` |
| `SESSION_TTL_SECONDS` | 会话过期 | `1800` |
| `SESSION_MAX_TURNS` | 桥接侧保留的最大轮数（messages 模式） | `20` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `HOST` / `PORT` | 监听 | `0.0.0.0` / `8090` |

### 企微

| 变量 | 含义 |
|------|------|
| `WECOM_ENABLED` | 是否挂载企微路由，默认 `true` |
| `WECOM_TOKEN` | 智能机器人 Token |
| `WECOM_ENCODING_AES_KEY` | EncodingAESKey |

后期通道使用独立前缀：`FEISHU_*`、`WHATSAPP_*`。

## 错误处理

| 场景 | 用户侧 | 日志 |
|------|--------|------|
| 验签/解密失败 | 无正常业务回复 | `stage=wecom.verify\|decrypt` error |
| 重复 `msg_id` | 不再回答 | `stage=dedupe` info |
| 非文本 | 「暂仅支持文字提问」 | `stage=normalize` info |
| MaxKB 超时/5xx | 「知识库暂时不可用，请稍后重试」并 done | `stage=maxkb` error + status |
| MaxKB 401 | 同上 | `stage=maxkb` error `maxkb.auth` |
| 进程重启导致 stream 丢失 | 「服务已刷新，请重新 @ 提问」 | `stage=stream` warn |
| 未捕获异常 | 尽量回短错误提示 | `exception` + 全栈 |

## Channel 扩展约定（后期）

`channels/base.py` 至少定义：

- `verify_url(...)`（若平台需要）
- `parse_inbound(request) -> IncomingMessage | StreamRefresh | None`
- `build_stream_response(chunk: OutgoingChunk) -> Response`
- `build_text_response(reply: OutgoingReply) -> Response`

Core 只依赖上述抽象与统一模型；禁止在 `core/` 中引用企微字段名。

## 测试计划（实现阶段）

- 单元：加解密向量（官方样例）、去重、会话 TTL、文本去 @
- 单元：MaxKB client 对流式 chunk 的拼接（httpx mock）
- 集成（可选）：对企微回调 JSON fixture 跑通 adapter
- 手工：真实企微群 @ 联调清单见验收标准

## 风险与限制

- 企微智能机器人协议若变更，仅影响 `channels/wecom`
- 单实例内存状态：水平扩展前必须引入共享存储
- 流式刷新依赖企微持续回调；桥接必须快速响应，MaxKB 只在后台任务中跑
- `from.userid` 在非超管创建机器人时可能是密文 ID，会话键仍可用该字符串，不要求第一版转明文

## 参考

- [MaxKB 通过 API KEY 对话](https://maxkb.cn/docs/v2/user_manual/chat_to_API/)
- [企业微信智能机器人接收消息](https://developer.work.weixin.qq.com/document/path/100719)
- [回调和回复的加解密方案](https://developer.work.weixin.qq.com/document/path/101033)
