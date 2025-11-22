import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import config


def _ensure_sqlite_dir(db_url: str):
    """Create directory for SQLite database if it doesn't exist (backward compatibility)."""
    if db_url.startswith("sqlite"):
        # Expecting formats like sqlite:///./data/app.db or sqlite:///data/app.db
        path = db_url.split("sqlite:///")[-1]
        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)


# Ensure SQLite directory exists if using SQLite
_ensure_sqlite_dir(config.DB_URL)

# Configure engine based on database type
if config.DB_URL.startswith("sqlite"):
    # SQLite-specific configuration
    connect_args = {"check_same_thread": False}
    engine = create_engine(config.DB_URL, connect_args=connect_args, future=True)
elif config.DB_URL.startswith("postgresql"):
    # PostgreSQL-specific configuration
    engine = create_engine(
        config.DB_URL,
        pool_size=10,  # Connection pool size
        max_overflow=20,  # Max overflow connections
        pool_pre_ping=True,  # Verify connections before using
        pool_recycle=3600,  # Recycle connections after 1 hour
        echo=False,  # Set to True for SQL query logging
        future=True
    )
else:
    # Generic configuration for other databases
    engine = create_engine(config.DB_URL, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

Base = declarative_base()


def init_db():
    """Create all tables if they don't exist."""
    # Import models to register them with Base
    from app.db import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def get_db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

