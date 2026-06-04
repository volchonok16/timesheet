#!/usr/bin/env bash
# mateplace.ru (Timesheet) + pallink.fun (Ganta/Roadmap) на одном VPS.
#
# Nginx pallink — только из репозитория Ganta (deploy/nginx/pallink.conf).
# Nginx mateplace — из этого репозитория (deploy/nginx/mateplace.conf).
#
#   cd /var/www/timesheet && sudo bash deploy/fix-coexist.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

warn() { echo "!!  $*" >&2; }

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Запустите: sudo bash deploy/fix-coexist.sh" >&2
    exit 1
  fi
}

need_root

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  source .env 2>/dev/null || true
  set +a
fi

find_ganta_root() {
  local d
  for d in /var/www/ganta /var/www/roadmap /var/www/pallink /opt/ganta; do
    if [[ -f "$d/deploy/nginx/pallink.conf" ]]; then
      echo "$d"
      return 0
    fi
  done
  return 1
}

echo "==> 1. Убираем default_server (mateplace не должен попадать на pallink)"
for f in /etc/nginx/sites-enabled/* /etc/nginx/sites-available/*; do
  [[ -f "$f" ]] || continue
  sed -i \
    -e 's/listen \(.*\) default_server/listen \1/g' \
    -e 's/default_server//g' \
    "$f"
done

echo "==> 2. Pallink — nginx из Ganta (8000 / 5173), не из Timesheet"
GANTA_ROOT=""
if GANTA_ROOT="$(find_ganta_root)"; then
  echo "    Ganta: $GANTA_ROOT"
  mkdir -p /etc/nginx/snippets /var/www/certbot
  cp -f "$GANTA_ROOT/deploy/nginx/snippets/proxy-common.conf" /etc/nginx/snippets/ 2>/dev/null || true
  cp -f "$GANTA_ROOT/deploy/nginx/snippets/ssl-pallink.conf" /etc/nginx/snippets/
  if [[ -f /etc/letsencrypt/live/pallink.fun/fullchain.pem ]]; then
    cp -f "$GANTA_ROOT/deploy/nginx/pallink.conf" /etc/nginx/sites-available/pallink.conf
  else
    cp -f "$GANTA_ROOT/deploy/nginx/pallink.certbot-bootstrap.conf" /etc/nginx/sites-available/pallink.conf
    echo "!!  Нет SSL pallink.fun — после DNS: cd $GANTA_ROOT && sudo bash deploy/apply-production.sh"
  fi
  ln -sf /etc/nginx/sites-available/pallink.conf /etc/nginx/sites-enabled/pallink.conf

  echo "==> 3. Ganta Docker (pallink.fun)"
  (cd "$GANTA_ROOT" && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build) || \
    warn "Ganta compose не поднялся — проверьте $GANTA_ROOT"
else
  echo "!!  Ganta не найден (ожидался /var/www/ganta или /var/www/roadmap)"
  echo "    Восстановите pallink вручную: proxy_pass 127.0.0.1:5173 и :8000"
  PALLINK="/etc/nginx/sites-available/pallink.conf"
  if [[ -f "$PALLINK" ]]; then
    sed -i 's/127.0.0.1:32573/127.0.0.1:5173/g; s/127.0.0.1:32080/127.0.0.1:8000/g' "$PALLINK"
  fi
fi

echo "==> 4. Timesheet — только mateplace.conf (31080 / 31573)"
bash "$ROOT/deploy/deploy.sh"

if [[ ! -f "/etc/letsencrypt/live/${TIMESHEET_DOMAIN:-mateplace.ru}/fullchain.pem" ]]; then
  echo "!!  Нет SSL mateplace.ru:"
  echo "    cd $ROOT && sudo bash deploy/deploy.sh --issue-ssl"
fi

nginx -t
systemctl reload nginx

echo ""
echo "==> Проверка upstream"
curl -sf --connect-timeout 2 "http://127.0.0.1:${TIMESHEET_BACKEND_PORT:-31080}/api/health" && echo "mateplace API OK" || echo "mateplace API DOWN"
curl -sf --connect-timeout 2 "http://127.0.0.1:8000/api/health" && echo "pallink API OK" || echo "pallink API DOWN"
curl -sf --connect-timeout 2 -o /dev/null "http://127.0.0.1:5173/" && echo "pallink UI OK" || echo "pallink UI DOWN"

echo ""
echo "==> Nginx proxy_pass"
grep -h 'proxy_pass' /etc/nginx/sites-enabled/mateplace.conf 2>/dev/null || true
grep -h 'proxy_pass' /etc/nginx/sites-enabled/pallink.conf 2>/dev/null || true

echo ""
echo "Готово: https://mateplace.ru (Timesheet) | https://pallink.fun (Ganta)"
