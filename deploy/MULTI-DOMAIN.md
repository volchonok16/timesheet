# Два домена на одном VPS (mateplace.ru + pallink.fun)

Один сервер, **два независимых сайта** — нормальная схема. Они не мешают друг другу, если у каждого:

- свой каталог и свой `docker compose`;
- свои порты на `127.0.0.1`;
- свой файл nginx с **`server_name`** только для своего домена;
- свой SSL-сертификат Let's Encrypt;
- **нет** `default_server` на `listen 443` (ни у одного из них).

```
                    Интернет :80 / :443
                              │
                         nginx (один)
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
   mateplace.ru        api.mateplace.ru      pallink.fun
          │                   │                   │
   127.0.0.1:31573    127.0.0.1:31080    127.0.0.1:32573 (пример)
   127.0.0.1:31080                         127.0.0.1:32080 (пример)
          │                   │                   │
   /var/www/timesheet                    /var/www/pallink (Roadmap)
```

## Порты (не пересекать)

| Проект | Каталог | Frontend (host) | Backend (host) | Домены |
|--------|---------|-----------------|----------------|--------|
| **TFS Timesheet** | `/var/www/timesheet` | **31573** | **31080** | mateplace.ru, api.mateplace.ru |
| **TFS Roadmap** (pallink) | `/var/www/pallink` (или как у вас) | **5173** | **8000** | pallink.fun |

В `.env` каждого проекта свои `TIMESHEET_*_PORT` / аналоги.  
Timesheet **не трогает** контейнеры pallink — только свой `docker compose`.

Roadmap может оставаться на стандартных **8000/5173** — Timesheet уже на **31080/31573**, конфликта нет.

---

## 1. Timesheet (mateplace.ru)

```bash
cd /var/www/timesheet
git pull
cp .env.production.example .env   # при первом разе
nano .env                         # порты 31080, 31573, домены mateplace
sudo bash deploy/deploy.sh
sudo bash deploy/deploy.sh --issue-ssl   # если ещё нет сертификата mateplace
```

Проверка:

```bash
curl -s http://127.0.0.1:31080/api/health
curl -sI --resolve mateplace.ru:443:127.0.0.1 https://mateplace.ru/ -k | head -8
```

---

## 2. Pallink / Roadmap (pallink.fun)

Код Roadmap в **отдельной** папке, свой деплой, **другие** порты (32080 / 32573).

Nginx для pallink — **отдельный** файл, например `/etc/nginx/sites-available/pallink.conf`:

```nginx
# Шаблон: deploy/nginx/pallink.coexist.example.conf

server {
    listen 80;
    listen [::]:80;
    server_name pallink.fun www.pallink.fun;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name www.pallink.fun;

    ssl_certificate /etc/letsencrypt/live/pallink.fun/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pallink.fun/privkey.pem;

    return 301 https://pallink.fun$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name pallink.fun;

    ssl_certificate /etc/letsencrypt/live/pallink.fun/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pallink.fun/privkey.pem;

    location /api/ {
        proxy_pass http://127.0.0.1:32080/api/;
        include snippets/proxy-common.conf;
    }

    location / {
        proxy_pass http://127.0.0.1:32573;
        include snippets/proxy-common.conf;
        proxy_set_header Host localhost;
    }
}
```

Включить:

```bash
sudo ln -sf /etc/nginx/sites-available/pallink.conf /etc/nginx/sites-enabled/pallink.conf
```

Сертификат pallink (один раз):

```bash
sudo certbot certonly --webroot -w /var/www/certbot \
  -d pallink.fun -d www.pallink.fun
```

---

## 3. Обязательно: убрать `default_server`

Оба сайта работают **параллельно**, но если у pallink (или у кого-то одного) стоит:

```nginx
listen 443 ssl default_server;
```

то запросы с «чужим» Host (или по IP) уйдут на этот сайт — отсюда редирект mateplace → pallink.

**Исправление:** у **обоих** конфигов только:

```nginx
listen 443 ssl http2;
server_name <свой-домен>;
```

без `default_server`.

Проверка:

```bash
sudo grep -r default_server /etc/nginx/sites-enabled/
# ideally empty or only intentional catch-all you understand
```

---

## 4. Итоговая проверка обоих доменов

```bash
sudo nginx -t
sudo systemctl reload nginx

curl -sI https://mateplace.ru | head -5
curl -sI https://pallink.fun | head -5

ls -la /etc/nginx/sites-enabled/
# ожидается: mateplace.conf  pallink.conf  (и НЕ default)
```

| URL | Ожидание |
|-----|----------|
| https://mateplace.ru | TFS Timesheet, без редиректа на pallink |
| https://api.mateplace.ru/api/health | `{"status":"ok",...}` |
| https://pallink.fun | TFS Roadmap |

---

## 5. DNS

Оба домена — **A-запись на один IP** VPS:

- `mateplace.ru`, `www`, `api` → IP VPS  
- `pallink.fun`, `www` → тот же IP VPS  

Это правильно для двух сайтов на одном сервере.

---

## 6. Обновления

```bash
# Timesheet
cd /var/www/timesheet && git pull && sudo bash deploy/deploy.sh --pull

# Roadmap — в своей папке, свой скрипт/compose
cd /var/www/pallink && git pull && …
```

Деплой Timesheet **не удаляет** `sites-enabled/pallink.conf` — трогает только `mateplace.conf`.

---

## mateplace.ru редиректит на pallink.fun

Обычно две причины сразу:

1. У **pallink** в nginx стоит `default_server` на 443 — запросы на `https://mateplace.ru` попадают на vhost pallink (Roadmap может увести на свой домен).
2. Нет SSL-сертификата для **mateplace.ru** — блоки `mateplace.conf` на 443 не работают, снова срабатывает pallink.

**Авто-правка:**

```bash
cd /var/www/timesheet
git pull
sudo bash deploy/fix-coexist.sh
```

**Вручную:**

```bash
sudo grep -r default_server /etc/nginx/sites-enabled/
# уберите default_server везде

sudo sed -i 's/32573/5173/g; s/32080/8000/g' /etc/nginx/sites-available/pallink.conf

cd /var/www/timesheet && sudo bash deploy/deploy.sh
sudo bash deploy/deploy.sh --issue-ssl   # если нет cert для mateplace

cd /var/www/pallink && docker compose up -d
```

Проверка, что mateplace не проксируется на pallink:

```bash
grep proxy_pass /etc/nginx/sites-enabled/mateplace.conf
# должно быть 31080 и 31573, НЕ 5173
```

---

## 502 Bad Gateway на обоих доменах

Nginx работает, но **контейнеры не слушают** те порты, что указаны в `proxy_pass`.

### Диагностика

```bash
cd /var/www/timesheet
sudo bash deploy/diagnose.sh
```

Смотрите строки `DOWN:` и `nginx error.log` (`connect() failed`).

### Частые причины

| Причина | Решение |
|---------|---------|
| Timesheet не запущен | `cd /var/www/timesheet && sudo bash deploy/deploy.sh` |
| В `.env` нет `TIMESHEET_BACKEND_PORT` / `TIMESHEET_FRONTEND_PORT` | скопировать из `.env.production.example` |
| nginx mateplace → 31080, а Docker на 8000 | перезапустить compose с `.env` или поправить nginx |
| pallink nginx → 32080, а Roadmap на 8000 | в `pallink.conf` заменить на `8000` и `5173` **или** сменить порты в `.env` pallink и перезапустить Docker |
| Оба проекта down после `docker compose down` | поднять оба стека |

### Быстрое восстановление mateplace

```bash
cd /var/www/timesheet
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
sudo bash deploy/deploy.sh
curl -s http://127.0.0.1:31080/api/health
```

### Быстрое восстановление pallink (502 → Connection refused на :32573)

Nginx смотрит не туда. Вернуть **8000** и **5173**:

```bash
sudo sed -i 's/127.0.0.1:32080/127.0.0.1:8000/g; s/127.0.0.1:32573/127.0.0.1:5173/g' \
  /etc/nginx/sites-available/pallink.conf
sudo nginx -t && sudo systemctl reload nginx

cd /var/www/pallink   # папка Roadmap
docker compose ps
docker compose up -d

curl -sI http://127.0.0.1:5173/ | head -3
curl -s http://127.0.0.1:8000/api/health 2>/dev/null || true
```

### Проверка в браузере

```bash
curl -sI https://mateplace.ru | head -5
curl -sI https://pallink.fun | head -5
```
