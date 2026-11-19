import os
from dataclasses import dataclass


def _database_url() -> str:
    """Hosted databases (Neon, Vercel Marketplace) hand out postgres:// or postgresql:// URLs;
    SQLAlchemy needs the driver name, so add it."""
    url = os.getenv("DATABASE_URL", "sqlite:///./zayqora.db")
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@dataclass(frozen=True)
class Settings:
    app_name: str = "ZAYQORA"
    # SQLite works with zero setup. For PostgreSQL use e.g.
    # postgresql+psycopg://user:pass@localhost:5432/zayqora
    database_url: str = _database_url()
    secret_key: str = os.getenv("SECRET_KEY", "dev-only-change-me")
    access_token_minutes: int = int(os.getenv("ACCESS_TOKEN_MINUTES", "480"))
    environment: str = os.getenv("ENVIRONMENT", "development")  # set to "production" when deployed
    # Email (optional). Without SMTP_HOST, emails are stored in the notification log only.
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("SMTP_FROM", "ZAYQORA <no-reply@localhost>")
    smtp_starttls: bool = os.getenv("SMTP_STARTTLS", "1") != "0"


    def validate_for_environment(self) -> None:
        """Refuse to start in production with a weak or default signing key (spec 17.3)."""
        if self.environment == "production" and (
            self.secret_key == "dev-only-change-me" or len(self.secret_key) < 32
        ):
            raise RuntimeError(
                "SECRET_KEY must be set to a random value of at least 32 characters in production"
            )


settings = Settings()
