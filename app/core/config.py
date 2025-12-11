from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "zai.is2api"
    API_V1_STR: str = "/v1"
    ADMIN_API_KEY: str = ""
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/zai_gateway.db"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Zai.is
    ZAI_BASE_URL: str = "https://zai.is"
    
    # Token Management
    TOKEN_REFRESH_INTERVAL: int = 60  # seconds
    ZAI_TOKEN_TTL_BUFFER: int = 600   # 10 minutes buffer for refresh
    ZAI_TOKEN_TTL: int = 10200        # 2h 50m (approx 3h total)
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
