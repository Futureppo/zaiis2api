import json
import uuid
import time
import logging
import httpx
from typing import List, AsyncGenerator, Dict, Any

from app.core.config import settings
from app.schemas.openai import Message

logger = logging.getLogger(__name__)

class ZaiAuthError(Exception):
    """Raised when Zai API returns 401 Unauthorized"""
    pass

class ZaiAPIError(Exception):
    """Raised when Zai API returns other errors"""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Zai API returned {status_code}: {message}")

class ZaiClient:
    _client: httpx.AsyncClient = None

    @classmethod
    def get_client(cls) -> httpx.AsyncClient:
        if cls._client is None or cls._client.is_closed:
            cls._client = httpx.AsyncClient(timeout=120.0)
        return cls._client

    @classmethod
    async def close_client(cls):
        if cls._client and not cls._client.is_closed:
            await cls._client.aclose()
            cls._client = None

    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": settings.ZAI_BASE_URL,
            "Referer": f"{settings.ZAI_BASE_URL}/",
        }

    def _build_payload(self, messages: List[Message], model: str) -> Dict[str, Any]:
        # Generate a chat UUID
        chat_id = str(uuid.uuid4())
        
        # We need to construct a linked list of messages
        history_messages = {}
        message_list = []
        
        parent_id = None
        current_id = None
        
        # 1. Process existing history + user new message
        # NOTE: Since we are a stateless gateway, we treat all 'messages' from OpenAI request
        # as the full history to be sent to Zai.
        
        for idx, msg in enumerate(messages):
            msg_id = str(uuid.uuid4())
            timestamp = int(time.time())
            
            # OpenAI roles: system, user, assistant
            # Zai roles: user, assistant (system might need to be prepended to user or handled specially)
            role = msg.role
            if role == "system":
                role = "user" # Fallback or merge with first user message
            
            message_obj = {
                "id": msg_id,
                "parentId": parent_id,
                "childrenIds": [],
                "role": role,
                "content": msg.content,
                "timestamp": timestamp,
                "models": [model] if role == "user" else None
            }
            
            if role == "assistant":
                 message_obj["model"] = model
                 message_obj["modelName"] = model # Simplified
                 message_obj["modelIdx"] = 0
            
            # Update parent's children
            if parent_id and parent_id in history_messages:
                history_messages[parent_id]["childrenIds"].append(msg_id)
            
            history_messages[msg_id] = message_obj
            message_list.append(message_obj)
            
            parent_id = msg_id
            current_id = msg_id

        # 2. Add the empty assistant response placeholder (target for generation)
        response_id = str(uuid.uuid4())
        timestamp = int(time.time())
        
        response_obj = {
            "parentId": parent_id,
            "id": response_id,
            "childrenIds": [],
            "role": "assistant",
            "content": "",
            "model": model,
            "modelName": model,
            "modelIdx": 0,
            "timestamp": timestamp
        }
        
        if parent_id and parent_id in history_messages:
            history_messages[parent_id]["childrenIds"].append(response_id)
            
        history_messages[response_id] = response_obj
        message_list.append(response_obj)
        current_id = response_id

        payload = {
            "chat": {
                "models": [model],
                "history": {
                    "messages": history_messages,
                    "currentId": current_id
                },
                "messages": message_list,
                "params": {},
                "files": []
            }
        }
        return payload, chat_id

    async def stream_chat(self, messages: List[Message], model: str) -> AsyncGenerator[str, None]:
        payload, chat_id = self._build_payload(messages, model)
        url = f"{settings.ZAI_BASE_URL}/api/v1/chats/{chat_id}"
        
        # Update Referer with the specific chat ID
        self.headers["Referer"] = f"{settings.ZAI_BASE_URL}/c/{chat_id}"

        client = self.get_client()
        try:
            async with client.stream("POST", url, json=payload, headers=self.headers) as response:
                if response.status_code != 200:
                    error_bytes = await response.aread()
                    error_text = error_bytes.decode('utf-8', errors='ignore')
                    logger.error(f"Zai API Error: {response.status_code} - {error_text}")
                    if response.status_code == 401:
                        raise ZaiAuthError("401 Unauthorized")
                    raise ZaiAPIError(response.status_code, error_text)

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            # Zai format usually sends partial content or updates
                            # We need to check actual response format. 
                            # Based on typical SSE, we expect content deltas.
                            # Assuming OpenWebUI format:
                            # {"token": "...", "content": "...", "done": false}
                            
                            # Let's assume standard 'content' field in data
                            content = data.get("content", "") or data.get("token", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            logger.error(f"Stream error: {e}")
            raise e
