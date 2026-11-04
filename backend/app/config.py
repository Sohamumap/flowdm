from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/manychat"
    secret_key: str = "change-me"
    public_base_url: str = "http://localhost:8080"
    environment: str = "development"
    # Folder with the built dashboard (frontend/dist). Optional: Caddy serves it in Docker.
    static_dir: str = ""

    # Dashboard admin (created on startup if missing)
    admin_email: str = "admin@example.com"
    admin_password: str = "change-me"
    session_days: int = 14

    # Instagram app (Meta App Dashboard -> Instagram -> API setup with Instagram login)
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    # Optional: the Meta app secret (App settings -> Basic). Webhook signatures are accepted
    # if they match either secret, since Meta's docs and real deliveries disagree on which one.
    meta_app_secret: str = ""
    webhook_verify_token: str = "change-me"
    graph_api_version: str = "v26.0"
    graph_base_url: str = "https://graph.instagram.com"

    # Worker
    worker_poll_seconds: float = 0.5
    worker_batch_size: int = 10
    job_max_attempts: int = 5
    webhook_event_retention_days: int = 14
    # How often to look for the post an automation is waiting for
    next_post_check_seconds: float = 120

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/api/instagram/oauth/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
