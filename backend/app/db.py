"""Database engine + session (sync SQLAlchemy 2.0 over psycopg3).

Sync is chosen deliberately: FastAPI runs sync route handlers in a threadpool,
the inline Gemini call is the real latency cost, and sync sessions make the
``SET LOCAL`` / RLS GUC handling far simpler to reason about than async.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


# create_engine does not open a connection here — connections are lazy, so the
# app module imports cleanly even without a running Postgres.
engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def get_db():
    """FastAPI dependency yielding a session that is always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
