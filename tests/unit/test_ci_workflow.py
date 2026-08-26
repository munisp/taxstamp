"""Regression checks for material local CI gate coverage."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_ci_covers_all_checked_polyglot_modules() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert {"quality", "image", "go-gateway-policy", "rust-ledger-boundary", "mobile-foundation"} <= set(jobs)

    go_steps = str(jobs["go-gateway-policy"]["steps"])
    assert "go test -race ./..." in go_steps
    assert "go vet ./..." in go_steps

    rust_steps = str(jobs["rust-ledger-boundary"]["steps"])
    assert "cargo clippy --all-targets -- -D warnings" in rust_steps
    assert "cargo test" in rust_steps

    mobile_steps = str(jobs["mobile-foundation"]["steps"])
    assert "pnpm install --frozen-lockfile" in mobile_steps
    assert "pnpm run typecheck" in mobile_steps
    assert "pnpm audit --prod --audit-level=low" in mobile_steps

    quality_steps = str(jobs["quality"]["steps"])
    assert "make package" in quality_steps


def test_ci_does_not_track_unreviewed_action_branches() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "@main" not in workflow
    assert "@master" not in workflow
