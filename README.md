# Zai.is API Gateway

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.104.1-green.svg)](https://fastapi.tiangolo.com/)

这是一个将 [zai.is](https://zai.is) 封装为 OpenAI 兼容 API 的私有网关，支持多账号轮询、自动刷新 Token、严格限流等功能。

## 功能特性

*   **OpenAI 兼容接口**: 完全兼容 OpenAI API 格式，支持流式 (stream=True) 和非流式响应
*   **多账号轮询**: 支持添加多个 Discord Token，自动维护 Zai Access Token 池，实现负载均衡
*   **自动刷新**: 后台任务自动刷新即将过期的 Token，确保服务持续可用
*   **严格限流**: 内置 Redis 分布式锁，严格控制每个 Token 1 RPM (请求/分钟)，防止被封禁
*   **Web 管理界面**: 提供直观的 Web UI 用于管理账号、查看日志、配置系统参数
*   **Docker 部署**: 一键 Docker Compose 启动，支持快速部署和扩展
*   **API 密钥管理**: 支持创建和管理多个 API 密钥，便于权限控制
*   **详细日志记录**: 记录所有请求日志，便于监控和问题排查
*   **模型列表接口**: 支持获取可用模型列表

## 快速开始

### 1. 环境准备

确保已安装以下软件：
*   Docker & Docker Compose
*   Git

### 2. 克隆项目

```bash
git clone https://github.com/Futureppo/zai.is2api.git
cd zai.is2api
```

### 3. 配置环境变量（可选）

复制 `.env.example` 文件并根据需要修改配置：

```bash
cp .env.example .env
# 编辑 .env 文件
```

### 4. 启动服务

```bash
docker-compose up --build -d
```

服务将在 `http://localhost:8000` 启动。

### 5. 访问管理界面

打开浏览器访问 `http://localhost:8000`，使用默认密码 `114514` 登录管理界面。

### 6. 添加账号

在管理界面中添加 Discord Token，或使用 API：

```bash
curl -X POST "http://localhost:8000/v1/accounts" \
     -H "Content-Type: application/json" \
     -d '{"discord_token": "YOUR_DISCORD_TOKEN"}'
```

### 7. 创建 API 密钥

在管理界面中创建 API 密钥，或使用 API：

```bash
curl -X POST "http://localhost:8000/v1/api-keys" \
     -H "Content-Type: application/json" \
     -d '{"name": "my-key"}'
```

### 8. 调用对话接口

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
     -H "Authorization: Bearer sk-zai-YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "gemini-3-pro-image-preview",
       "messages": [{"role": "user", "content": "Hello!"}],
       "stream": true
     }'
```

## API 接口文档

项目提供完整的 OpenAPI 文档：

*   **Swagger UI**: `http://localhost:8000/docs`
*   **ReDoc**: `http://localhost:8000/redoc`

### 主要接口

*   `POST /v1/chat/completions` - 聊天完成接口（OpenAI 兼容）
*   `GET /v1/models` - 获取可用模型列表
*   `POST /v1/accounts` - 添加 Discord 账号
*   `GET /v1/accounts` - 获取账号列表
*   `DELETE /v1/accounts/{id}` - 删除账号
*   `POST /v1/api-keys` - 创建 API 密钥
*   `GET /v1/api-keys` - 获取 API 密钥列表
*   `DELETE /v1/api-keys/{id}` - 删除 API 密钥

## 目录结构

```text
zai.is2api/
├── app/                    # 核心应用代码
│   ├── api/               # API 路由定义
│   │   └── v1/            # API v1 版本
│   │       ├── admin.py   # 管理接口（账号、配置、日志等）
│   │       └── chat.py    # 聊天接口（OpenAI 兼容）
│   ├── core/              # 核心配置
│   │   └── config.py      # 应用配置
│   ├── db/                # 数据库相关
│   │   ├── redis.py       # Redis 连接
│   │   └── session.py     # 数据库会话
│   ├── models/            # 数据库模型
│   │   ├── account.py     # 账号模型
│   │   ├── log.py         # 日志模型
│   │   └── system.py      # 系统配置模型
│   ├── schemas/           # Pydantic 数据模型
│   │   └── openai.py      # OpenAI 兼容的数据结构
│   ├── services/          # 业务逻辑
│   │   ├── auth_service.py # 认证服务
│   │   ├── token_manager.py # Token 管理
│   │   └── zai_client.py   # Zai API 客户端
│   ├── templates/         # Web UI 模板
│   │   └── index.html     # 管理界面
│   ├── workers/           # 后台任务
│   │   └── refresh_task.py # Token 刷新任务
│   ├── main.py            # 应用入口
│   └── __init__.py
├── data/                  # 数据存储目录
│   └── zai_gateway.db     # SQLite 数据库文件
├── scripts/               # 辅助脚本
│   └── zai_token.py       # Discord Token 转换工具
├── plans/                 # 设计文档
├── docker-compose.yml     # Docker Compose 配置
├── Dockerfile            # Docker 镜像构建文件
├── requirements.txt      # Python 依赖
└── README.md             # 项目说明文档
```

## 配置说明

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ADMIN_API_KEY` | `114514` | 管理界面登录密码 |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/zai_gateway.db` | 数据库连接字符串 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接字符串 |
| `ZAI_BASE_URL` | `https://zai.is` | Zai API 基础 URL |
| `TOKEN_REFRESH_INTERVAL` | `60` | Token 刷新间隔（秒） |
| `ZAI_TOKEN_TTL_BUFFER` | `600` | Token 刷新缓冲时间（秒） |
| `ZAI_TOKEN_TTL` | `10200` | Zai Token 有效期（秒，约 2 小时 50 分钟） |
| `ZAI_RETRY_COUNT` | `3` | 请求失败重试次数 |

## 架构设计

系统采用微服务风格的单体架构，核心分为三个模块：

1.  **API Gateway (接口层)**: FastAPI 实现的 REST API，处理 OpenAI 兼容请求
2.  **Token Manager (管理层)**: 管理 Discord Token 和 Zai Access Token 的转换、缓存和限流
3.  **Worker (任务层)**: 后台任务定期刷新即将过期的 Token

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

## 开发指南

### 本地开发

1.  安装依赖：
    ```bash
    pip install -r requirements.txt
    ```

2.  启动 Redis：
    ```bash
    docker run -d -p 6379:6379 redis:alpine
    ```

3.  启动应用：
    ```bash
    uvicorn app.main:app --reload
    ```

### 代码结构

*   使用 FastAPI 框架实现 REST API
*   使用 SQLAlchemy ORM 进行数据库操作
*   使用 Redis 进行 Token 缓存和限流
*   使用 APScheduler 进行定时任务调度
*   使用 Pydantic 进行数据验证

## 注意事项

*   请确保 `scripts/zai_token.py` 与项目同步
*   默认数据库为 SQLite，存储在 `data/zai_gateway.db`
*   Redis 用于 Token 缓存和限流
*   每个 Discord Token 严格限制 1 RPM (每分钟 1 次请求)
*   建议使用多个 Discord Token 以提高并发能力
*   定期检查管理界面中的日志和错误信息

## 许可证

本项目采用 MIT 许可证，详情请见 [LICENSE](LICENSE) 文件。
