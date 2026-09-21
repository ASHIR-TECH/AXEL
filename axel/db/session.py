"""
Database session and engine management.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from axel.core.config import settings
from axel.db.base import Base


def create_db_engine(db_url: str | None = None):
    """Create SQLAlchemy engine with appropriate dialect arguments."""
    url = db_url or settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(
        url,
        connect_args=connect_args,
        echo=(settings.log_level.upper() == "DEBUG"),
    )


engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db(bind_engine=None) -> None:
    """Create all tables in the database (useful for SQLite/dev/tests)."""
    target_engine = bind_engine or engine
    Base.metadata.create_all(bind=target_engine)


def get_db() -> Generator[Session, None, None]:
    """Dependency for obtaining a database session with auto-close."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
