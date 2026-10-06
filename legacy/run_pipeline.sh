#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
exec uv run --group audio python scripts/13_run_case_pipeline.py "$@"
