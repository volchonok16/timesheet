#!/usr/bin/env bash
# Чинит два сайта на одном VPS: mateplace.ru + pallink.fun
#   cd /var/www/timesheet && sudo bash deploy/fix-coexist.sh
#
# - mateplace → nginx 31080/31573, без редиректа на pallink
# - pallink → nginx 8000/5173, убирает default_server и :32573

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

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

echo "==> 1. Убираем default_server (из-за него mateplace уезжает на pallink)"
for f in /etc/nginx/sites-enabled/* /etc/nginx/sites-available/*; do
  [[ -f "$f" ]] || continue
  sed -i \
    -e 's/listen \(.*\) default_server/listen \1/g' \
    -e 's/default_server//g' \
    "$f"
done

echo "==> 2. Pallink nginx → 8000 / 5173 (не 32573)"
PALLINK="/etc/nginx/sites-available/pallink.conf"
if [[ -f "$PALLINK" ]]; then
  sed -i \
    -e 's/127.0.0.1:32573/127.0.0.1:5173/g' \
    -e 's/127.0.0.1:32080/127.0.0.1:8000/g' \
    "$PALLINK"
  ln -sf "$PALLINK" /etc/nginx/sites-enabled/pallink.conf
else
  echo "!!  Нет $PALLINK — скопируйте deploy/nginx/pallink.coexist.example.conf"
fi

echo "==> 3. Timesheet nginx + Docker (mateplace.ru → 31080 / 31573)"
bash "$ROOT/deploy/deploy.sh"

echo "==> 4. Проверка SSL mateplace (без сертификата HTTPS уйдёт на чужой vhost)"
if [[ ! -f "/etc/letsencrypt/live/${TIMESHEET_DOMAIN:-mateplace.ru}/fullchain.pem" ]]; then
  echo "!!  Нет SSL для mateplace.ru — выпустите:"
  echo "    sudo bash deploy/deploy.sh --issue-ssl"
fi

echo "==> 5. Pallink Docker (вручную, если 502 останется)"
echo "    cd /var/www/pallink && docker compose up -d"
echo "    curl -sI http://127.0.0.1:5173/ | head -3"

echo ""
echo "==> 6. Тест upstream"
curl -sf --connect-timeout 2 "http://127.0.0.1:${TIMESHEET_BACKEND_PORT:-31080}/api/health" && echo "timesheet API OK" || echo "timesheet API DOWN"
curl -sf --connect-timeout 2 -o /dev/null "http://127.0.0.1:5173/" && echo "pallink frontend OK" || echo "pallink frontend DOWN — docker compose up в /var/www/pallink"

echo ""
echo "==> 7. Проверка Host (с сервера)"
curl -sI --resolve "mateplace.ru:443:127.0.0.1" "https://mateplace.ru/" -k 2>/dev/null | head -8 || true
curl -sI --resolve "pallink.fun:443:127.0.0.1" "https://pallink.fun/" -k 2>/dev/null | head -8 || true

echo ""
echo "Готово. В браузере: https://mateplace.ru и https://pallink.fun"
