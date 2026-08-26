"""Worker health command for container orchestration."""

from __future__ import annotations

from taxstamp.runtime import build_runtime


def main() -> int:
    runtime = build_runtime()
    try:
        return 0 if runtime.check_database() and runtime.check_redis() else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
