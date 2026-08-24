"""Database engine, session management, and transaction helpers."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.config import Settings

_ADVISORY_NAMESPACE: Final[int] = 0x7A5_1000


class LockKey:
    """Stable advisory-lock identifiers used to serialise critical sections."""

    AUDIT_CHAIN = _ADVISORY_NAMESPACE + 1
    RECONCILIATION = _ADVISORY_NAMESPACE + 2


def create_db_engine(settings: Settings) -> Engine:
    engine = create_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1_800,
        future=True,
        connect_args={"application_name": settings.service_name},
    )

    statement_timeout = settings.database_statement_timeout_ms

    @event.listens_for(engine, "connect")
    def _set_session_defaults(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f"SET statement_timeout = {statement_timeout}")
            cursor.execute("SET idle_in_transaction_session_timeout = 60000")
            cursor.execute("SET TIME ZONE 'UTC'")

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def transaction(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Run a unit of work in one database transaction, rolling back on any error."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def serializable_transaction(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transaction at SERIALIZABLE isolation, for multi-row invariants."""
    session = session_factory()
    try:
        session.connection(execution_options={"isolation_level": "SERIALIZABLE"})
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def advisory_xact_lock(session: Session, key: int, discriminator: int = 0) -> None:
    """Take a transaction-scoped advisory lock; released automatically at commit."""
    session.execute(text("SELECT pg_advisory_xact_lock(:k, :d)"), {"k": key, "d": discriminator})


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
