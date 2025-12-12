#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zAI Token 获取并推送到newapi
纯后端 Discord OAuth 登录
命令行用法示例：python zai_token.py run-loop --config config.json
"""

import base64
import json
import argparse
import os
import requests
import re
import time
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, parse_qs

class DiscordOAuthHandler:
    """Discord OAuth 登录处理"""
    
    # Discord API 端点
    DISCORD_API_BASE = "https://discord.com/api/v9"
    
    def __init__(self, base_url: str = "https://zai.is"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': f'{base_url}/auth',
            'Origin': base_url,
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
        })
    
    def get_oauth_login_url(self) -> str:
        """获取 Discord OAuth 登录 URL"""
        return f"{self.base_url}/oauth/discord/login"
    
    def backend_login(self, discord_token: str) -> Dict[str, Any]:
        """
        纯后端 Discord OAuth 登录
        
        Args:
            discord_token: Discord 账号的 token
            
        Returns:
            zai.is JWT token 
        """
        if not discord_token or len(discord_token) < 20:
             return {'error': '无效的 Discord Token'}

        print("\n[*] 开始后端 OAuth 登录流程...")
        print(f"[*] Discord Token: {discord_token[:20]}...{discord_token[-10:]}")
        
        try:
            # Step 1: 访问 OAuth 登录入口，获取 Discord 授权 URL
            print("[1/5] 获取 Discord 授权 URL...")
            oauth_info = self._get_discord_authorize_url()
            if 'error' in oauth_info:
                return oauth_info
            
            authorize_url = oauth_info['authorize_url']
            client_id = oauth_info['client_id']
            redirect_uri = oauth_info['redirect_uri']
            state = oauth_info.get('state', '')
            scope = oauth_info.get('scope', 'identify email')
            
            print(f"    Client ID: {client_id}")
            print(f"    Redirect URI: {redirect_uri}")
            print(f"    Scope: {scope}")
            
            # Step 2: 使用 Discord token 授权应用
            print("[2/5] 授权应用...")
            auth_result = self._authorize_discord_app(
                discord_token, client_id, redirect_uri, scope, state
            )
            if 'error' in auth_result:
                return auth_result
            
            callback_url = auth_result['callback_url']
            print(f"    获取到回调 URL")
            
            # Step 3: 访问回调 URL 获取 token
            print("[3/5] 处理 OAuth 回调...")
            token_result = self._handle_oauth_callback(callback_url)
            if 'error' in token_result:
                return token_result
            
            print(f"[4/5] 成功获取 JWT Token!")
            
            return token_result
            
        except Exception as e:
            return {'error': f'登录过程出错: {str(e)}'}
    
    def _get_discord_authorize_url(self) -> Dict[str, Any]:
        """获取 Discord 授权 URL 和参数"""
        try:
            response = self.session.get(
                self.get_oauth_login_url(),
                allow_redirects=False
            )
            
            if response.status_code in [301, 302, 303, 307, 308]:
                location = response.headers.get('Location', '')
                if 'discord.com' in location:
                    parsed = urlparse(location)
                    params = parse_qs(parsed.query)
                    return {
                        'authorize_url': location,
                        'client_id': params.get('client_id', [''])[0],
                        'redirect_uri': params.get('redirect_uri', [''])[0],
                        'scope': params.get('scope', ['identify email'])[0],
                        'state': params.get('state', [''])[0]
                    }
            return {'error': f'无法获取授权 URL，状态码: {response.status_code}'}
        except Exception as e:
            return {'error': f'获取授权 URL 失败: {str(e)}'}
    
    def _authorize_discord_app(self, discord_token, client_id, redirect_uri, scope, state) -> Dict[str, Any]:
        """使用 Discord token 授权应用"""
        try:
            authorize_url = f"{self.DISCORD_API_BASE}/oauth2/authorize"
            
            # 构建 super properties 
            super_properties = base64.b64encode(json.dumps({
                "os": "Windows",
                "browser": "Chrome",
                "device": "",
                "browser_user_agent": self.session.headers['User-Agent'],
            }).encode()).decode()
            
            headers = {
                'Authorization': discord_token,
                'Content-Type': 'application/json',
                'X-Super-Properties': super_properties,
            }
            
            params = {
                'client_id': client_id,
                'response_type': 'code',
                'redirect_uri': redirect_uri,
                'scope': scope,
            }
            if state:
                params['state'] = state
            
            payload = {
                'permissions': '0',
                'authorize': True,
                'integration_type': 0
            }
            
            response = self.session.post(
                authorize_url,
                headers=headers,
                params=params,
                json=payload
            )
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    location = data.get('location', '')
                    if location:
                        if location.startswith('/'):
                            location = f"{self.base_url}{location}"
                        return {'callback_url': location}
                except:
                    pass
            
            return {'error': f'授权失败 (状态码: {response.status_code})'}
            
        except Exception as e:
            return {'error': f'授权过程出错: {str(e)}'}
    
    def _handle_oauth_callback(self, callback_url: str) -> Dict[str, Any]:
        """处理 OAuth 回调，获取 JWT token"""
        try:
            print(f"    回调 URL: {callback_url[:80]}...")
            
            response = self.session.get(callback_url, allow_redirects=False)
            
            max_redirects = 10
            for i in range(max_redirects):
                print(f"    重定向 {i+1}: 状态码 {response.status_code}")
                
                if response.status_code not in [301, 302, 303, 307, 308]:
                    break
                
                location = response.headers.get('Location', '')
                print(f"    Location: {location[:100]}...")
                
                # Check for token in URL
                token = self._extract_token(location)
                if token: return {'token': token}
                
                if location.startswith('/'):
                    location = f"{self.base_url}{location}"
                
                response = self.session.get(location, allow_redirects=False)
            
            # Final check in URL
            final_url = response.url if hasattr(response, 'url') else ''
            print(f"    最终 URL: {final_url}")
            print(f"    最终状态码: {response.status_code}")
            
            token = self._extract_token(final_url)
            if token: return {'token': token}
            
            # Check Cookies
            print(f"    检查 Cookies...")
            has_session = False
            for cookie in self.session.cookies:
                print(f"      {cookie.name}: {str(cookie.value)[:50]}...")
                if cookie.name == 'token':
                    return {'token': cookie.value}
                if any(x in cookie.name.lower() for x in ['session', 'auth', 'id', 'user']):
                    has_session = True
            
            # Session Fallback
            if has_session:
                print(f"    [!] 尝试 Session 验证...")
                user_info = self._verify_session()
                if user_info and not user_info.get('error'):
                    print(f"    [+] Session 验证成功！用户: {user_info.get('name', 'Unknown')}")
                    return {'token': 'SESSION_AUTH', 'user_info': user_info}

            return {'error': '未能从回调中获取 token'}
            
        except Exception as e:
            return {'error': f'处理回调失败: {str(e)}'}

    def _extract_token(self, input_str: str) -> Optional[str]:
        if '#token=' in input_str:
            match = re.search(r'#token=([^&\s]+)', input_str)
            if match: return match.group(1)
        if '?token=' in input_str:
            match = re.search(r'[?&]token=([^&\s]+)', input_str)
            if match: return match.group(1)
        return None

    def _verify_session(self) -> Optional[Dict]:
        try:
            resp = self.session.get(f"{self.base_url}/api/v1/auths/", headers={'Accept': 'application/json'})
            if resp.status_code == 200: return resp.json()
        except: pass
        return None


class NewAPITokenManager:
    """
    NewAPI 渠道 token 管理
    参考接口文档: https://apifox.newapi.ai/llms.txt
    接口路径: /api/channel/
    """

    def __init__(self, base_url: str, api_key: str, user_id: str = "1"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers.update({
            "New-Api-User": str(user_id),
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def get_channel(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """获取渠道信息 GET /api/channel/{id}"""
        url = f"{self.base_url}/api/channel/{channel_id}"
        resp = self.session.get(url)
        if resp.status_code == 200:
            try:
                data = resp.json()
                if isinstance(data, dict):
                    if "data" in data:
                        return data.get("data")
                    return data
            except Exception as exc:
                print(f"[NewAPI] 解析渠道信息失败: {exc}")
        else:
            print(f"[NewAPI] 获取渠道失败: {resp.status_code} {resp.text}")
        return None

    def update_channel(self, channel_data: Dict[str, Any]) -> bool:
        """更新渠道 PUT /api/channel/"""
        url = f"{self.base_url}/api/channel/"
        resp = self.session.put(url, json=channel_data)
        if resp.status_code in (200, 201):
            print(f"[NewAPI] 更新渠道成功")
            return True
        print(f"[NewAPI] 更新渠道失败: {resp.status_code} {resp.text}")
        return False

    def get_channel_keys(self, channel_id: str) -> List[str]:
        """获取渠道当前的 key 列表"""
        channel = self.get_channel(channel_id)
        if not channel:
            return []
        key_str = channel.get("key", "")
        if not key_str:
            return []
        return [k.strip() for k in key_str.split("\n") if k.strip()]

    def clear_channel_tokens(self, channel_id: str) -> None:
        """清空渠道的所有 key"""
        channel = self.get_channel(channel_id)
        if not channel:
            print("[NewAPI] 无法获取渠道信息，跳过清理")
            return

        old_keys = channel.get("key", "")
        if not old_keys or not old_keys.strip():
            print("[NewAPI] 渠道当前无 key")
            return

        key_count = len([k for k in old_keys.split("\n") if k.strip()])
        print(f"[NewAPI] 准备清空渠道 {key_count} 个旧 key ...")

        channel["key"] = ""
        if self.update_channel(channel):
            print(f"[NewAPI] 已清空渠道旧 key")
        else:
            print(f"[NewAPI] 清空渠道旧 key 失败")

    def push_tokens(self, channel_id: str, tokens: List[str]) -> bool:
        """推送多个 token 到渠道（替换原有 key）"""
        channel = self.get_channel(channel_id)
        if not channel:
            print("[NewAPI] 无法获取渠道信息，推送失败")
            return False

        channel["key"] = "\n".join(tokens)
        if self.update_channel(channel):
            print(f"[NewAPI] 推送 {len(tokens)} 个 token 成功")
            return True
        return False

    def create_token(self, channel_id: str, token: str, expires_in: int = 10800) -> bool:
        """添加单个 token（追加到现有 key）"""
        channel = self.get_channel(channel_id)
        if not channel:
            print("[NewAPI] 无法获取渠道信息，推送失败")
            return False

        old_key = channel.get("key", "") or ""
        # 追加新 token
        if old_key.strip():
            channel["key"] = old_key.strip() + "\n" + token
        else:
            channel["key"] = token

        if self.update_channel(channel):
            print(f"[NewAPI] 推送 token 成功: {token[:8]}...{token[-6:]}")
            return True
        print(f"[NewAPI] 推送 token 失败")
        return False


def _load_discord_tokens(single_token: Optional[str] = None,
                         token_file: Optional[str] = None,
                         token_list: Optional[List[str]] = None) -> List[str]:
    tokens: List[str] = []
    if token_list:
        for t in token_list:
            if t:
                tokens.append(t.strip())
    if single_token:
        tokens.append(single_token.strip())
    if token_file and os.path.exists(token_file):
        with open(token_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    tokens.append(line)
    uniq: List[str] = []
    seen = set()
    for t in tokens:
        if len(t) < 20:
            continue
        if t in seen:
            continue
        uniq.append(t)
        seen.add(t)
    return uniq


def _load_config(config_path: str) -> Dict[str, Any]:
    if not os.path.exists(config_path):
        print(f"[!] 未找到配置文件: {config_path}")
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"[!] 读取配置失败: {exc}")
        return {}


def convert_and_push(discord_tokens: List[str], zai_url: str, newapi_base: str, newapi_key: str, channel_id: str, expires_in: int, user_id: str = "1") -> None:
    if not discord_tokens:
        print("[!] 未提供 Discord Token，跳过本轮")
        return

    print(f"[*] 将处理 {len(discord_tokens)} 个 Discord Token")
    handler = DiscordOAuthHandler(zai_url)

    zai_tokens: List[str] = []
    for idx, d_token in enumerate(discord_tokens, start=1):
        print(f"\n==== {idx}/{len(discord_tokens)} 开始转换 ====")
        res = handler.backend_login(d_token)
        if res.get('error'):
            print(f"[!] 转换失败: {res['error']}")
            continue
        token_val = res.get('token')
        if not token_val or token_val == 'SESSION_AUTH':
            print("[!] 未获取到有效的 zAI token，已跳过")
            continue
        zai_tokens.append(token_val)
        print(f"[+] 转换成功，获得 token: {token_val[:12]}...{token_val[-8:]}")

    if not zai_tokens:
        print("[!] 所有 Discord Token 均转换失败，停止推送")
        return

    manager = NewAPITokenManager(newapi_base, newapi_key, user_id)
    print(f"\n[*] 开始推送 {len(zai_tokens)} 个新 token 到渠道 {channel_id} (将替换旧 key)")
    
    # 一次性替换渠道的所有 key
    if manager.push_tokens(channel_id, zai_tokens):
        print(f"\n[+] 推送完成，成功 {len(zai_tokens)}/{len(zai_tokens)}")
    else:
        print(f"\n[!] 推送失败")

def main():
    parser = argparse.ArgumentParser(description='zAI Token 获取工具')
    subparsers = parser.add_subparsers(dest='command')
    backend_parser = subparsers.add_parser('backend-login', help='后端登录')
    backend_parser.add_argument('--discord-token', required=True, help='Discord Token')
    backend_parser.add_argument('--url', default='https://zai.is', help='Base URL')
    batch_parser = subparsers.add_parser('batch-push', help='批量转换 Discord token 并推送到 NewAPI 渠道')
    batch_parser.add_argument('--discord-token', action='append', help='Discord Token，可重复')
    batch_parser.add_argument('--discord-token-file', help='包含 Discord Token 的文件，一行一个')
    batch_parser.add_argument('--url', default='https://zai.is', help='zAI Base URL')
    batch_parser.add_argument('--newapi-base', default='https://api.newapi.ai', help='NewAPI 基础 URL')
    batch_parser.add_argument('--newapi-key', required=True, help='NewAPI 管理密钥 (Bearer)')
    batch_parser.add_argument('--newapi-channel-id', required=True, help='NewAPI 渠道 ID')
    batch_parser.add_argument('--expires-in', type=int, default=10800, help='新 token 有效期（秒），默认 10800 秒=3 小时')

    # 读取 JSON 配置并循环运行
    loop_parser = subparsers.add_parser('run-loop', help='读取 JSON 配置并循环转换+推送')
    loop_parser.add_argument('--config', default='config.json', help='配置文件路径，JSON 格式')
    
    args = parser.parse_args()
    
    if args.command == 'backend-login':
        handler = DiscordOAuthHandler(args.url)
        result = handler.backend_login(args.discord_token)
        
        if 'error' in result:
            print(f"\n[!] 登录失败: {result['error']}")
        else:
            print(f"\n[+] 登录成功!\n")
            
            token = result.get('token')
            if token == 'SESSION_AUTH':
                user_info = result.get('user_info', {})
                print(f"\n[Session Cookie Authentication Active]")
                print(f"User: {user_info.get('name')} ({user_info.get('email')})")
                print(f"ID: {user_info.get('id')}")
            else:
                print(f"\n{token}\n")
    elif args.command == 'batch-push':
        discord_tokens = _load_discord_tokens(args.discord_token, args.discord_token_file)
        if not discord_tokens:
            print("[!] 未提供有效的 Discord Token，请使用 --discord-token 或 --discord-token-file")
            return

        convert_and_push(
            discord_tokens=discord_tokens,
            zai_url=args.url,
            newapi_base=args.newapi_base,
            newapi_key=args.newapi_key,
            channel_id=args.newapi_channel_id,
            expires_in=args.expires_in
        )
    elif args.command == 'run-loop':
        config_path = args.config
        print(f"[*] 使用配置文件循环运行: {config_path}")
        try:
            while True:
                cfg = _load_config(config_path)
                if not cfg:
                    break

                discord_tokens = _load_discord_tokens(
                    single_token=None,
                    token_file=cfg.get("discord_token_file"),
                    token_list=cfg.get("discord_tokens") or cfg.get("discord_token")
                )
                zai_url = cfg.get("zai_url") or cfg.get("zai_base_url") or "https://zai.is"
                newapi_base = cfg.get("newapi_base") or cfg.get("newapi_base_url") or "https://91vip.futureppo.top"
                newapi_key = cfg.get("newapi_key") or cfg.get("system_token") or cfg.get("access_token")
                channel_id = cfg.get("newapi_channel_id") or cfg.get("channel_id")
                user_id = str(cfg.get("newapi_user_id") or cfg.get("user_id") or "1")
                expires_in = int(cfg.get("expires_in", 10800))
                interval = int(cfg.get("update_interval", cfg.get("interval", 3600)))

                if not newapi_key or not channel_id:
                    print("[!] 配置缺少 newapi_key 或 newapi_channel_id，退出")
                    break

                convert_and_push(
                    discord_tokens=discord_tokens,
                    zai_url=zai_url,
                    newapi_base=newapi_base,
                    newapi_key=newapi_key,
                    channel_id=channel_id,
                    expires_in=expires_in,
                    user_id=user_id
                )
                print(f"\n[*] 本轮结束，{interval} 秒后再次执行 ...\n")
                time.sleep(max(1, interval))
        except KeyboardInterrupt:
            print("\n[!] 已停止循环运行")
    else:
        parser.print_help()

if __name__ == '__main__':
    main()