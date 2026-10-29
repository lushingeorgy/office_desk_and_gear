"""Compatibility entry point for the current isolated PostgreSQL acceptance suite."""
from test_acceptance import run_isolated


if __name__ == "__main__":
    raise SystemExit(run_isolated())
