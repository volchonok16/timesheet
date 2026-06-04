#!/usr/bin/env bash
# Быстрая диагностика 502 (nginx не видит upstream).
#   cd /var/www/timesheet && sudo bash deploy/diagnose.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  source .env 2>/dev/null || true
  set +a
fi

BACKEND_PORT="${TIMESHEET_BACKEND_PORT:-31080}"
FRONTEND_PORT="${TIMESHEET_FRONTEND_PORT:-31573}"

echo "=== Timesheet .env ports: backend=${BACKEND_PORT} frontend=${FRONTEND_PORT} ==="
echo ""

echo "=== Docker (timesheet) ==="
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps -a 2>/dev/null || docker ps -a | head -20
echo ""

echo "=== Порты на 127.0.0.1 ==="
check_port() {
  local port=$1
  local label=$2
  if curl -sf --connect-timeout 2 "http://127.0.0.1:${port}/" -o /dev/null 2>/dev/null \
    || curl -sf --connect-timeout 2 "http://127.0.0.1:${port}/api/health" -o /dev/null 2>/dev/null; then
    echo "OK  :${port}  ${label}"
  else
    echo "DOWN:${port}  ${label}"
  fi
}

check_port "$BACKEND_PORT" "timesheet API (ожидается для mateplace)"
check_port "$FRONTEND_PORT" "timesheet frontend"
check_port 32080 "pallink API (если настроен 32xxx)"
check_port 32573 "pallink frontend"
check_port 8000 "старый API"
check_port 5173 "старый Vite"
echo ""

echo "=== Backend health (timesheet) ==="
curl -sf --connect-timeout 3 "http://127.0.0.1:${BACKEND_PORT}/api/health" && echo "" || echo "НЕ ОТВЕЧАЕТ на :${BACKEND_PORT}"
echo ""

echo "=== Nginx upstream в конфигах ==="
grep -rh 'proxy_pass http://127.0.0.1' /etc/nginx/sites-enabled/ 2>/dev/null || true
echo ""

echo "=== Последние ошибки nginx ==="
tail -15 /var/log/nginx/error.log 2>/dev/null || echo "(нет доступа к error.log)"
echo ""

echo "=== Рекомендация при 502 ==="
echo "1) cd $ROOT && sudo bash deploy/deploy.sh"
echo "2) Порты в nginx = порты из «docker ps» (TIMESHEET_*_PORT в .env)"
echo "3) pallink: proxy_pass должен указывать на РЕАЛЬНЫЕ порты контейнеров pallink"
echo "4) sudo nginx -t && sudo systemctl reload nginx"
