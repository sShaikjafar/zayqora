from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

if settings.database_url.startswith("sqlite"):
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
else:
    # Serverless-friendly: small pool, drop dead connections, and no server-side prepared statements
    # (poolers such as Neon's PgBouncer do not support them).
    engine = create_engine(
        settings.database_url, pool_size=2, max_overflow=3, pool_recycle=300, pool_pre_ping=True,
        connect_args={"prepare_threshold": None} if settings.database_url.startswith("postgresql+psycopg") else {},
    )
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
