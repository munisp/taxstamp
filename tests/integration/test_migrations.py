"""Migrations are ordered, repeatable and reversible against a real database."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text

from taxstamp.config import Settings
from taxstamp.db import create_db_engine

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = str(REPO_ROOT / ".venv" / "bin" / "alembic")


def _alembic(command: list[str], url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [ALEMBIC, *command],
        cwd=REPO_ROOT,
        env={**os.environ, "TAXSTAMP_DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )


def test_downgrade_and_upgrade_round_trip(settings: Settings) -> None:
    url = settings.database_url
    assert _alembic(["downgrade", "base"], url).returncode == 0
    engine = create_db_engine(settings)
    with engine.connect() as connection:
        remaining = connection.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        ).scalar_one()
    assert remaining == 1  # only alembic_version survives
    assert _alembic(["upgrade", "head"], url).returncode == 0
    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        }
    engine.dispose()
    for expected in (
        "orders",
        "stamps",
        "audit_events",
        "ledger_entries",
        "outbox_messages",
        "tigerbeetle_ledger_intents",
    ):
        assert expected in tables


def test_migration_is_idempotent_at_head(settings: Settings) -> None:
    first = _alembic(["upgrade", "head"], settings.database_url)
    second = _alembic(["upgrade", "head"], settings.database_url)
    assert first.returncode == 0
    assert second.returncode == 0


def test_single_head(settings: Settings) -> None:
    heads = _alembic(["heads"], settings.database_url)
    assert heads.returncode == 0
    assert len([line for line in heads.stdout.splitlines() if "(head)" in line]) == 1
