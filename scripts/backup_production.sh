#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/production.py backup "$@"
