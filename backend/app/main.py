from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from sqlalchemy import text

from app.auth_service import default_api_url, default_app_url, login_with_auth
from app.auth_sessions import delete_session, get_session
from app.config import settings
from app.db import Base, engine, get_db
from app.schemas import (
    ActivityOut,
    AuthDefaultsOut,
    AuthLoginOut,
    AuthStatusOut,
    CalendarOut,
    CostProjectOptionsOut,
    RecentEntryOut,
    RoleOut,
    StatsSummaryOut,
    TimeEntryIn,
    TimeEntryOut,
    TimesheetOut,
    TimesheetSyncOut,
    TfsAuthIn,
    WorkItemOut,
)
from app.activity_comments import comment_template_for
from app.time_service import (
    ACTIVITIES,
    ROLES,
    build_calendar,
    build_timesheet,
    get_cost_project_options_for_item,
    get_stats_summary,
    list_recent,
    list_recent_entries,
    log_time_entry,
    month_start,
    parent_items_from_recent,
    timesheet_parent_ids,
    touch_recent,
    week_start,
    work_item_out,
)
from app.time_sync import sync_time_from_tfs
from app.tfs_auth import TfsAuth, build_tfs_auth
from app.tfs_client import TfsClient

app = FastAPI(title="TFS Timesheet API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE time_entries ADD COLUMN IF NOT EXISTS cost_project VARCHAR(512)")
        )
        conn.execute(
            text("ALTER TABLE time_entries ADD COLUMN IF NOT EXISTS tfs_sync_key VARCHAR(160)")
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_time_entries_account_sync_key "
                "ON time_entries (account_key, tfs_sync_key) WHERE tfs_sync_key IS NOT NULL"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE time_entries ADD COLUMN IF NOT EXISTS owner_unique_name VARCHAR(256)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS auth_sessions ("
                "session_id VARCHAR(64) PRIMARY KEY, "
                "payload TEXT NOT NULL, "
                "created_at TIMESTAMP NOT NULL DEFAULT NOW()"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS account_sync_states ("
                "id SERIAL PRIMARY KEY, "
                "account_key VARCHAR(255) NOT NULL, "
                "period_start DATE NOT NULL, "
                "view VARCHAR(16) NOT NULL, "
                "synced_at TIMESTAMP NOT NULL, "
                "CONSTRAINT uq_account_sync_period UNIQUE (account_key, period_start, view)"
                ")"
            )
        )


def require_tfs_auth(x_session_id: str | None = Header(default=None, alias="X-Session-Id")) -> TfsAuth:
    auth = get_session(x_session_id)
    if auth is None:
        raise HTTPException(status_code=401, detail="Сессия TFS не найдена. Войдите снова.")
    return auth


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}


@app.get("/api/auth/defaults", response_model=AuthDefaultsOut)
def auth_defaults() -> AuthDefaultsOut:
    return AuthDefaultsOut(
        base_url=settings.tfs_base_url,
        project=settings.tfs_project,
        project_id=settings.tfs_project_id,
        app_url=default_app_url(),
        api_url=default_api_url(),
        version=settings.app_version,
    )


@app.get("/api/auth/status", response_model=AuthStatusOut)
def auth_status(auth: TfsAuth = Depends(require_tfs_auth)) -> AuthStatusOut:
    return AuthStatusOut(authenticated=True)


@app.post("/api/auth/login", response_model=AuthLoginOut)
async def auth_login(payload: TfsAuthIn) -> AuthLoginOut:
    auth = build_tfs_auth(**payload.model_dump())
    return await login_with_auth(auth)


@app.post("/api/auth/logout")
def auth_logout(x_session_id: str | None = Header(default=None, alias="X-Session-Id")) -> dict[str, bool]:
    delete_session(x_session_id)
    return {"ok": True}


@app.get("/api/meta/roles", response_model=list[RoleOut])
def meta_roles() -> list[RoleOut]:
    return ROLES


@app.get("/api/meta/activities", response_model=list[ActivityOut])
def meta_activities(role: str | None = Query(default=None)) -> list[ActivityOut]:
    rows = ACTIVITIES
    if role:
        role_row = next((item for item in ROLES if item.id == role or item.label == role), None)
        if role_row is not None:
            rows = [item for item in ACTIVITIES if item.role_id == role_row.id]
    return [
        item.model_copy(update={"comment_template": comment_template_for(item.id, item.label, item.role_id)})
        for item in rows
    ]


@app.get("/api/work-items/search", response_model=list[WorkItemOut])
async def search_work_items(
    q: str = Query(min_length=1),
    auth: TfsAuth = Depends(require_tfs_auth),
    db: Session = Depends(get_db),
) -> list[WorkItemOut]:
    client = TfsClient(auth)
    try:
        items = await client.search_work_items(q)
        for item in items:
            touch_recent(db, auth, item)
        return [work_item_out(item) for item in items]
    finally:
        await client.close()


@app.get("/api/work-items/recent", response_model=list[WorkItemOut])
def recent_work_items(
    auth: TfsAuth = Depends(require_tfs_auth),
    db: Session = Depends(get_db),
) -> list[WorkItemOut]:
    return list_recent(db, auth)


@app.get("/api/work-items/{item_id}", response_model=WorkItemOut)
async def get_work_item(
    item_id: int,
    auth: TfsAuth = Depends(require_tfs_auth),
    db: Session = Depends(get_db),
) -> WorkItemOut:
    client = TfsClient(auth)
    try:
        item = await client.get_work_item(item_id)
        normalized = client.normalize_item(item)
        touch_recent(db, auth, normalized)
        return work_item_out(normalized)
    finally:
        await client.close()


@app.get("/api/work-items/{item_id}/cost-projects", response_model=CostProjectOptionsOut)
async def cost_project_options(
    item_id: int,
    auth: TfsAuth = Depends(require_tfs_auth),
) -> CostProjectOptionsOut:
    client = TfsClient(auth)
    try:
        payload = await get_cost_project_options_for_item(client, item_id)
        return CostProjectOptionsOut(**payload)
    finally:
        await client.close()


@app.post("/api/timesheet/sync", response_model=TimesheetSyncOut)
async def sync_timesheet_from_tfs(
    start: date | None = Query(default=None),
    view: str = Query(default="week", pattern="^(week|month)$"),
    force: bool = Query(default=False, description="Игнорировать кэш TTL и полный WIQL"),
    auth: TfsAuth = Depends(require_tfs_auth),
    db: Session = Depends(get_db),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> TimesheetSyncOut:
    today = date.today()
    if start is None:
        start = month_start(today) if view == "month" else week_start(today)
    elif view == "month":
        start = month_start(start)
    else:
        start = week_start(start)

    try:
        payload = await sync_time_from_tfs(
            db,
            auth,
            period_start=start,
            view=view,
            force=force,
            session_id=x_session_id,
        )
        return TimesheetSyncOut(**payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TFS sync: {exc}") from exc


@app.post("/api/timesheet/repair", response_model=TimesheetSyncOut)
async def repair_timesheet_data(
    auth: TfsAuth = Depends(require_tfs_auth),
    db: Session = Depends(get_db),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> TimesheetSyncOut:
    """Сбросить все импорты из TFS и пересобрать текущую неделю только для владельца PAT."""
    start = week_start(date.today())
    try:
        payload = await sync_time_from_tfs(
            db,
            auth,
            period_start=start,
            view="week",
            force=True,
            session_id=x_session_id,
        )
        return TimesheetSyncOut(**payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TFS repair: {exc}") from exc


@app.get("/api/timesheet", response_model=TimesheetOut)
async def get_timesheet(
    start: date | None = Query(default=None),
    view: str = Query(default="week", pattern="^(week|month)$"),
    sync: bool = Query(
        default=False,
        description="Подтянуть из TFS (с TTL-кэшем; для быстрой загрузки оставьте false)",
    ),
    enrich: bool = Query(
        default=False,
        description="Обогатить табель из TFS (медленно); по умолчанию только БД и кэш",
    ),
    auth: TfsAuth = Depends(require_tfs_auth),
    db: Session = Depends(get_db),
) -> TimesheetOut:
    today = date.today()
    if start is None:
        start = month_start(today) if view == "month" else week_start(today)
    elif view == "month":
        start = month_start(start)
    else:
        start = week_start(start)

    if sync:
        try:
            await sync_time_from_tfs(
                db, auth, period_start=start, view=view, force=False
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"TFS sync: {exc}") from exc

    parent_ids = timesheet_parent_ids(db, auth, period_start=start, view=view)
    children_by_parent: dict[int, list[dict[str, Any]]] = {}

    if enrich and parent_ids:
        client = TfsClient(auth)
        try:
            items = await client.get_work_items_batch(parent_ids)
            parent_items = [client.normalize_item(item) for item in items]
            for row in parent_items:
                touch_recent(db, auth, row)
            db.commit()
        finally:
            await client.close()
    else:
        parent_items = parent_items_from_recent(db, auth, parent_ids)

    return build_timesheet(
        db,
        auth,
        period_start=start,
        view=view,
        parent_items=parent_items,
        children_by_parent=children_by_parent,
    )


@app.get("/api/stats/summary", response_model=StatsSummaryOut)
def stats_summary(
    auth: TfsAuth = Depends(require_tfs_auth),
    db: Session = Depends(get_db),
) -> StatsSummaryOut:
    return StatsSummaryOut(**get_stats_summary(db, auth))


@app.get("/api/time-entries/recent", response_model=list[RecentEntryOut])
def recent_time_entries(
    limit: int = Query(default=8, ge=1, le=30),
    auth: TfsAuth = Depends(require_tfs_auth),
    db: Session = Depends(get_db),
) -> list[RecentEntryOut]:
    return [RecentEntryOut(**row) for row in list_recent_entries(db, auth, limit=limit)]


@app.get("/api/calendar", response_model=CalendarOut)
def get_calendar(
    scope: str = Query(default="month", pattern="^(month|quarter|year)$"),
    year: int = Query(default=date.today().year),
    month: int | None = Query(default=None, ge=1, le=12),
    quarter: int | None = Query(default=None, ge=1, le=4),
    auth: TfsAuth = Depends(require_tfs_auth),
    db: Session = Depends(get_db),
) -> CalendarOut:
    return build_calendar(db, auth, scope=scope, year=year, month=month, quarter=quarter)


@app.post("/api/time-entries", response_model=TimeEntryOut)
async def create_time_entry(
    payload: TimeEntryIn,
    auth: TfsAuth = Depends(require_tfs_auth),
    db: Session = Depends(get_db),
) -> TimeEntryOut:
    try:
        return await log_time_entry(
            db,
            auth,
            parent_work_item_id=payload.parent_work_item_id,
            role=payload.role,
            activity=payload.activity,
            entry_date=payload.entry_date,
            hours=payload.hours,
            minutes=payload.minutes,
            subtract=payload.subtract,
            comment=payload.comment,
            cost_project=payload.cost_project,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TFS: {exc}") from exc
