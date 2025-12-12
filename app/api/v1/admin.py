import hashlib
import hmac
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Body, Response, Cookie, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, or_, delete
from pydantic import BaseModel

from app.core.config import settings
from app.db.session import get_db
from app.db.redis import get_redis
from app.models.account import Account
from app.models.log import RequestLog
from app.models.system import SystemConfig, ApiKey
from app.services.token_manager import get_token_hash, get_zai_stats_key

router = APIRouter()

# --- Auth ---

SESSION_SALT = b"admin-session"

def _get_admin_session_token() -> str:
    if not settings.ADMIN_API_KEY:
        raise HTTPException(status_code=500, detail="Admin key not configured")
    return hmac.new(
        settings.ADMIN_API_KEY.encode("utf-8"),
        SESSION_SALT,
        hashlib.sha256,
    ).hexdigest()

@router.post("/login")
async def login(response: Response, password: str = Body(..., embed=True)):
    expected = settings.ADMIN_API_KEY
    if not expected:
        raise HTTPException(status_code=500, detail="Admin key not configured")
    if hmac.compare_digest(password, expected):
        session_token = _get_admin_session_token()
        response.set_cookie(
            key="admin_session",
            value=session_token,
            httponly=True,
            samesite="lax",
        )
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="Invalid password")

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("admin_session")
    return {"status": "success"}

async def verify_admin(admin_session: str | None = Cookie(None)):
    if not admin_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    expected_token = _get_admin_session_token()
    if not hmac.compare_digest(admin_session, expected_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

# --- Stats ---

@router.get("/stats", dependencies=[Depends(verify_admin)])
async def get_stats(db: AsyncSession = Depends(get_db)):
    # Basic Stats
    account_count = await db.scalar(select(func.count(Account.id)))
    active_account_count = await db.scalar(select(func.count(Account.id)).where(Account.is_active))
    request_count = await db.scalar(select(func.count(RequestLog.id)))
    
    # Model Usage Stats (Top 5)
    model_stats_stmt = (
        select(RequestLog.model, func.count(RequestLog.id))
        .group_by(RequestLog.model)
        .order_by(func.count(RequestLog.id).desc())
        .limit(5)
    )
    model_stats_result = await db.execute(model_stats_stmt)
    model_usage = [{"model": r[0], "count": r[1]} for r in model_stats_result.all()]

    # Zai Token Stats (Active Tokens and their success/failure)
    redis = await get_redis()
    active_tokens = 0
    token_stats = []
    
    # Get all active accounts to map stats
    stmt = select(Account).where(Account.is_active == True)
    result = await db.execute(stmt)
    accounts = result.scalars().all()

    for account in accounts:
        token_hash = get_token_hash(account.discord_token)
        stats_key = get_zai_stats_key(token_hash)
        
        # Check if active in Redis (optional, but good to know)
        # exists = await redis.exists(get_zai_token_key(token_hash))
        
        stats = await redis.hgetall(stats_key)
        success = int(stats.get("success", 0))
        failure = int(stats.get("failure", 0))
        
        if success > 0 or failure > 0:
            token_stats.append({
                "account_id": account.id,
                "token_preview": account.discord_token[:10] + "...",
                "success": success,
                "failure": failure
            })
            
    # Count redis active tokens simply
    async for _ in redis.scan_iter(match="zai:token:*", count=100):
        active_tokens += 1
    
    return {
        "total_accounts": account_count,
        "active_accounts": active_account_count,
        "active_zai_tokens": active_tokens,
        "total_requests": request_count,
        "model_usage": model_usage,
        "token_stats": token_stats
    }

# --- Logs ---

class RequestLogResponse(BaseModel):
    id: int
    timestamp: Any
    model: str
    status_code: int
    duration_ms: float
    error_message: str | None

    class Config:
        from_attributes = True

@router.get("/logs", response_model=list[RequestLogResponse], dependencies=[Depends(verify_admin)])
async def get_logs(
    limit: int = 50, 
    offset: int = 0, 
    search: str | None = None,
    only_errors: bool = False,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(RequestLog).order_by(desc(RequestLog.timestamp))
    
    if search:
        search_filter = or_(
            RequestLog.model.ilike(f"%{search}%"),
            RequestLog.chat_id.ilike(f"%{search}%"),
            RequestLog.error_message.ilike(f"%{search}%")
        )
        stmt = stmt.where(search_filter)
        
    if only_errors:
        stmt = stmt.where(RequestLog.status_code != 200)

    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.delete("/logs", dependencies=[Depends(verify_admin)])
async def clear_logs(db: AsyncSession = Depends(get_db)):
    await db.execute(delete(RequestLog)) # Requires import delete
    await db.commit()
    return {"status": "success", "message": "Logs cleared"}


# --- Config ---

class ConfigUpdate(BaseModel):
    retry_count: int

@router.post("/config", dependencies=[Depends(verify_admin)])
async def update_config(config: ConfigUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(SystemConfig).where(SystemConfig.key == "retry_count")
    result = await db.execute(stmt)
    sys_config = result.scalar_one_or_none()
    
    if sys_config:
        sys_config.value = str(config.retry_count)
    else:
        sys_config = SystemConfig(key="retry_count", value=str(config.retry_count))
        db.add(sys_config)
    
    await db.commit()
    return {"retry_count": config.retry_count}

@router.get("/config", dependencies=[Depends(verify_admin)])
async def get_config(db: AsyncSession = Depends(get_db)):
    # Get dynamic config
    retry_count_val = await db.scalar(select(SystemConfig.value).where(SystemConfig.key == "retry_count"))
    retry_count = int(retry_count_val) if retry_count_val else settings.ZAI_RETRY_COUNT

    return {
        "PROJECT_NAME": settings.PROJECT_NAME,
        "DATABASE_URL": settings.DATABASE_URL,
        "ZAI_BASE_URL": settings.ZAI_BASE_URL,
        "TOKEN_REFRESH_INTERVAL": settings.TOKEN_REFRESH_INTERVAL,
        "ZAI_TOKEN_TTL": settings.ZAI_TOKEN_TTL,
        "retry_count": retry_count
    }

# --- API Keys ---

class ApiKeyCreate(BaseModel):
    name: str

class ApiKeyResponse(BaseModel):
    id: int
    key: str
    name: str | None
    is_active: bool
    created_at: Any

    class Config:
        from_attributes = True

@router.get("/api-keys", response_model=list[ApiKeyResponse], dependencies=[Depends(verify_admin)])
async def get_api_keys(db: AsyncSession = Depends(get_db)):
    stmt = select(ApiKey).order_by(desc(ApiKey.created_at))
    result = await db.execute(stmt)
    return result.scalars().all()

@router.post("/api-keys", response_model=ApiKeyResponse, dependencies=[Depends(verify_admin)])
async def create_api_key(key_data: ApiKeyCreate, db: AsyncSession = Depends(get_db)):
    new_key_str = f"sk-zai-{uuid.uuid4()}"
    new_key = ApiKey(key=new_key_str, name=key_data.name)
    db.add(new_key)
    await db.commit()
    await db.refresh(new_key)
    return new_key

@router.delete("/api-keys/{key_id}", dependencies=[Depends(verify_admin)])
async def delete_api_key(key_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(ApiKey).where(ApiKey.id == key_id)
    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()
    
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key not found")
        
    await db.delete(api_key)
    await db.commit()
    return {"status": "success"}

# --- Zai Tokens ---

@router.get("/zai-tokens", dependencies=[Depends(verify_admin)])
async def get_zai_tokens():
    redis = await get_redis()
    tokens = []
    async for key in redis.scan_iter(match="zai:token:*", count=100):
        ttl = await redis.ttl(key)
        tokens.append({
            "key": key,
            "ttl": ttl
        })
    return tokens

# --- Accounts ---

class AccountCreate(BaseModel):
    discord_token: str

class AccountResponse(BaseModel):
    id: int
    # discord_token: str # SECURITY: This field is commented out to prevent leaking sensitive credentials.
    is_active: bool
    last_error: str | None = None

    class Config:
        from_attributes = True

@router.post("/accounts", response_model=AccountResponse, dependencies=[Depends(verify_admin)])
async def create_account(account: AccountCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    # Check if exists
    stmt = select(Account).where(Account.discord_token == account.discord_token)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(status_code=400, detail="Token already registered")
    
    new_account = Account(discord_token=account.discord_token)
    db.add(new_account)
    await db.commit()
    await db.refresh(new_account)

    # Trigger token refresh immediately
    from app.services.token_manager import refresh_account_token
    # Since refresh_account_token needs a session, and we are in async context,
    # we can try to do it here or via background task.
    # But background task needs a new session.
    # Let's try to do it in background task wrapper
    background_tasks.add_task(trigger_initial_refresh, new_account.id)

    return new_account

async def trigger_initial_refresh(account_id: int):
    # We need to import SessionLocal here to avoid circular imports if defined at top
    from app.db.session import SessionLocal
    from app.services.token_manager import refresh_account_token
    
    async with SessionLocal() as session:
        stmt = select(Account).where(Account.id == account_id)
        result = await session.execute(stmt)
        account = result.scalar_one_or_none()
        if account:
            await refresh_account_token(session, account)

@router.get("/accounts", response_model=list[AccountResponse], dependencies=[Depends(verify_admin)])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    stmt = select(Account)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.delete("/accounts/{account_id}", dependencies=[Depends(verify_admin)])
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Account).where(Account.id == account_id)
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    await db.delete(account)
    await db.commit()
    return {"status": "success", "message": "Account deleted"}
