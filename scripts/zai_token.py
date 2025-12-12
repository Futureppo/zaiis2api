#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zAI Token 获取工具
纯后端 Discord OAuth 登录
命令行用法示例：python zai_token.py backend-login --discord-token "你的discord token"
"""

import base64
import json
import argparse
import requests
import re
import logging
from typing import Optional, Dict, Any
from urllib.parse import urlparse, parse_qs

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DiscordOAuthHandler:
    """Discord OAuth 登录处理器"""
    
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
            包含 zai.is JWT token 的字典
        """
        if not discord_token or len(discord_token) < 20:
             return {'error': '无效的 Discord Token'}

        logger.info("[*] 开始后端 OAuth 登录流程...")
        logger.info(f"[*] Discord Token: {discord_token[:20]}...{discord_token[-10:]}")
        
        try:
            # Step 1: 访问 OAuth 登录入口，获取 Discord 授权 URL
            logger.info("[1/5] 获取 Discord 授权 URL...")
            oauth_info = self._get_discord_authorize_url()
            if 'error' in oauth_info:
                return oauth_info
            
            authorize_url = oauth_info['authorize_url']
            client_id = oauth_info['client_id']
            redirect_uri = oauth_info['redirect_uri']
            state = oauth_info.get('state', '')
            scope = oauth_info.get('scope', 'identify email')
            
            logger.info(f"    Client ID: {client_id}")
            logger.info(f"    Redirect URI: {redirect_uri}")
            logger.info(f"    Scope: {scope}")
            
            # Step 2: 使用 Discord token 授权应用
            logger.info("[2/5] 授权应用...")
            auth_result = self._authorize_discord_app(
                discord_token, client_id, redirect_uri, scope, state
            )
            if 'error' in auth_result:
                return auth_result
            
            callback_url = auth_result['callback_url']
            logger.info(f"    获取到回调 URL")
            
            # Step 3: 访问回调 URL 获取 token
            logger.info("[3/5] 处理 OAuth 回调...")
            token_result = self._handle_oauth_callback(callback_url)
            if 'error' in token_result:
                return token_result
            
            logger.info(f"[4/5] 成功获取 JWT Token!")
            
            return token_result
            
        except requests.exceptions.RequestException as e:
            logger.error(f"登录网络请求出错: {str(e)}")
            return {'error': f'登录网络请求出错: {str(e)}'}
        except Exception as e:
            logger.error(f"登录过程出错: {str(e)}")
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
        except requests.exceptions.RequestException as e:
            return {'error': f'获取授权 URL 失败 (网络错误): {str(e)}'}
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
                except json.JSONDecodeError:
                    pass
            
            return {'error': f'授权失败 (状态码: {response.status_code})'}
            
        except requests.exceptions.RequestException as e:
            return {'error': f'授权过程出错 (网络错误): {str(e)}'}
        except Exception as e:
            return {'error': f'授权过程出错: {str(e)}'}
    
    def _handle_oauth_callback(self, callback_url: str) -> Dict[str, Any]:
        """处理 OAuth 回调，获取 JWT token"""
        try:
            logger.info(f"    回调 URL: {callback_url[:80]}...")
            
            response = self.session.get(callback_url, allow_redirects=False)
            
            max_redirects = 10
            for i in range(max_redirects):
                logger.info(f"    重定向 {i+1}: 状态码 {response.status_code}")
                
                if response.status_code not in [301, 302, 303, 307, 308]:
                    break
                
                location = response.headers.get('Location', '')
                logger.info(f"    Location: {location[:100]}...")
                
                # Check for token in URL
                token = self._extract_token(location)
                if token: return {'token': token}
                
                if location.startswith('/'):
                    location = f"{self.base_url}{location}"
                
                response = self.session.get(location, allow_redirects=False)
            
            # Final check in URL
            final_url = response.url if hasattr(response, 'url') else ''
            logger.info(f"    最终 URL: {final_url}")
            logger.info(f"    最终状态码: {response.status_code}")
            
            token = self._extract_token(final_url)
            if token: return {'token': token}
            
            # Check Cookies
            logger.info(f"    检查 Cookies...")
            has_session = False
            for cookie in self.session.cookies:
                logger.debug(f"      {cookie.name}: {str(cookie.value)[:50]}...")
                if cookie.name == 'token':
                    return {'token': cookie.value}
                if any(x in cookie.name.lower() for x in ['session', 'auth', 'id', 'user']):
                    has_session = True
            
            # Session Fallback
            if has_session:
                logger.info(f"    [!] 尝试 Session 验证...")
                user_info = self._verify_session()
                if user_info and not user_info.get('error'):
                    logger.info(f"    [+] Session 验证成功！用户: {user_info.get('name', 'Unknown')}")
                    # Try to extract actual JWT from session if possible, but currently API relies on token
                    # If we only have session cookie, we might need to change how ZaiClient works to use cookies
                    # But the user asked to convert discord token to jwt token.
                    # If we can't find a JWT token but have session, we might need to warn.
                    
                    # For now, return SESSION_AUTH indicator, but ideally we want the JWT.
                    # Usually 'token' cookie IS the JWT.
                    return {'token': 'SESSION_AUTH', 'user_info': user_info, 'cookies': self.session.cookies.get_dict()}

            return {'error': '未能从回调中获取 token'}
            
        except requests.exceptions.RequestException as e:
             return {'error': f'处理回调失败 (网络错误): {str(e)}'}
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
        except requests.exceptions.RequestException: pass
        except Exception: pass
        return None

def main():
    parser = argparse.ArgumentParser(description='zAI Token 获取工具')
    subparsers = parser.add_subparsers(dest='command')
    
    # Only keep backend-login
    backend_parser = subparsers.add_parser('backend-login', help='后端登录')
    backend_parser.add_argument('--discord-token', required=True, help='Discord Token')
    backend_parser.add_argument('--url', default='https://zai.is', help='Base URL')
    
    args = parser.parse_args()
    
    if args.command == 'backend-login':
        handler = DiscordOAuthHandler(args.url)
        result = handler.backend_login(args.discord_token)
        
        if 'error' in result:
            logger.error(f"\n[!] 登录失败: {result['error']}")
        else:
            logger.info(f"\n[+] 登录成功!\n")
            
            token = result.get('token')
            if token == 'SESSION_AUTH':
                # Try to extract a real token from user_info if present, else just show a message
                user_info = result.get('user_info', {})
                logger.info(f"\n[Session Cookie Authentication Active]")
                logger.info(f"User: {user_info.get('name')} ({user_info.get('email')})")
                logger.info(f"ID: {user_info.get('id')}")
            else:
                logger.info(f"\n{token}\n")

if __name__ == '__main__':
    main()