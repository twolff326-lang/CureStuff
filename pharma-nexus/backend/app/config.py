from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://pharma_nexus:pharma_nexus@localhost:5432/pharma_nexus"
    database_url_sync: str = "postgresql+psycopg2://pharma_nexus:pharma_nexus@localhost:5432/pharma_nexus"
    cors_origins: str = "http://localhost:3000"
    app_env: str = "development"
    app_debug: bool = True

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
