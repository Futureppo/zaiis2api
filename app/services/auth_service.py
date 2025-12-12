import asyncio
import sys
import os
from typing import Dict, Any

from scripts.zai_token import DiscordOAuthHandler

async def login_with_discord_token(discord_token: str) -> Dict[str, Any]:
    """
    Asynchronously exchange Discord Token for Zai Access Token.
    """
    def _login():
        # Use the updated DiscordOAuthHandler which returns dict with 'token'
        handler = DiscordOAuthHandler()
        return handler.backend_login(discord_token)
    
    # Run blocking synchronous code in a separate thread
    return await asyncio.to_thread(_login)