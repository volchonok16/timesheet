# Несколько доменов на одном VPS (mateplace.ru + pallink.fun)

Если при открытии **https://mateplace.ru** браузер уводит на **https://pallink.fun/** — nginx отдаёт **другой сайт** как сайт по умолчанию (`default_server`).

## Диагностика на сервере

```bash
# Какие сайты включены
ls -la /etc/nginx/sites-enabled/

# Кто слушает 443 и кто default_server
sudo nginx -T 2>/dev/null | grep -E 'listen.*443|server_name|default_server'

# Куда реально отвечает mateplace (с сервера)
curl -sI --resolve mateplace.ru:443:127.0.0.1 https://mateplace.ru/ -k | head -15
curl -sI --resolve pallink.fun:443:127.0.0.1 https://pallink.fun/ -k | head -5
```

Если в ответе на `mateplace.ru` в заголовке `Location: https://pallink.fun/...` — правьте nginx у **pallink**, не Timesheet.

## Исправление (типичное)

### 1. У pallink убрать `default_server`

Откройте конфиг pallink (имя файла может отличаться):

```bash
sudo grep -r default_server /etc/nginx/sites-enabled/
sudo nano /etc/nginx/sites-enabled/pallink   # или pallink.fun, roadmap…
```

Было (плохо на общем сервере):

```nginx
listen 443 ssl http2 default_server;
```

Должно быть:

```nginx
listen 443 ssl http2;
server_name pallink.fun www.pallink.fun;
```

То же для `listen 80 default_server` — уберите `default_server`, оставьте явный `server_name pallink.fun`.

### 2. Timesheet (mateplace) должен быть включён

```bash
cd /var/www/timesheet
ls -la /etc/nginx/sites-enabled/mateplace.conf
sudo bash deploy/deploy.sh
```

### 3. Отдельные SSL-сертификаты

У каждого домена свой сертификат:

```bash
sudo certbot certificates
```

Должны быть отдельно:

- `mateplace.ru` (+ www, api)
- `pallink.fun` (свой)

Для mateplace:

```bash
cd /var/www/timesheet
sudo bash deploy/deploy.sh --issue-ssl
```

### 4. Проверка и перезагрузка

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -sI https://mateplace.ru | head -10
```

## Важно

| Домен | Конфиг nginx | Docker Timesheet |
|-------|----------------|------------------|
| mateplace.ru, api.mateplace.ru | `sites-enabled/mateplace.conf` | да (порты 31080, 31573) |
| pallink.fun | свой файл в sites-enabled | нет (свой проект Roadmap) |

Не открывайте сайт по **IP сервера** — без `Host` nginx выберет `default_server` (часто pallink).

В браузере используйте именно **https://mateplace.ru**.

## Если mateplace всё ещё не открывается

```bash
curl -s http://127.0.0.1:31080/api/health
docker compose -f /var/www/timesheet/docker-compose.yml \
  -f /var/www/timesheet/docker-compose.prod.yml ps
```

Backend должен быть `Up`. Если health OK, а в браузере pallink — проблема только в nginx/SSL/DNS.
