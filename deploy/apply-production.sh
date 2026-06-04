#!/usr/bin/env bash
# Обратная совместимость — вызывает единый deploy/deploy.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec bash "$ROOT/deploy.sh" "$@"
