# zai.is2api - System Design Document (v1)

## 1. 系统架构概览 (System Architecture)

系统采用微服务风格的单体架构，核心分为三个模块：**API Gateway (接口层)**、**Token Manager (管理层)** 和 **Worker (任务层)**。

```mermaid
graph TD
    Client[Client (OpenAI SDK)] -->|/v1/chat/completions| Gateway[FastAPI Gateway]
    
    subgraph Core System
        Gateway -->|1. Request Token| TM[Token Manager]
        Gateway -->|3. Forward Request| ZaiClient[Zai API Client]
        
        TM -->|Read/Write| Redis[(Redis Cache)]
        TM -->|Read/Write| DB[(SQLite DB)]
        
        ZaiClient -->|4. Call API| ZaiTarget[zai.is]
        ZaiTarget -->|5. SSE Stream| ZaiClient
        
        Worker[Background Worker] -->|Monitor & Refresh| TM
        Worker -->|Execute Script| ZaiScript[zai_token.py]
    end
    
    ZaiScript -->|Login| ZaiTarget
```

## 2. 核心数据结构设计 (Data Structures)

### 2.1 SQLite (持久化存储)
用于存储用户配置的 Discord Token 及其元数据。

**Table: `accounts`**

| Field | Type | Description |
|-------|------|-------------|
| `id` | INTEGER PK | 自增主键 |
| `discord_token` | TEXT | 用户提供的 Discord Token (Unique) |
| `is_active` | BOOLEAN | 账号是否启用 (默认 True) |
| `created_at` | DATETIME | 创建时间 |
| `last_error` | TEXT | 最后一次登录/刷新失败的错误信息 |

### 2.2 Redis (运行时状态 & 缓存)
用于存储临时的 Zai Token 和限流计数器，以实现高性能和原子操作。

**Key Schema:**

1.  **Zai Access Token**
    *   **Key**: `zai:token:{discord_token_hash}`
    *   **Value**: `eyJhbGciOi...` (Zai JWT String)
    *   **TTL**: 动态设置 (Zai Token 有效期通常为 3 小时，我们设置为 2小时50分，留 10 分钟缓冲)

2.  **Rate Limiter (1 RPM)**
    *   **Key**: `zai:limit:{discord_token_hash}`
    *   **Value**: `1`
    *   **TTL**: 60 秒 (严格过期)

3.  **Token Refresh Lock** (防止并发刷新)
    *   **Key**: `zai:refresh_lock:{discord_token_hash}`
    *   **Value**: `locked`
    *   **TTL**: 30 秒

## 3. Token 管理逻辑 (Token Lifecycle)

### 3.1 状态机
*   **Active**: Redis 中存在有效的 Zai Token。
*   **Expired**: Redis 中 Key 不存在或 TTL 到期。
*   **Invalid**: API 调用返回 401。

### 3.2 自动刷新流程 (Active Refresh)
后台任务 (`APScheduler` 或 `asyncio.create_task`) 每分钟运行一次：
1.  遍历 SQLite 中所有 `is_active=True` 的账号。
2.  检查 Redis 中对应的 Zai Token TTL。
3.  如果 TTL < 600 秒 (10分钟) 或 Key 不存在：
    *   触发刷新逻辑：调用 `zai_token.py` (封装为函数调用)。
    *   成功：更新 Redis Token，重置 TTL。
    *   失败：记录错误到 SQLite，如果是认证失败则标记 `is_active=False`。

### 3.3 被动失效处理 (Passive Invalidation)
1.  API 网关发起请求收到 `401 Unauthorized`。
2.  立即删除 Redis 中的 Token Key。
3.  抛出 HTTP 503 Service Unavailable (提示客户端重试，或网关内部自动重试一次)。

## 4. API 路由与逻辑 (API Logic)

### Endpoint: `POST /v1/chat/completions`

**流程:**
1.  **Select Token**: 从 Redis 中获取所有可用的 Zai Token Keys。
2.  **Filter**: 过滤掉 `zai:limit:{hash}` 存在的 Token (即正在冷却中的 Token)。
3.  **Load Balance**: 随机或轮询选择一个可用 Token。如果无可用 Token，返回 429 Too Many Requests。
4.  **Lock**: 立即为选中的 Token 设置 `zai:limit:{hash}`，TTL = 60s。
5.  **Transform**:
    *   将 OpenAI `messages` 转换为 Zai 的 Chat History 结构。
    *   生成新的 UUID 作为 Chat ID。
6.  **Execute**: 使用 `httpx` 发起流式请求。
7.  **Response Handling**:
    *   **If stream=True**: 解析 Zai 的 SSE 事件，实时转换为 OpenAI chunk 格式并 yield。
    *   **If stream=False**: 接收完整流，拼接 `content`，构造标准 JSON 响应返回。

## 5. 目录结构 (Directory Structure)

```text
zai.is2api/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 应用入口，路由注册
│   ├── core/
│   │   ├── config.py        # 环境变量配置 (Redis URL等)
│   │   └── events.py        # 启动/关闭事件 (初始化DB, 启动后台任务)
│   ├── db/
│   │   ├── session.py       # SQLite 连接
│   │   └── redis.py         # Redis 连接池
│   ├── models/
│   │   └── account.py       # SQLite ORM 模型
│   ├── schemas/
│   │   ├── openai.py        # Pydantic 模型 (OpenAI 请求/响应)
│   │   └── zai.py           # Pydantic 模型 (Zai 内部结构)
│   ├── services/
│   │   ├── auth_service.py  # 封装 zai_token.py 的逻辑
│   │   ├── token_manager.py # Token 池管理, 刷新逻辑
│   │   └── zai_api.py       # Zai.is API 客户端 (请求转换, 发送)
│   └── workers/
│       └── refresh_task.py  # 定时刷新任务
├── scripts/
│   └── zai_token.py         # [现有资源] Discord 登录脚本
├── data/                    # 挂载目录
│   └── zai_gateway.db       # SQLite 数据库文件
├── plans/                   # 设计文档
├── .env.example
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## 6. WebUI 设计 (WebUI Design)

为了方便管理账号和监控系统状态，系统集成了一个轻量级的 Web 管理界面。

### 6.1 页面功能
1.  **Dashboard (仪表盘)**:
    *   查看当前系统状态 (Redis 连接状态、Worker 状态)。
    *   统计信息 (活跃账号数、今日请求数等)。
2.  **Account Management (账号管理)**:
    *   添加新的 Discord Token。
    *   查看账号列表 (ID, 状态, 最后错误信息)。
    *   删除/禁用账号。
    *   手动触发 Token 刷新。
3.  **Chat Playground (对话测试)**:
    *   一个简易的聊天界面，直接调用 `/v1/chat/completions` 接口测试可用性。

### 6.2 技术选型
*   **Frontend**: HTML5 + TailwindCSS (CDN) + Alpine.js (CDN) - 纯静态单页应用，无构建步骤。
*   **Backend Serving**: FastAPI `StaticFiles` 挂载。
*   **Templates**: Jinja2 (用于注入初始配置，如 API Base URL)。

### 6.3 路由
*   `GET /`: 主界面 (Dashboard)。
*   `GET /ui/static/{path}`: 静态资源 (CSS/JS)。

## 7. 下一步行动 (Next Steps)

1.  确认以上设计是否符合需求。
2.  开始搭建项目骨架。
3.  优先实现 `Token Manager` 和后台刷新机制。
4.  开发 WebUI 并集成。
