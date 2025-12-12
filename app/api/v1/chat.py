import logging
import json
import time
import uuid
from typing import AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.schemas.openai import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChunk, ChatCompletionChunkChoice, ChatCompletionChoice, Message
from app.services.token_manager import get_valid_zai_token, mark_token_invalid, increment_token_stats
from app.services.zai_client import ZaiClient
from app.db.session import get_db
from app.models.log import RequestLog
from app.models.system import ApiKey, SystemConfig
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)
security = HTTPBearer()

async def verify_api_key(credentials: HTTPAuthorizationCredentials = Security(security), db: AsyncSession = Depends(get_db)):
    token = credentials.credentials
    if not token.startswith("sk-zai-"):
         raise HTTPException(status_code=401, detail="Invalid API Key format. Key must start with 'sk-zai-'.")
         
    stmt = select(ApiKey).where(ApiKey.key == token, ApiKey.is_active == True)
    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()
    
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return api_key

async def log_request(db: AsyncSession, model: str, chat_id: str, status_code: int, duration_ms: float, error: str = None):
    try:
        log_entry = RequestLog(
            model=model,
            chat_id=chat_id,
            status_code=status_code,
            duration_ms=duration_ms,
            error_message=error
        )
        db.add(log_entry)
        await db.commit()
    except Exception as e:
        logger.error(f"Failed to save request log: {e}")

async def generate_chunks(zai_client: ZaiClient, request: ChatCompletionRequest, chat_id: str, db: AsyncSession, start_time: float, token_hash: str):
    error_msg = None
    status_code = 200
    try:
        async for content_delta in zai_client.stream_chat(request.messages, request.model):
            chunk = ChatCompletionChunk(
                id=chat_id,
                model=request.model,
                choices=[
                    ChatCompletionChunkChoice(
                        index=0,
                        delta={"content": content_delta},
                        finish_reason=None
                    )
                ]
            )
            yield f"data: {chunk.json()}\n\n"
            
        # End of stream
        chunk = ChatCompletionChunk(
            id=chat_id,
            model=request.model,
            choices=[
                ChatCompletionChunkChoice(
                    index=0,
                    delta={},
                    finish_reason="stop"
                )
            ]
        )
        yield f"data: {chunk.json()}\n\n"
        yield "data: [DONE]\n\n"
        
        # Success
        await increment_token_stats(token_hash, success=True)
        
    except Exception as e:
        logger.error(f"Stream generation error: {e}")
        error_msg = str(e)
        status_code = 500
        if "401" in str(e):
             status_code = 401
             # Trigger invalidation
             await mark_token_invalid(zai_client.token)
        
        # Failure (if not success)
        await increment_token_stats(token_hash, success=False)
        
        # We can't easily return error in SSE, usually just close stream or send error event
    finally:
        duration = (time.time() - start_time) * 1000
        await log_request(db, request.model, chat_id, status_code, duration, error_msg)

@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(verify_api_key)
):
    start_time = time.time()
    
    # Get retry count from DB
    retry_count_val = await db.scalar(select(SystemConfig.value).where(SystemConfig.key == "retry_count"))
    max_retries = int(retry_count_val) if retry_count_val else settings.ZAI_RETRY_COUNT
    
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            # 1. Get Valid Token
            result = await get_valid_zai_token(db)
            
            if not result:
                 raise HTTPException(status_code=429, detail="No available tokens or rate limit exceeded.")
            
            token, token_hash = result
            zai_client = ZaiClient(token)
            chat_id = f"chatcmpl-{uuid.uuid4()}"
            
            if request.stream:
                # For streaming, we can't easily retry if it fails mid-stream.
                # But we can try to establish connection.
                # generate_chunks needs to handle errors.
                # If we return StreamingResponse, the generator starts running.
                # If it fails immediately, we can't catch it here easily because we already returned.
                # So retry logic for streaming is limited.
                # We will only support retry for non-streaming for now, OR we need a way to check connection first.
                return StreamingResponse(
                    generate_chunks(zai_client, request, chat_id, db, start_time, token_hash),
                    media_type="text/event-stream"
                )
            else:
                # Non-streaming retry logic
                full_content = ""
                async for content_delta in zai_client.stream_chat(request.messages, request.model):
                    full_content += content_delta
                
                await increment_token_stats(token_hash, success=True)
                
                duration = (time.time() - start_time) * 1000
                await log_request(db, request.model, chat_id, 200, duration, None)
                
                return ChatCompletionResponse(
                    id=chat_id,
                    model=request.model,
                    choices=[
                        ChatCompletionChoice(
                            index=0,
                            message=Message(role="assistant", content=full_content),
                            finish_reason="stop"
                        )
                    ]
                )
                
        except Exception as e:
            last_error = e
            # Handle specific errors
            if "401" in str(e) and result:
                 token, token_hash = result
                 await mark_token_invalid(token)
                 await increment_token_stats(token_hash, success=False)
            
            logger.warning(f"Attempt {attempt + 1} failed: {e}")
            
            if attempt == max_retries:
                # Failed all retries
                status_code = 500
                if isinstance(e, HTTPException):
                    status_code = e.status_code
                elif "401" in str(e):
                    status_code = 401
                
                await log_request(db, request.model, "error", status_code, (time.time() - start_time) * 1000, str(e))
                if isinstance(e, HTTPException):
                    raise e
                raise HTTPException(status_code=status_code, detail=f"Request failed after retries: {str(e)}")
            
            # Continue to next attempt
            continue