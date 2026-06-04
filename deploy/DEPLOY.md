# Деплой TFS Timesheet на mateplace.ru

## Локально (Docker + nginx)

```bash
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Откройте **http://localhost:18080**.

---

## Production (mateplace.ru)

Docker слушает только `127.0.0.1`, снаружи — системный nginx + certbot.

### Автоматически (единый скрипт)

Подробная инструкция: **[deploy/LINUX.md](LINUX.md)**

```bash
cd /var/www/timesheet
git pull
cp .env.production.example .env   # только первый раз
sudo bash deploy/deploy.sh --bootstrap   # первый раз на VPS
sudo bash deploy/deploy.sh --issue-ssl   # после настройки DNS
```

Обновление: `sudo bash deploy/deploy.sh --pull`

Старый вызов `deploy/apply-production.sh` делает то же самое.

### Сервисы

| Сервис   | Домен                    | Docker (localhost) |
|----------|--------------------------|--------------------|
| Frontend | https://mateplace.ru     | `127.0.0.1:15173`  |
| API      | https://api.mateplace.ru | `127.0.0.1:18080`  |
| Postgres | только внутри Docker     | порт не пробрасывается |

### DNS

A-записи на IP вашего VPS:

| Имя  | FQDN              |
|------|-------------------|
| `@`  | mateplace.ru      |
| `www`| www.mateplace.ru  |
| `api`| api.mateplace.ru  |

### Первый SSL-сертификат

1. DNS указывает на сервер.
2. `sudo bash deploy/apply-production.sh` — поднимет HTTP-конфиг для certbot.
3. Выпустите сертификат:

```bash
sudo apt install -y certbot
sudo certbot certonly --webroot -w /var/www/certbot \
  -d mateplace.ru -d www.mateplace.ru -d api.mateplace.ru
```

4. Снова `sudo bash deploy/apply-production.sh` — переключится на HTTPS.

### Конфиги nginx

| Файл | Назначение |
|------|------------|
| `deploy/nginx/docker.conf` | nginx в Docker (локальный `:18080`) |
| `deploy/nginx/mateplace.conf` | Production HTTPS |
| `deploy/nginx/mateplace.certbot-bootstrap.conf` | HTTP до выпуска SSL |
| `deploy/nginx/snippets/ssl-mateplace.conf` | Пути к Let's Encrypt |

### Проверка

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
curl -s http://127.0.0.1:18080/api/health
curl -sI -H 'Host: localhost' http://127.0.0.1:15173/
```

После SSL: https://mateplace.ru и https://api.mateplace.ru/api/health
