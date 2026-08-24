"""Test infrastructure.

Every fixture below uses a real PostgreSQL database created from the real Alembic
migrations and a real Redis instance. Nothing is mocked in-process: the only test double
is a local HTTP server standing in for an external registry, reached over a real socket.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.api.app import create_app
from taxstamp.clock import FixedClock
from taxstamp.config import Environment, Settings
from taxstamp.runtime import Runtime, build_runtime
from tests.support.registry_server import RegistrySandbox
from tests.support.tenant import Tenant, tenant

__all__ = ["Tenant", "tenant"]

REPO_ROOT = Path(__file__).resolve().parents[1]
ADMIN_DSN_TEMPLATE = "postgresql://{user}:{password}@{host}:{port}/postgres"
TEST_DATABASE = os.environ.get("TAXSTAMP_TEST_DATABASE", "taxstamp_test")
PG_HOST = os.environ.get("TAXSTAMP_TEST_PG_HOST", "127.0.0.1")
PG_PORT = os.environ.get("TAXSTAMP_TEST_PG_PORT", "55432")
PG_USER = os.environ.get("TAXSTAMP_TEST_PG_USER", "taxstamp")
PG_PASSWORD = os.environ.get("TAXSTAMP_TEST_PG_PASSWORD", "taxstamp")
REDIS_URL = os.environ.get("TAXSTAMP_TEST_REDIS_URL", "redis://127.0.0.1:56379/9")

SECRETS = {
    "api_token_secret": "test-api-token-secret-" + "a" * 40,
    "device_hmac_secret": "test-device-hmac-secret-" + "b" * 40,
    "payment_webhook_secret": "test-payment-webhook-secret-" + "c" * 40,
    "audit_chain_secret": "test-audit-chain-secret-" + "d" * 40,
    "export_signing_secret": "test-export-signing-secret-" + "e" * 40,
    "transparency_signing_secret": "test-transparency-signing-secret-" + "f" * 40,
}
CLOCK_START = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)


def _database_url(database: str) -> str:
    return f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{database}"


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Create the test database and bring it to head with the real migrations."""
    admin_dsn = ADMIN_DSN_TEMPLATE.format(user=PG_USER, password=PG_PASSWORD, host=PG_HOST, port=PG_PORT)
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DATABASE,)
        ).fetchone()
        if exists is None:
            connection.execute(f'CREATE DATABASE "{TEST_DATABASE}"')
    url = _database_url(TEST_DATABASE)
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(REPO_ROOT / ".venv" / "bin" / "alembic"), "upgrade", "head"],
        cwd=REPO_ROOT,
        env={**os.environ, "TAXSTAMP_DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade failed: {result.stdout}\n{result.stderr}")
    return url


@pytest.fixture(scope="session")
def registry() -> Iterator[RegistrySandbox]:
    sandbox = RegistrySandbox()
    sandbox.start()
    try:
        yield sandbox
    finally:
        sandbox.stop()


@pytest.fixture
def registry_url(registry: RegistrySandbox) -> str:
    registry.script.requests.clear()
    registry.compliant()
    return registry.base_url


@pytest.fixture
def settings(migrated_database: str, registry_url: str) -> Settings:
    return Settings(
        env=Environment.TEST,
        revision="test",
        database_url=migrated_database,
        redis_url=REDIS_URL,
        require_tls=False,
        issuance_chunk_size=100,
        outbox_batch_size=25,
        firs_base_url=registry_url,
        nafdac_base_url=registry_url,
        son_base_url=registry_url,
        customs_base_url=registry_url,
        ledger_anchor_base_url=registry_url,
        external_api_key="test-key",
        **SECRETS,
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(CLOCK_START)


@pytest.fixture
def runtime(settings: Settings, clock: FixedClock) -> Iterator[Runtime]:
    active = build_runtime(settings, clock=clock)
    _truncate(active)
    Redis.from_url(REDIS_URL).flushdb()
    try:
        yield active
    finally:
        active.close()


def _truncate(runtime: Runtime) -> None:
    with runtime.engine.begin() as connection:
        tables = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
        ]
        if tables:
            joined = ", ".join(f'"{table}"' for table in tables)
            connection.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session_factory(runtime: Runtime) -> sessionmaker[Session]:
    return runtime.session_factory


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session
        session.commit()


@pytest.fixture
def app(runtime: Runtime) -> FastAPI:
    return create_app(runtime.settings, runtime=runtime)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def idem() -> IdemKeys:
    return IdemKeys()


class IdemKeys:
    """Fresh idempotency keys, so a test never accidentally replays another's key."""

    def next(self, label: str = "key") -> str:
        return f"{label}-{uuid.uuid4().hex}"
