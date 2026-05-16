from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.settings import settings

database_url = settings.resolved_metadata_database_url
engine_kwargs = {
    "pool_pre_ping": True,
    "pool_recycle": 3600,
    "future": True,
}

if database_url.startswith("sqlite:///"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(
    database_url,
    **engine_kwargs,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
