> **注意**：因 zai.is 网站新增 `x-zai-darkknight` 请求头验证，当前版本的无头请求方式已失效。

# Zai2API

Zai2API 是一个功能完整的 OpenAI 兼容 API 服务网关。它允许你管理 Discord Token，自动将其转换为 zai.is 的访问凭证，并提供标准的 OpenAI 接口供第三方客户端调用。

## 轻量化版本

如果你只需要“自动刷新 Discord Token 并推送到NewAPI”这一精简能力，可以使用仓库内的 `自动刷新token推送到newapi` 目录：

- `自动刷新token推送到newapi` 为轻量化版本，专注于 Token 自动刷新与推送，适合资源受限或仅需 Token 分发的场景；

## 功能特性

- **多 Token 管理**：支持批量添加、删除、禁用 Discord Token。
- **自动保活**：后台调度器自动检测并刷新过期的 Zai Token。
- **OpenAI 兼容**：提供 `/v1/chat/completions` 和 `/v1/models` 接口。
- **负载均衡**：API 请求会自动轮询使用当前活跃的 Token。
- **WebUI 面板**：
  - **Token 列表**：实时查看 Token 状态、剩余有效期。
  - **系统配置**：修改管理员密码、API Key、代理设置、错误重试策略等。
  - **请求日志**：详细记录 API 调用的耗时、状态码和使用的 Token。
- **Docker 部署**：提供 Dockerfile 和 docker-compose.yml，一键部署。

## 快速开始

### 获取discord token

随便在一个群组中发消息，复制其中的Authorization作为discord token
![获取discord token](png/获取doscordtoken.png)

### 方式一：Docker Compose 部署（推荐）

1. 克隆或下载本项目代码。
2. 确保已安装 Docker 和 Docker Compose。
3. 在项目根目录下运行：

```bash
git clone  https://github.com/Futureppo/zai.is2api.git && cd zai.is2api
```

```bash
docker-compose up -d
```

1. 服务启动后，访问 `http://localhost:5000` 进入管理后台。

### 方式二：源码部署

1. 确保已安装 Python 3.10+。
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 启动服务：

```bash
python app.py
```

## 配置说明

### 环境变量

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `DATABASE_URI` | `sqlite:////app/instance/zai2api.db` | 数据库连接字符串 |
| `SECRET_KEY` | `your-secret-key...` | Flask Session 密钥，建议修改 |
| `TZ` | `Asia/Shanghai` | 容器时区 |

## 管理面板功能

1. **Token 管理**：
    - 点击“新增 Token”输入 Discord Token。
    - 系统会自动尝试获取 Zai Token。
    - 点击“一键刷新 ZaiToken”可强制刷新所有 Token。
2. **系统配置**：
    - 调整“错误封禁阈值”和“错误重试次数”以优化稳定性。
    - 调整 Token 刷新间隔。
3. **请求日志**：
    - 查看最近的 API 请求记录。

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Futureppo/zai.is2api&type=date&legend=top-left)](https://www.star-history.com/#Futureppo/zai.is2api&type=date&legend=top-left)

## 免责声明

本项目仅供逆向学习和研究使用。使用者应自行承担使用本工具产生的所有风险和责任。请遵守相关服务条款。
