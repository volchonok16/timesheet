#!/usr/bin/env bash
# Почему mateplace.ru уходит на pallink.fun
#   sudo bash deploy/verify-domains.sh

set -euo pipefail

DOMAIN="${TIMESHEET_DOMAIN:-mateplace.ru}"

echo "=== 1. Сертификат mateplace ==="
if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
  echo "OK  cert: /etc/letsencrypt/live/${DOMAIN}/"
else
  echo "FAIL нет SSL — nginx на 443 отдаёт другой сайт (часто pallink.fun)"
  echo "     sudo bash deploy/deploy.sh --issue-ssl"
fi

echo ""
echo "=== 2. mateplace.conf: есть ли HTTPS? ==="
if [[ -f /etc/nginx/sites-enabled/mateplace.conf ]]; then
  grep -E 'listen 443|server_name' /etc/nginx/sites-enabled/mateplace.conf | head -20 || true
else
  echo "FAIL sites-enabled/mateplace.conf отсутствует"
fi

echo ""
echo "=== 3. default_server на 443 ==="
if nginx -T 2>&1 | grep -E 'listen.*443.*default_server'; then
  echo "WARN есть default_server — уберите: sudo bash deploy/fix-coexist.sh"
else
  echo "OK  явного default_server на 443 нет"
fi

echo ""
echo "=== 4. proxy_pass (mateplace → 31080/31573, pallink → 8000/5173) ==="
grep -h 'proxy_pass' /etc/nginx/sites-enabled/mateplace.conf 2>/dev/null && echo "---" || echo "нет mateplace.conf"
grep -h 'proxy_pass' /etc/nginx/sites-enabled/pallink.conf 2>/dev/null || echo "нет pallink.conf"

echo ""
echo "=== 5. Ответ https://${DOMAIN} (заголовки) ==="
curl -sI --connect-timeout 5 "https://${DOMAIN}/" 2>/dev/null | head -15 || echo "curl не удался"

LOC="$(curl -sI --connect-timeout 5 "https://${DOMAIN}/" 2>/dev/null | grep -i '^location:' || true)"
if echo "$LOC" | grep -qi 'pallink'; then
  echo ""
  echo "FAIL редирект на pallink — выполните:"
  echo "  cd /var/www/timesheet && sudo bash deploy/fix-coexist.sh"
elif [[ -z "$LOC" ]]; then
  echo "(редиректа Location нет — норма для SPA)"
fi

echo ""
echo "=== 6. SNI на localhost (какой сертификат) ==="
if command -v openssl >/dev/null 2>&1; then
  echo | openssl s_client -connect 127.0.0.1:443 -servername "${DOMAIN}" 2>/dev/null \
    | openssl x509 -noout -subject 2>/dev/null || echo "openssl не смог (nginx не слушает 443?)"
fi
