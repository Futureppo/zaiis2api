import logging
import json
import time
import uuid
from typing import AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.openai import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChunk, ChatCompletionChunkChoice, ChatCompletionChoice, Message
from app.services.token_manager import get_valid_zai_token, mark_token_invalid
from app.services.zai_client import ZaiClient
from app.db.session import get_db
from app.models.log import RequestLog

router = APIRouter()
logger = logging.getLogger(__name__)

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

async def generate_chunks(zai_client: ZaiClient, request: ChatCompletionRequest, chat_id: str, db: AsyncSession, start_time: float):
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
        
    except Exception as e:
        logger.error(f"Stream generation error: {e}")
        error_msg = str(e)
        status_code = 500
        if "401" in str(e):
             status_code = 401
             # Trigger invalidation
             await mark_token_invalid(zai_client.token)
        # We can't easily return error in SSE, usually just close stream or send error event
    finally:
        duration = (time.time() - start_time) * 1000
        await log_request(db, request.model, chat_id, status_code, duration, error_msg)

@router.post("/chat/completions")
async def chat_completions(request: ChatCompletionRequest, db: AsyncSession = Depends(get_db)):
    start_time = time.time()
    # 1. Get Valid Token (1 RPM limit enforced inside)
    token = await get_valid_zai_token(db)
    
    if not token:
        await log_request(db, request.model, "N/A", 429, (time.time() - start_time) * 1000, "No available tokens")
        raise HTTPException(status_code=429, detail="No available tokens or rate limit exceeded. Please try again later.")
    
    zai_client = ZaiClient(token)
    
    # Generate ID for response
    chat_id = f"chatcmpl-{uuid.uuid4()}"
    
    if request.stream:
        return StreamingResponse(
            generate_chunks(zai_client, request, chat_id, db, start_time),
            media_type="text/event-stream"
        )
    else:
        # Non-streaming: aggregate response
        full_content = ""
        error_msg = None
        status_code = 200
        try:
            async for content_delta in zai_client.stream_chat(request.messages, request.model):
                full_content += content_delta
        except Exception as e:
             error_msg = str(e)
             status_code = 500
             if "401" in str(e):
                 status_code = 401
                 await mark_token_invalid(token)
                 await log_request(db, request.model, chat_id, status_code, (time.time() - start_time) * 1000, "Upstream authentication failed")
                 raise HTTPException(status_code=401, detail="Upstream authentication failed")
             # Do not expose internal error details to client
             await log_request(db, request.model, chat_id, status_code, (time.time() - start_time) * 1000, error_msg)
             raise HTTPException(status_code=500, detail="An internal server error occurred.")
        
        duration = (time.time() - start_time) * 1000
        await log_request(db, request.model, chat_id, status_code, duration, None)
             
        response = ChatCompletionResponse(
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
        return response