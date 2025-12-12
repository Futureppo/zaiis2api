# 自动刷新 Token 推送到 NewAPI

轻量化版本，定期获取 zAI 平台的访问令牌，并将其推送到 NewAPI 中。

## 配置文件结构

```json
{
  "discord_tokens": [
    "discord_token1",
    "discord_token2"
  ],
  "discord_token_file": "discord_tokens.txt",
  "zai_url": "https://zai.is",
  "newapi_base": "https://aaa.aaa.cn",
  "newapi_key": "aaa",
  "system_token": "aaa",
  "newapi_user_id": "1",
  "newapi_channel_id": "1",
  "expires_in": 3600,
  "update_interval": 3600
}


```

### 配置项详细说明

#### 1. NewAPI 相关配置

- **`newapi_url`**：NewAPI 地址
  - 示例：`https://api.yoursite.com`

- **`newapi_key`**：NewAPI 管理员密钥


#### 2. Discord 账号配置

- **`discord_accounts`**：Discord 账号token列表，可以配置多个账号

#### 3. 运行配置

- **`refresh_interval`**：刷新间隔（单位：秒）
  - 默认值：`3600`（1小时）
  - 说明：每隔多少秒刷新一次 Token

### 配置示例

完整的配置文件示例：

```json
{
    "newapi_url": "https://api.example.com",
    "newapi_key": "sk-xxxxxxxxxxxxxxxxxxxxx",
    "discord_accounts": [
        {
            "email": "user1@example.com",
            "password": "password123",
            "channel_id": "1"
        },
        {
            "email": "user2@example.com",
            "password": "password456",
            "channel_id": "2"
        }
    ],
    "refresh_interval": 3600,
    "run_once": false
}
```

## 使用方法

配置完成后，在项目文件夹中打开命令行工具，运行：

```bash
python zai_token.py run-loop --config config.json
```

## 📄 许可证

本项目仅供学习和研究使用，请遵守相关服务条款。