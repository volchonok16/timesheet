# TFS Timesheet

Современный интерфейс для списания времени в TFS (Tele2): недельный табель, месячный календарь, быстрый поиск требований/задач и запись в **Completed Work** через дочернюю задачу вида `Роль - Активность`.

Авторизация повторяет подход из Ganta/Roadmap: логин/пароль или PAT, сессия хранится на backend.

## Быстрый старт

```bash
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Откройте **http://localhost:30080** и войдите в TFS.

Nginx в Docker проксирует frontend (`/`) и API (`/api`). Postgres на хосте: `localhost:30433`.

Production-деплой на Linux: `sudo bash deploy/deploy.sh` — см. [deploy/LINUX.md](deploy/LINUX.md).

На одном VPS с **pallink.fun** (Ganta/Roadmap): [deploy/MULTI-DOMAIN.md](deploy/MULTI-DOMAIN.md) — nginx pallink только из репозитория **ganta**.

## Что уже работает

- Вход в TFS (учётная запись / PAT), как в Ganta
- Поиск требований, ошибок и задач по номеру или названию
- Недельный и помесячный табель со списаниями по дням
- Календарь за месяц с суммой часов по дням
- Модальное окно «Внести время»: роль, активность, дата, добавить/вычесть, часы/минуты, комментарий
- Создание/поиск дочерней задачи в TFS и обновление `Microsoft.VSTS.Scheduling.CompletedWork`
- Локальное хранение дневных списаний в PostgreSQL (для календаря и табеля)
- Подтягивание списаний из TFS (как **Oscar** `stream_get-time-tracking-results`): недавние ЗНИ/требования → дочерние задачи `Роль - Активность` → часы по дням из истории TFS и **Completed Work**

## Архитектура

| Сервис   | Стек              | Доступ (dev)        |
|----------|-------------------|---------------------|
| nginx    | nginx:alpine      | http://localhost:30080 |
| frontend | React + TS + Vite | через nginx (хост :31573) |
| backend  | FastAPI + httpx   | хост :31080, `/api` через nginx |
| postgres | PostgreSQL 16     | localhost:30433      |

## API (основное)

- `POST /api/auth/login` — вход
- `GET /api/work-items/search?q=` — поиск
- `GET /api/timesheet?start=&view=week|month&sync=true` — табель (с опциональной синхронизацией из TFS)
- `POST /api/timesheet/sync?start=&view=week|month` — только синхронизация из TFS
- `GET /api/calendar?year=&month=` — календарь
- `POST /api/time-entries` — списание времени

## Дальнейшие улучшения

- Импорт списаний из внешних time-tracking систем (не только TFS Completed Work)
- Шаблоны time-shooting (8ч одним кликом, копирование прошлой недели)
- Фильтры по area path / команде
- Экспорт в Excel

## Переменные окружения

См. `.env.example`.
