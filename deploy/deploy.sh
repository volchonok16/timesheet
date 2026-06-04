#!/usr/bin/env bash
# Единый скрипт деплоя TFS Timesheet на Linux (Ubuntu/Debian).
#
# Первый раз на чистом VPS:
#   sudo bash deploy/deploy.sh --bootstrap
#
# Обычное обновление:
#   cd /var/www/timesheet && git pull && sudo bash deploy/deploy.sh
#
# Выпуск SSL (после настройки DNS):
#   sudo bash deploy/deploy.sh --issue-ssl
#
# Переменные (или в .env): TIMESHEET_DOMAIN, TIMESHEET_API_DOMAIN

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DOMAIN="${TIMESHEET_DOMAIN:-mateplace.ru}"
API_DOMAIN="${TIMESHEET_API_DOMAIN:-api.mateplace.ru}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
CERTBOT_WEBROOT="/var/www/certbot"

DO_BOOTSTRAP=false
DO_PULL=false
DO_ISSUE_SSL=false
SKIP_NGINX=false

usage() {
  cat <<'EOF'
TFS Timesheet — deploy/deploy.sh

Использование:
  sudo bash deploy/deploy.sh              # production: Docker + nginx
  sudo bash deploy/deploy.sh --bootstrap  # + Docker/nginx на чистом сервере
  sudo bash deploy/deploy.sh --pull       # git pull перед деплоем
  sudo bash deploy/deploy.sh --issue-ssl  # Let's Encrypt (нужен DNS)
  sudo bash deploy/deploy.sh --help

Переменные окружения:
  TIMESHEET_DOMAIN      (по умолчанию mateplace.ru)
  TIMESHEET_API_DOMAIN  (по умолчанию api.mateplace.ru)

Пример со своим доменом:
  sudo TIMESHEET_DOMAIN=ts.example.com TIMESHEET_API_DOMAIN=api.ts.example.com \
    bash deploy/deploy.sh --bootstrap

Подробнее: deploy/LINUX.md
EOF
}

log() { echo "==> $*"; }
warn() { echo "!!  $*" >&2; }
die() { echo "Ошибка: $*" >&2; exit 1; }

need_root_for_nginx() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "Для nginx/SSL запустите с sudo: sudo bash deploy/deploy.sh $*"
  fi
}

load_domains_from_env() {
  if [[ -f .env ]]; then
    # shellcheck disable=SC1091
    set -a
    source .env 2>/dev/null || true
    set +a
    DOMAIN="${TIMESHEET_DOMAIN:-$DOMAIN}"
    API_DOMAIN="${TIMESHEET_API_DOMAIN:-$API_DOMAIN}"
    CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
  fi
}

substitute_domain() {
  sed -e "s/mateplace.ru/${DOMAIN}/g" \
      -e "s/api.mateplace.ru/${API_DOMAIN}/g"
}

ensure_env_file() {
  if [[ -f .env ]]; then
    return
  fi
  if [[ -f .env.production.example ]]; then
    cp .env.production.example .env
    log "Создан .env из .env.production.example"
  else
    warn "Файл .env отсутствует — будут значения по умолчанию из compose."
  fi
}

patch_env_domains() {
  [[ -f .env ]] || return 0
  local tmp
  tmp="$(mktemp)"
  grep -v '^TIMESHEET_DOMAIN=' .env 2>/dev/null | grep -v '^TIMESHEET_API_DOMAIN=' >"$tmp" || true
  {
    cat "$tmp"
    echo "TIMESHEET_DOMAIN=${DOMAIN}"
    echo "TIMESHEET_API_DOMAIN=${API_DOMAIN}"
    echo "APP_PUBLIC_URL=https://${DOMAIN}"
    echo "API_PUBLIC_URL=https://${API_DOMAIN}"
    echo "VITE_API_URL=https://${API_DOMAIN}"
    echo "CORS_ALLOW_ORIGINS=https://${DOMAIN},https://www.${DOMAIN}"
  } > .env
  rm -f "$tmp"
}

fix_broken_apt_sources() {
  # На VPS иногда добавляют https://docker.com (неверно) вместо download.docker.com
  local f
  for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do
    [[ -f "$f" ]] || continue
    if grep -qE 'https?://docker\.com' "$f" 2>/dev/null; then
      warn "Удаляем неверный репозиторий docker.com из $f"
      sed -i '/docker\.com/d' "$f"
    fi
  done
  if [[ -f /etc/apt/sources.list.d/docker.list ]] \
    && grep -qE 'docker\.com' /etc/apt/sources.list.d/docker.list 2>/dev/null; then
    warn "Удаляем /etc/apt/sources.list.d/docker.list (битый URL)"
    rm -f /etc/apt/sources.list.d/docker.list
  fi
}

apt_update_safe() {
  export DEBIAN_FRONTEND=noninteractive
  fix_broken_apt_sources
  if apt-get update -qq; then
    return 0
  fi
  warn "apt-get update не удался — повтор после очистки источников…"
  fix_broken_apt_sources
  apt-get update -qq || die "apt-get update не работает. Проверьте /etc/apt/sources.list.d/ (см. deploy/LINUX.md)."
}

install_bootstrap_packages() {
  need_root_for_nginx
  log "Установка базовых пакетов (apt)…"
  apt_update_safe
  apt-get install -y -qq ca-certificates curl git nginx
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker уже установлен: $(docker --version)"
    return
  fi
  need_root_for_nginx
  log "Установка Docker (пакеты Ubuntu: docker.io)…"
  export DEBIAN_FRONTEND=noninteractive
  apt_update_safe
  apt-get install -y -qq ca-certificates curl gnupg
  if ! command -v docker >/dev/null 2>&1; then
    apt-get install -y -qq docker.io docker-compose-v2 2>/dev/null \
      || apt-get install -y -qq docker.io docker-compose-plugin 2>/dev/null \
      || apt-get install -y -qq docker.io
  fi
  systemctl enable docker 2>/dev/null || true
  systemctl start docker 2>/dev/null || true
  sleep 2
  command -v docker >/dev/null 2>&1 || die "Не удалось установить Docker."
  docker compose version >/dev/null 2>&1 || die "Нужен Docker Compose v2 (плагин docker compose)."
}

ensure_docker_running() {
  if docker info >/dev/null 2>&1; then
    return
  fi
  need_root_for_nginx
  log "Запуск Docker daemon…"
  systemctl start docker.socket docker.service 2>/dev/null || true
  sleep 2
  docker info >/dev/null 2>&1 || die "Docker daemon недоступен (systemctl status docker)."
}

git_pull_if_requested() {
  if ! $DO_PULL; then
    return
  fi
  if [[ ! -d .git ]]; then
    warn "--pull: не git-репозиторий, пропуск."
    return
  fi
  log "git pull…"
  git pull --ff-only
}

stop_compose_port_conflicts() {
  # Старый стек или прежние порты :8000 / :5173 могут мешать :18080 / :15173
  local project
  for project in timesheet prog; do
    if docker compose -p "$project" -f docker-compose.yml -f docker-compose.prod.yml ps -q 2>/dev/null | grep -q .; then
      log "Останавливаем compose-проект «${project}»…"
      docker compose -p "$project" -f docker-compose.yml -f docker-compose.prod.yml down --remove-orphans 2>/dev/null || true
    fi
  done
  local id port
  for port in 18080 15173 8000 5173; do
    for id in $(docker ps -q --filter "publish=127.0.0.1:${port}" 2>/dev/null); do
      warn "Порт ${port} занят контейнером ${id} — останавливаем"
      docker stop "$id" 2>/dev/null || true
    done
  done
}

deploy_compose() {
  ensure_env_file
  load_domains_from_env
  patch_env_domains
  load_domains_from_env

  log "Проект: $ROOT"
  log "Домен: $DOMAIN | API: $API_DOMAIN"

  ensure_docker_running

  log "Остановка предыдущих контейнеров…"
  "${COMPOSE[@]}" down --remove-orphans 2>/dev/null || true
  stop_compose_port_conflicts

  log "Docker Compose (production)…"
  "${COMPOSE[@]}" up -d --build
}

configure_nginx() {
  need_root_for_nginx
  log "Nginx…"

  mkdir -p "$CERTBOT_WEBROOT"
  mkdir -p /etc/nginx/snippets

  substitute_domain < "$ROOT/deploy/nginx/snippets/ssl-mateplace.conf" > /etc/nginx/snippets/ssl-mateplace.conf
  cp -f "$ROOT/deploy/nginx/snippets/proxy-common.conf" /etc/nginx/snippets/

  if [[ -f "$CERT_DIR/fullchain.pem" && -f "$CERT_DIR/privkey.pem" ]]; then
    log "SSL найден — HTTPS."
    substitute_domain < "$ROOT/deploy/nginx/mateplace.conf" > /etc/nginx/sites-available/mateplace.conf
  else
    log "SSL нет — HTTP для certbot."
    substitute_domain < "$ROOT/deploy/nginx/mateplace.certbot-bootstrap.conf" > /etc/nginx/sites-available/mateplace.conf
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
}

issue_ssl_certificate() {
  need_root_for_nginx
  load_domains_from_env

  if [[ -f "$CERT_DIR/fullchain.pem" ]]; then
    log "Сертификат уже есть: $CERT_DIR"
    return 0
  fi

  configure_nginx
  deploy_compose

  if ! command -v certbot >/dev/null 2>&1; then
    log "Установка certbot…"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot
  fi

  log "Запрос сертификата Let's Encrypt…"
  certbot certonly --webroot -w "$CERTBOT_WEBROOT" \
    -d "$DOMAIN" -d "www.${DOMAIN}" -d "$API_DOMAIN" \
    --non-interactive --agree-tos --register-unsafely-without-email \
    || die "certbot не удался. Проверьте DNS (A-записи на IP сервера) и порт 80."

  configure_nginx
}

wait_for_health() {
  log "Проверка backend (до 30 с)…"
  local i code
  for i in $(seq 1 15); do
    if curl -sf http://127.0.0.1:18080/api/health >/dev/null 2>&1; then
      echo "OK: $(curl -sf http://127.0.0.1:18080/api/health)"
      return 0
    fi
    sleep 2
  done
  warn "backend не отвечает на :18080 — смотрите: ${COMPOSE[*]} logs backend"
}

print_summary() {
  load_domains_from_env
  echo ""
  log "Статус контейнеров"
  "${COMPOSE[@]}" ps || true

  wait_for_health

  code="$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: localhost' http://127.0.0.1:15173/ 2>/dev/null || echo '000')"
  echo "Frontend :15173 → HTTP $code (ожидается 200)"

  if [[ -f "$CERT_DIR/fullchain.pem" ]]; then
    echo ""
    log "HTTPS"
    curl -sI --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/" 2>/dev/null | head -3 || true
    curl -sf --resolve "${API_DOMAIN}:443:127.0.0.1" "https://${API_DOMAIN}/api/health" 2>/dev/null && echo "" || true
    echo ""
    echo "Готово: https://${DOMAIN}  |  API: https://${API_DOMAIN}/api/health"
  else
    echo ""
    echo "Сайт (HTTP, до SSL): http://${DOMAIN}"
    echo "Дальше: sudo bash deploy/deploy.sh --issue-ssl"
    echo "  (нужны A-записи: ${DOMAIN}, www.${DOMAIN}, ${API_DOMAIN} → IP сервера)"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --bootstrap) DO_BOOTSTRAP=true ;;
    --pull) DO_PULL=true ;;
    --issue-ssl) DO_ISSUE_SSL=true ;;
    --skip-nginx) SKIP_NGINX=true ;;
    *) die "Неизвестный аргумент: $1 (см. --help)" ;;
  esac
  shift
done

if $DO_ISSUE_SSL; then
  issue_ssl_certificate
  print_summary
  exit 0
fi

if $DO_BOOTSTRAP; then
  install_bootstrap_packages
  install_docker
fi

git_pull_if_requested
deploy_compose

if ! $SKIP_NGINX; then
  configure_nginx
fi

print_summary
