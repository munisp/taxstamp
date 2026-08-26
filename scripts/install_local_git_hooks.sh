#!/usr/bin/env bash
# Opt-in repository-local hook setup. It changes only this clone's Git config.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
chmod +x .githooks/pre-commit
git config core.hooksPath .githooks
echo "Installed Taxstamp local hooks at $repo_root/.githooks"
echo "The hook validates synthetic fixtures only; it does not inspect protected deployment evidence."
