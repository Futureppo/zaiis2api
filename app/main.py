import logging
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager

from app.core.config import settings
from app.db.session import engine, Base
from app.workers.refresh_task import start_scheduler, scheduler
from app.api.v1 import chat, admin
from app.services.zai_client import ZaiClient
# Import models to register them with Base
from app.models.account import Account
from app.models.log import RequestLog

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Creating database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("Starting background tasks...")
    start_scheduler()
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    await ZaiClient.close_client()
    scheduler.shutdown()

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

templates = Jinja2Templates(directory="app/templates")

app.include_router(chat.router, prefix=settings.API_V1_STR, tags=["chat"])
app.include_router(admin.router, prefix=settings.API_V1_STR, tags=["admin"])

@app.get("/", include_in_schema=False)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health")
async def health_check():
    return {"status": "ok"}