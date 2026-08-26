"""Supply-chain regression checks for the Python build configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_build_backend_dependency_is_exactly_pinned() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    build_system = project["build-system"]
    assert build_system["requires"] == ["setuptools==84.0.0"]
    assert build_system["build-backend"] == "setuptools.build_meta"


def test_generated_migration_lint_exceptions_remain_narrow() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    per_file_ignores = project["tool"]["ruff"]["lint"]["per-file-ignores"]
    assert per_file_ignores["migrations/*"] == ["E501", "PLR0915"]
