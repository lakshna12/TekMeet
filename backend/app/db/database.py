"""SQLAlchemy database engine and session configuration for TekMeet."""

import logging
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

db_url = settings.get_database_url()

# Handle SQLite connect args for multi-thread safety
connect_args = {}
if db_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    db_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Generator:
    """FastAPI Dependency for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initialize database tables defined in Base metadata."""
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("[Database] Successfully initialized database tables.")
    except Exception as exc:
        logger.warning("[Database] Could not auto-create database tables on startup: %s", exc)
