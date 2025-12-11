from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Body, Response, Cookie
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, or_, delete
from pydantic import BaseModel

from app.core.config import settings
from app.db.session import get_db
from app.db.redis import get_redis
from app.models.account import Account
from app.models.log import RequestLog

router = APIRouter()

# --- Auth ---

@router.post("/login")
async def login(response: Response, password: str = Body(..., embed=True)):
    if password == "admin": # Default hardcoded password
        response.set_cookie(key="admin_session", value="authenticated", httponly=True)
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="Invalid password")

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("admin_session")
    return {"status": "success"}

async def verify_admin(admin_session: str | None = Cookie(None)):
    if admin_session != "authenticated":
        raise HTTPException(status_code=401, detail="Not authenticated")

# --- Stats ---

@router.get("/stats", dependencies=[Depends(verify_admin)])
async def get_stats(db: AsyncSession = Depends(get_db)):
    account_count = await db.scalar(select(func.count(Account.id)))
    active_account_count = await db.scalar(select(func.count(Account.id)).where(Account.is_active == True))
    request_count = await db.scalar(select(func.count(RequestLog.id)))
    
    redis = await get_redis()
    # Simple estimation of zai tokens (scan might be slow if many keys, but here likely few)
    # Using keys is blocking, but for admin panel with low load it's acceptable for now.
    # A better way is to maintain a set of tokens.
    keys = await redis.keys("zai:token:*")
    active_tokens = len(keys)
    
    return {
        "total_accounts": account_count,
        "active_accounts": active_account_count,
        "active_zai_tokens": active_tokens,
        "total_requests": request_count
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

@router.get("/config", dependencies=[Depends(verify_admin)])
async def get_config():
    return {
        "PROJECT_NAME": settings.PROJECT_NAME,
        "DATABASE_URL": settings.DATABASE_URL, # Mask if sensitive
        "ZAI_BASE_URL": settings.ZAI_BASE_URL,
        "TOKEN_REFRESH_INTERVAL": settings.TOKEN_REFRESH_INTERVAL,
        "ZAI_TOKEN_TTL": settings.ZAI_TOKEN_TTL
    }

# --- Zai Tokens ---

@router.get("/zai-tokens", dependencies=[Depends(verify_admin)])
async def get_zai_tokens():
    redis = await get_redis()
    keys = await redis.keys("zai:token:*")
    tokens = []
    for k in keys:
        ttl = await redis.ttl(k)
        tokens.append({
            "key": k.decode("utf-8"),
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
async def create_account(account: AccountCreate, db: AsyncSession = Depends(get_db)):
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
    return new_account

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
