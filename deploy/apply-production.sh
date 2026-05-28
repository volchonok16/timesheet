#!/usr/bin/env bash
# Применяет nginx + Docker для TFS Timesheet (mateplace.ru) из репозитория.
# Запуск на VPS:
#   cd /var/www/timesheet && git pull && sudo bash deploy/apply-production.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DOMAIN="${TIMESHEET_DOMAIN:-mateplace.ru}"
API_DOMAIN="${TIMESHEET_API_DOMAIN:-api.mateplace.ru}"

echo "==> Проект: $ROOT"
echo "==> Домен: $DOMAIN, API: $API_DOMAIN"

if ! command -v docker >/dev/null 2>&1; then
  echo "Ошибка: docker не установлен." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "==> Запуск Docker…"
  systemctl start docker.socket docker.service 2>/dev/null || true
  sleep 2
  if ! docker info >/dev/null 2>&1; then
    echo "Ошибка: Docker daemon недоступен (проверьте systemctl status docker)." >&2
    exit 1
  fi
fi

if [[ ! -f .env ]]; then
  if [[ -f .env.production.example ]]; then
    cp .env.production.example .env
    echo "==> Создан .env из .env.production.example (при необходимости отредактируйте TFS_*)."
  else
    echo "Предупреждение: нет .env — используются значения по умолчанию из compose." >&2
  fi
fi

echo "==> Docker Compose (prod)…"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

echo "==> Nginx…"
if ! command -v nginx >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx
fi

mkdir -p /var/www/certbot
mkdir -p /etc/nginx/snippets

substitute_domain() {
  sed -e "s/mateplace.ru/${DOMAIN}/g" \
      -e "s/api.mateplace.ru/${API_DOMAIN}/g"
}

substitute_domain < "$ROOT/deploy/nginx/snippets/ssl-mateplace.conf" > /etc/nginx/snippets/ssl-mateplace.conf
cp -f "$ROOT/deploy/nginx/snippets/proxy-common.conf" /etc/nginx/snippets/

CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
if [[ -f "$CERT_DIR/fullchain.pem" && -f "$CERT_DIR/privkey.pem" ]]; then
  echo "==> SSL-сертификат найден — HTTPS-конфиг."
  substitute_domain < "$ROOT/deploy/nginx/mateplace.conf" > /etc/nginx/sites-available/mateplace.conf
else
  echo "==> Сертификата нет — временный HTTP-конфиг (для certbot)."
  substitute_domain < "$ROOT/deploy/nginx/mateplace.certbot-bootstrap.conf" > /etc/nginx/sites-available/mateplace.conf
  echo "    После DNS выполните:"
  echo "    sudo apt install -y certbot"
  echo "    sudo certbot certonly --webroot -w /var/www/certbot -d ${DOMAIN} -d www.${DOMAIN} -d ${API_DOMAIN}"
  echo "    sudo bash $ROOT/deploy/apply-production.sh"
fi

ln -sf /etc/nginx/sites-available/mateplace.conf /etc/nginx/sites-enabled/mateplace.conf
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl enable nginx 2>/dev/null || true
systemctl reload nginx

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow OpenSSH 2>/dev/null || true
  ufw allow 'Nginx Full' 2>/dev/null || ufw allow 80/tcp 443/tcp 2>/dev/null || true
fi

echo ""
echo "==> Статус контейнеров"
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

echo ""
echo "==> Ожидание backend (до 30 с)…"
for i in $(seq 1 15); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "OK: $(curl -sf http://127.0.0.1:8000/api/health)"
    break
  fi
  if [[ "$i" -eq 15 ]]; then
    echo "Предупреждение: backend не отвечает на :8000 — docker compose logs backend" >&2
  fi
  sleep 2
done

echo ""
echo "==> Проверка frontend (Vite)"
HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: localhost' http://127.0.0.1:5173/ || echo '000')"
echo "HTTP $HTTP_CODE на :5173 (ожидается 200)"

if [[ -f "$CERT_DIR/fullchain.pem" ]]; then
  echo ""
  echo "==> Проверка HTTPS"
  curl -sI --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/" | head -5
  curl -sf --resolve "${API_DOMAIN}:443:127.0.0.1" "https://${API_DOMAIN}/api/health" && echo "" || true
fi

echo ""
echo "Готово. Сайт: https://${DOMAIN}  API: https://${API_DOMAIN}"
