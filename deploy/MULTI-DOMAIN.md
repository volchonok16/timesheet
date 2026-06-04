# mateplace.ru + pallink.fun на одном VPS

Два репозитория, **два nginx-конфига**, общий nginx на хосте.

| Проект | Репозиторий | Каталог на VPS (пример) | Nginx | Docker порты (host) |
|--------|-------------|-------------------------|-------|---------------------|
| **TFS Timesheet** | timesheet | `/var/www/timesheet` | `mateplace.conf` из **timesheet** | **31080**, **31573** |
| **TFS Roadmap (Ganta)** | ganta | `/var/www/ganta` или `/var/www/roadmap` | `pallink.conf` из **ganta** | **8000**, **5173** |

**Не подменяйте** `pallink.conf` файлом из timesheet — только из [ganta/deploy/nginx/pallink.conf](https://github.com/volchonok16/timesheet/blob/main/../ganta).

---

## Быстрое восстановление обоих сайтов

```bash
cd /var/www/timesheet
git pull
sudo bash deploy/fix-coexist.sh
```

Скрипт:

1. Убирает `default_server` (из‑за него mateplace «уезжал» на pallink).
2. Копирует **pallink.conf** и **ssl-pallink.conf** из каталога **Ganta**.
3. Поднимает Docker Ganta (`8000` / `5173`).
4. Ставит **mateplace.conf** из Timesheet (`31080` / `31573`).

---

## Вручную (если нужно)

### Pallink — только Ganta

```bash
cd /var/www/ganta   # или /var/www/roadmap — где лежит ganta
git pull
sudo bash deploy/apply-production.sh
curl -s http://127.0.0.1:8000/api/health
curl -sI http://127.0.0.1:5173/ | head -3
```

Конфиг: `deploy/nginx/pallink.conf` — upstream `127.0.0.1:8000` и `127.0.0.1:5173`.  
Отдельно на сервере могут быть **minio.pallink.fun**, **turn.pallink.fun** (свои conf, Ganta их не трогает).

### Mateplace — только Timesheet

```bash
cd /var/www/timesheet
git pull
# .env: TIMESHEET_BACKEND_PORT=31080, TIMESHEET_FRONTEND_PORT=31573
sudo bash deploy/deploy.sh
sudo bash deploy/deploy.sh --issue-ssl   # если нет cert mateplace.ru
curl -s http://127.0.0.1:31080/api/health
```

Конфиг: `deploy/nginx/mateplace.conf` — upstream `127.0.0.1:31080` и `127.0.0.1:31573`.

---

## mateplace редиректит на pallink.fun

**Чаще всего:** нет сертификата и блоков `listen 443` для `mateplace.ru`. Тогда HTTPS обрабатывает **pallink** (единственный SSL на 443) — в браузере открывается pallink.fun.

Диагностика:

```bash
sudo bash deploy/verify-domains.sh
```

Исправление:

```bash
sudo bash deploy/fix-coexist.sh
# или только SSL:
sudo bash deploy/deploy.sh --issue-ssl
```

Другие причины:

1. `default_server` на 443 у pallink.
2. В `mateplace.conf` `proxy_pass` на **5173** (Ganta) вместо **31573**.
3. `sites-enabled/mateplace.conf` отсутствует.

**Проверка:**

```bash
grep default_server /etc/nginx/sites-enabled/*
grep proxy_pass /etc/nginx/sites-enabled/mateplace.conf
grep proxy_pass /etc/nginx/sites-enabled/pallink.conf
```

---

## 502 на pallink.fun

Nginx смотрит на `:5173`, контейнеры Ganta не запущены:

```bash
cd /var/www/ganta
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

---

## DNS

Оба домена — **A на один IP** VPS. Это нормально: nginx различает по `server_name`.

---

## Обновления

```bash
cd /var/www/ganta && git pull && sudo bash deploy/apply-production.sh
cd /var/www/timesheet && sudo bash deploy/deploy.sh --pull
```
