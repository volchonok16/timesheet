# Деплой TFS Timesheet на Linux (VPS)

Один скрипт: **`deploy/deploy.sh`**. Поднимает Docker (postgres, backend, frontend) и системный nginx с HTTPS.

## Требования

- Ubuntu 22.04/24.04 или Debian 12 (другие Debian-based — обычно тоже работают)
- root или `sudo`
- Домен с A-записями на IP сервера (для SSL)
- Открыты порты **22**, **80**, **443**

## DNS (перед SSL)

| Имя | Куда |
|-----|------|
| `@` | `your-domain.ru` → IP VPS |
| `www` | `www.your-domain.ru` → IP VPS |
| `api` | `api.your-domain.ru` → IP VPS |

По умолчанию в скрипте: `mateplace.ru`, `www.mateplace.ru`, `api.mateplace.ru`. Свой домен — через переменные (см. ниже).

---

## 1. Первый раз: клонировать и поднять всё

На сервере под root или пользователем с sudo:

```bash
sudo apt update
sudo apt install -y git

sudo mkdir -p /var/www
cd /var/www
sudo git clone <URL-ВАШЕГО-РЕПОЗИТОРИЯ> timesheet
cd timesheet

# Свой домен (опционально):
# export TIMESHEET_DOMAIN=ts.example.com
# export TIMESHEET_API_DOMAIN=api.ts.example.com

sudo bash deploy/deploy.sh --bootstrap
```

`--bootstrap` установит Docker, nginx, git (если не было) и запустит приложение в HTTP-режиме (для выпуска сертификата).

Проверка без SSL:

```bash
curl -s http://127.0.0.1:31080/api/health
```

---

## 2. SSL (Let's Encrypt)

Когда DNS уже указывает на сервер (подождите 5–30 мин после смены записей):

```bash
cd /var/www/timesheet
sudo bash deploy/deploy.sh --issue-ssl
```

Скрипт выпустит сертификат и переключит nginx на HTTPS.

Проверка:

```bash
curl -s https://api.<ваш-домен>/api/health
```

Откройте в браузере: `https://<ваш-домен>`.

---

## 3. Обновление после изменений в коде

```bash
cd /var/www/timesheet
sudo bash deploy/deploy.sh --pull
```

Или вручную:

```bash
git pull
sudo bash deploy/deploy.sh
```

---

## 4. Настройка `.env` на сервере

После первого запуска отредактируйте `/var/www/timesheet/.env`:

```bash
sudo nano /var/www/timesheet/.env
```

Минимум проверьте:

- `TFS_BASE_URL`, `TFS_PROJECT`, `TFS_PROJECT_ID`
- `TIMESHEET_DOMAIN`, `TIMESHEET_API_DOMAIN`
- `APP_PUBLIC_URL`, `API_PUBLIC_URL`, `VITE_API_URL`
- в production смените пароль БД: `POSTGRES_PASSWORD` и `DATABASE_URL`

После правок:

```bash
sudo bash deploy/deploy.sh
```

---

## 5. Полезные команды

| Действие | Команда |
|----------|---------|
| Статус контейнеров | `docker compose -f docker-compose.yml -f docker-compose.prod.yml ps` |
| Логи backend | `docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f backend` |
| Перезапуск без сборки | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` |
| Остановить | `docker compose -f docker-compose.yml -f docker-compose.prod.yml down` |
| Проверить nginx | `sudo nginx -t && sudo systemctl reload nginx` |

Продление SSL (cron certbot обычно ставит сам):

```bash
sudo certbot renew --dry-run
```

---

## 6. Архитектура на сервере

```
Интернет :443/:80
    ↓
nginx (системный)
    ├─ https://domain/      → 127.0.0.1:31573 (frontend)
    └─ https://api.domain/  → 127.0.0.1:31080 (FastAPI)
                              ↓
                         postgres (только Docker-сеть)
```

---

## 7. Типичные проблемы

**`The repository 'https://docker.com noble Release' does not have a Release file`**

На сервере добавлен неверный apt-репозиторий (`docker.com` вместо официального). Исправление:

```bash
sudo grep -r docker.com /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null
sudo rm -f /etc/apt/sources.list.d/docker.list
sudo sed -i '/docker\.com/d' /etc/apt/sources.list.d/*.list 2>/dev/null
sudo apt-get update
```

Затем снова: `sudo bash deploy/deploy.sh --bootstrap`  
(скрипт `deploy.sh` тоже пытается убрать этот репозиторий автоматически.)

**`Bind for 127.0.0.1:… failed: port is already allocated`**

Порт занят старым контейнером (часто после запуска из папки `prog` или старых портов 8000/5173):

```bash
cd /var/www/timesheet
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker compose -p prog -f docker-compose.yml -f docker-compose.prod.yml down 2>/dev/null || true
docker ps --format '{{.Names}} {{.Ports}}' | grep -E '31080|31573|30080|5173|8000'
sudo ss -tlnp | grep -E ':31080|:31573|:30080|:5173'
# остановить лишний контейнер:
docker stop <ID_из_docker_ps>
sudo bash deploy/deploy.sh
```

**certbot: connection refused** — DNS ещё не обновился или закрыт порт 80. Проверьте: `dig +short your-domain.ru`.

**502 Bad Gateway** — контейнеры не поднялись: `docker compose ... ps` и `logs backend`.

**CORS / API не отвечает** — в `.env` должны совпадать `APP_PUBLIC_URL`, `API_PUBLIC_URL`, `VITE_API_URL` с реальными HTTPS-URL; после смены пересоберите: `sudo bash deploy/deploy.sh`.

**Старый скрипт** — `deploy/apply-production.sh` по-прежнему работает и вызывает `deploy.sh`.

---

## Краткая шпаргалка

```bash
# Первый деплой
cd /var/www/timesheet && sudo bash deploy/deploy.sh --bootstrap

# SSL
sudo bash deploy/deploy.sh --issue-ssl

# Обновление
sudo bash deploy/deploy.sh --pull
```
