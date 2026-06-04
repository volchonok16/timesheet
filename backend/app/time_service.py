from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, desc, func, or_, select
from sqlalchemy.orm import Session

from app.activities import ACTIVITIES
from app.config import settings
from app.db import RecentWorkItem, TimeEntry
from app.schemas import (
    CalendarDayOut,
    CalendarOut,
    DayTotalOut,
    MonthCalendarOut,
    RoleOut,
    TimeEntryOut,
    TimesheetGroupOut,
    TimesheetOut,
    TrackingRowOut,
    WorkItemOut,
)
from app.tfs_auth import TfsAuth
from app.tfs_client import TfsClient

ROLES: list[RoleOut] = [
    RoleOut(id="analyst", label="Аналитик"),
    RoleOut(id="architect", label="Архитектор"),
    RoleOut(id="developer", label="Разработчик"),
    RoleOut(id="qa", label="QA"),
    RoleOut(id="qa-auto", label="QA Автоматизация"),
    RoleOut(id="qa-load", label="QA Нагрузка"),
    RoleOut(id="devops", label="DevOps"),
    RoleOut(id="pm", label="РГР"),
]

CLOSED_STATES = {"Closed", "Done", "Removed", "Cancelled"}


def tracking_title(role: str, activity: str) -> str:
    return f"{role} - {activity}"


def parse_tracking_title(title: str) -> tuple[str, str]:
    if " - " in title:
        role, activity = title.split(" - ", 1)
        return role.strip(), activity.strip()
    return "", title.strip()


ROLE_LABELS = {role.label for role in ROLES} | {role.id for role in ROLES}


def is_app_tracking_task(child: dict[str, Any], *, known_tracking_ids: set[int]) -> bool:
    child_id = int(child["id"])
    if child_id in known_tracking_ids:
        return True
    title = str(child.get("title") or "")
    if " - " not in title:
        return False
    role, _activity = parse_tracking_title(title)
    return role in ROLE_LABELS


def week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def month_start(value: date) -> date:
    return value.replace(day=1)


def quarter_for_month(month: int) -> int:
    return (month - 1) // 3 + 1


def quarter_start_month(quarter: int) -> int:
    return (quarter - 1) * 3 + 1


def period_end(start: date, view: str) -> date:
    if view == "month":
        last_day = monthrange(start.year, start.month)[1]
        return start.replace(day=last_day)
    return start + timedelta(days=6)


def total_hours(hours: float, minutes: int) -> float:
    return round(hours + minutes / 60, 2)


def owner_unique_name_for(auth: TfsAuth) -> str | None:
    """Канонический логин TFS (TELE2\\user) для владельца строки."""
    for candidate in (
        auth.tfs_unique_name,
        auth.username,
        auth.tfs_email,
        auth.tfs_descriptor,
        auth.tfs_identity_id,
    ):
        raw = (candidate or "").strip()
        if raw:
            return raw.casefold()
    return None


def entry_owned_by_current_user(entry: TimeEntry, auth: TfsAuth) -> bool:
    """Ручные списания в аккаунте — свои; stream — доверяем аккаунту; TFS history — по owner."""
    if not entry.tfs_sync_key:
        return True
    key = entry.tfs_sync_key
    if key.startswith(("stream:", "oscar:", "grid:", "tsapi:")):
        return True
    owner = owner_unique_name_for(auth)
    if not owner:
        return False
    stored = (entry.owner_unique_name or "").strip().casefold()
    if not stored:
        return False
    return stored == owner


def filter_entries_for_user(entries: list[TimeEntry], auth: TfsAuth) -> list[TimeEntry]:
    return [entry for entry in entries if entry_owned_by_current_user(entry, auth)]


def _entry_source_rank(sync_key: str | None) -> int:
    key = sync_key or ""
    if key.startswith("tsapi:"):
        return 0
    if key.startswith("grid:"):
        return 1
    if key.startswith(("stream:", "oscar:")):
        return 2
    if key.startswith("tfs:"):
        return 3
    return 4


def dedupe_time_entries(entries: list[TimeEntry]) -> list[TimeEntry]:
    """
    Одна ячейка сетки = задача списания + день.
    При tsapi + старом grid/tfs/history оставляем tsapi; иначе одну строку с лучшим источником.
    Несколько tsapi:… за день — разные ListDelta, часы суммируются.
    """
    buckets: dict[tuple, list[TimeEntry]] = defaultdict(list)
    for entry in entries:
        if entry.tracking_work_item_id is not None:
            key = ("t", int(entry.tracking_work_item_id), entry.entry_date)
        else:
            key = ("p", entry.parent_work_item_id, entry.role, entry.activity, entry.entry_date)
        buckets[key].append(entry)

    result: list[TimeEntry] = []
    for bucket in buckets.values():
        tsapi_rows = [row for row in bucket if (row.tfs_sync_key or "").startswith("tsapi:")]
        if tsapi_rows:
            result.extend(tsapi_rows)
            continue
        bucket.sort(key=lambda row: _entry_source_rank(row.tfs_sync_key))
        result.append(bucket[0])
    return result


def delete_duplicate_entries_in_period(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    period_end: date,
) -> int:
    """Удаляет из БД дубли grid/tsapi за период (оставляет dedupe_time_entries)."""
    entries = filter_entries_for_user(
        list(
            db.scalars(
                select(TimeEntry).where(
                    TimeEntry.account_key == auth.account_key,
                    TimeEntry.entry_date >= period_start,
                    TimeEntry.entry_date <= period_end,
                )
            ).all()
        ),
        auth,
    )
    keep_ids = {entry.id for entry in dedupe_time_entries(entries)}
    stale_ids = [entry.id for entry in entries if entry.id not in keep_ids]
    if not stale_ids:
        return 0
    db.execute(delete(TimeEntry).where(TimeEntry.id.in_(stale_ids)))
    return len(stale_ids)


def sum_deduped_hours(
    entries: list[TimeEntry],
    *,
    on_date: date | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> float:
    total = 0.0
    for entry in dedupe_time_entries(entries):
        if on_date is not None and entry.entry_date != on_date:
            continue
        if period_start is not None and entry.entry_date < period_start:
            continue
        if period_end is not None and entry.entry_date > period_end:
            continue
        total += entry.hours
    return round(total, 2)


def _tracking_row_key(entry: TimeEntry) -> tuple[str, str, int | None]:
    if entry.tracking_work_item_id is not None:
        return ("", "", entry.tracking_work_item_id)
    return (entry.role, entry.activity, None)


def closed_parent_ids(db: Session, auth: TfsAuth) -> set[int]:
    rows = db.scalars(
        select(RecentWorkItem.work_item_id).where(
            RecentWorkItem.account_key == auth.account_key,
            RecentWorkItem.state.in_(CLOSED_STATES),
        )
    ).all()
    return {int(row) for row in rows}


def sum_groups_hours(
    groups: list[TimesheetGroupOut],
) -> tuple[list[DayTotalOut], float]:
    day_totals_map: dict[date, float] = defaultdict(float)
    total = 0.0
    for group in groups:
        total += group.total_hours
        for row in group.tracking_rows:
            for day_key, hours in row.daily_hours.items():
                day_totals_map[date.fromisoformat(day_key)] += hours
    day_totals = [
        DayTotalOut(date=day, hours=round(hours, 2))
        for day, hours in sorted(day_totals_map.items())
    ]
    return day_totals, round(total, 2)


def _pick_display_role_activity(entries: list[TimeEntry]) -> tuple[str, str]:
    for entry in entries:
        if entry.role and entry.role != "—":
            return entry.role, entry.activity
    if entries:
        return entries[0].role, entries[0].activity
    return "—", ""


def entry_ownership_clause(auth: TfsAuth):
    owner = owner_unique_name_for(auth)
    stream_keys = or_(
        TimeEntry.tfs_sync_key.like("stream:%"),
        TimeEntry.tfs_sync_key.like("oscar:%"),
        TimeEntry.tfs_sync_key.like("grid:%"),
        TimeEntry.tfs_sync_key.like("tsapi:%"),
    )
    if not owner:
        return or_(TimeEntry.tfs_sync_key.is_(None), stream_keys)
    return or_(
        TimeEntry.tfs_sync_key.is_(None),
        stream_keys,
        func.lower(TimeEntry.owner_unique_name) == owner,
    )


def backfill_entry_owners(db: Session, auth: TfsAuth) -> int:
    """Проставить owner старым строкам этого аккаунта (после входа по PAT)."""
    owner = owner_unique_name_for(auth)
    if not owner:
        return 0
    rows = db.scalars(
        select(TimeEntry).where(
            TimeEntry.account_key == auth.account_key,
            TimeEntry.owner_unique_name.is_(None),
        )
    ).all()
    for row in rows:
        row.owner_unique_name = owner
    return len(rows)


def parent_items_from_recent(
    db: Session,
    auth: TfsAuth,
    parent_ids: list[int],
) -> list[dict[str, Any]]:
    """Метаданные родителей из локального кэша — без запросов к TFS."""
    if not parent_ids:
        return []
    rows = db.scalars(
        select(RecentWorkItem).where(
            RecentWorkItem.account_key == auth.account_key,
            RecentWorkItem.work_item_id.in_(parent_ids),
        )
    ).all()
    by_id = {row.work_item_id: row for row in rows}
    tfs_base = f"{auth.base_url.rstrip('/')}/{auth.project}/_workitems/edit"
    items: list[dict[str, Any]] = []
    for parent_id in parent_ids:
        row = by_id.get(parent_id)
        if row:
            items.append(
                {
                    "id": parent_id,
                    "title": row.title,
                    "workItemType": row.work_item_type,
                    "state": row.state,
                    "areaPath": row.area_path,
                    "kind": row.kind,
                    "tfsUrl": f"{tfs_base}/{parent_id}",
                    "completedWork": 0,
                }
            )
        else:
            items.append(
                {
                    "id": parent_id,
                    "title": f"#{parent_id}",
                    "workItemType": "",
                    "state": "",
                    "areaPath": "",
                    "kind": "other",
                    "tfsUrl": f"{tfs_base}/{parent_id}",
                    "completedWork": 0,
                }
            )
    return items


def timesheet_parent_ids(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
    recent_limit: int = 30,
) -> list[int]:
    """Как /track в Oscar: только ЗНИ/требования, по которым есть ваши часы в периоде."""
    del recent_limit  # недавние из поиска — не в сетку табеля
    end = period_end(period_start, view)
    entry_ids = db.scalars(
        select(TimeEntry.parent_work_item_id)
        .where(
            TimeEntry.account_key == auth.account_key,
            TimeEntry.entry_date >= period_start,
            TimeEntry.entry_date <= end,
            TimeEntry.hours > 0,
            entry_ownership_clause(auth),
        )
        .distinct()
    ).all()
    return list(entry_ids)


def touch_recent(db: Session, auth: TfsAuth, item: dict[str, Any]) -> None:
    account_key = auth.account_key
    row = db.scalar(
        select(RecentWorkItem).where(
            RecentWorkItem.account_key == account_key,
            RecentWorkItem.work_item_id == item["id"],
        )
    )
    if row is None:
        row = RecentWorkItem(
            account_key=account_key,
            work_item_id=item["id"],
            title=item["title"],
            work_item_type=item["workItemType"],
            state=item["state"],
            area_path=item["areaPath"],
            kind=item["kind"],
        )
        db.add(row)
    else:
        row.title = item["title"]
        row.state = item["state"]
        row.area_path = item["areaPath"]
        row.kind = item["kind"]
        row.touched_at = datetime.utcnow()
    db.commit()


def list_recent(db: Session, auth: TfsAuth, *, limit: int = 12) -> list[WorkItemOut]:
    rows = db.scalars(
        select(RecentWorkItem)
        .where(RecentWorkItem.account_key == auth.account_key)
        .order_by(desc(RecentWorkItem.touched_at))
        .limit(limit)
    ).all()
    return [
        WorkItemOut(
            id=row.work_item_id,
            title=row.title,
            work_item_type=row.work_item_type,
            state=row.state,
            area_path=row.area_path,
            kind=row.kind,  # type: ignore[arg-type]
            tfs_url="",
        )
        for row in rows
    ]


def work_item_out(item: dict[str, Any]) -> WorkItemOut:
    return WorkItemOut(
        id=int(item["id"]),
        title=item["title"],
        work_item_type=item["workItemType"],
        state=item["state"],
        area_path=item["areaPath"],
        kind=item["kind"],
        tfs_url=item["tfsUrl"],
        completed_work=float(item.get("completedWork") or 0),
    )


async def ensure_tracking_task(
    client: TfsClient,
    *,
    parent_id: int,
    role: str,
    activity: str,
    cost_project: str | None = None,
) -> int:
    title = tracking_title(role, activity)
    existing = await client.find_child_task(parent_id, title)
    if existing:
        tracking_id = int(existing["id"])
        if cost_project:
            await client.set_work_item_field(tracking_id, settings.cost_project_field, cost_project)
        return tracking_id

    parent = await client.get_work_item(parent_id)
    fields = parent.get("fields") or {}
    area_path = fields.get("System.AreaPath") or f"{client.project}\\Digital"
    extra_fields: dict[str, Any] = {}
    iteration_path = await client.get_latest_iteration_path(
        area_path=area_path,
    )
    if iteration_path:
        extra_fields["System.IterationPath"] = iteration_path
    elif fields.get("System.IterationPath"):
        extra_fields["System.IterationPath"] = fields["System.IterationPath"]
    resolved_cost = cost_project or client.read_cost_project_value(parent)
    if resolved_cost:
        extra_fields[settings.cost_project_field] = resolved_cost
    created = await client.create_child_task(
        parent_id,
        title=title,
        area_path=area_path,
        extra_fields=extra_fields,
    )
    return int(created["id"])


def match_user_cost_project(user_name: str | None, options: list[str]) -> str | None:
    if not user_name:
        return None

    lowered = user_name.casefold()
    for option in options:
        if option.casefold() == lowered:
            return option
    for option in options:
        option_lower = option.casefold()
        if lowered in option_lower or option_lower in lowered:
            return option
    surname = user_name.split()[0].casefold()
    if surname:
        for option in options:
            if option.casefold().startswith(surname):
                return option
    return None


async def resolve_cost_project(
    client: TfsClient,
    *,
    parent_id: int,
    explicit: str | None = None,
) -> str:
    if (explicit or "").strip():
        return explicit.strip()

    options = await client.get_cost_project_options(settings.task_type_name)
    user_name = await client.get_authenticated_user_name()
    matched = match_user_cost_project(user_name, options)
    if matched:
        return matched

    parent = await client.get_work_item(parent_id)
    parent_value = client.read_cost_project_value(parent)
    if parent_value:
        return parent_value

    configured_default = (settings.cost_project_default or "").strip()
    if configured_default:
        return configured_default

    if options:
        return options[0]

    raise ValueError("Не удалось определить проект учёта затрат для текущего пользователя")


async def get_cost_project_options_for_item(client: TfsClient, parent_id: int) -> dict[str, Any]:
    parent = await client.get_work_item(parent_id)
    parent_value = client.read_cost_project_value(parent)

    from_tasks = await client.collect_cost_projects_from_child_tasks(parent_id)
    options = await client.get_cost_project_options(settings.task_type_name)
    options = client._merge_cost_project_options(from_tasks, options)

    if parent_value:
        options = client._merge_cost_project_options([parent_value], options)

    configured_default = (settings.cost_project_default or "").strip()
    if configured_default:
        options = client._merge_cost_project_options([configured_default], options)

    # На ЗНИ поля нет — сначала дочерняя «Задача», иначе B2B 2026 из конфига.
    task_value = from_tasks[0] if from_tasks else None
    default_value = parent_value or task_value or configured_default or None
    if default_value:
        options = client._merge_cost_project_options([default_value], options)
    if not default_value and len(options) == 1:
        default_value = options[0]

    return {
        "fieldName": settings.cost_project_field,
        "options": options,
        "defaultValue": default_value,
        "parentValue": parent_value or task_value,
    }


async def log_time_entry(
    db: Session,
    auth: TfsAuth,
    *,
    parent_work_item_id: int,
    role: str,
    activity: str,
    entry_date: date,
    hours: float,
    minutes: int,
    subtract: bool,
    comment: str | None,
    cost_project: str | None = None,
) -> TimeEntryOut:
    amount = total_hours(hours, minutes)
    if amount <= 0:
        raise ValueError("Укажите время больше нуля")
    signed = -amount if subtract else amount

    client = TfsClient(auth)
    try:
        parent_item = await client.get_work_item(parent_work_item_id)
        parent = client.normalize_item(parent_item)
        touch_recent(db, auth, parent)

        resolved_cost_project = await resolve_cost_project(
            client,
            parent_id=parent_work_item_id,
            explicit=cost_project,
        )

        tracking_id = await ensure_tracking_task(
            client,
            parent_id=parent_work_item_id,
            role=role,
            activity=activity,
            cost_project=resolved_cost_project,
        )

        from app.tfs_auth import tfs_login_unique_name
        from app.tfs_tsapi import TfsTsapiClient

        tsapi_user = tfs_login_unique_name(auth) or owner_unique_name_for(auth)
        tsapi_saved = False
        if settings.tfs_tsapi_enabled and tsapi_user:
            minutes = int(round(amount * 60))
            tsapi = TfsTsapiClient(auth)
            try:
                await tsapi.save_delta(
                    work_item_id=tracking_id,
                    period_date=entry_date,
                    duration_minutes=minutes,
                    user_id=tsapi_user,
                    comment=comment,
                )
                tsapi_saved = True
            except Exception as exc:
                raise ValueError(
                    f"Не удалось записать списание в TFS «Время» (tsapi): {exc}"
                ) from exc
            finally:
                await tsapi.close()

        entry = TimeEntry(
            account_key=auth.account_key,
            parent_work_item_id=parent_work_item_id,
            tracking_work_item_id=tracking_id,
            role=role,
            activity=activity,
            entry_date=entry_date,
            hours=signed,
            comment=comment,
            cost_project=resolved_cost_project,
            owner_unique_name=owner_unique_name_for(auth),
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)

        if not settings.tfs_tsapi_enabled:
            total_for_task = db.scalar(
                select(func.coalesce(func.sum(TimeEntry.hours), 0.0)).where(
                    TimeEntry.account_key == auth.account_key,
                    TimeEntry.tracking_work_item_id == tracking_id,
                )
            )
            history = (
                f"{entry_date.isoformat()}: {'-' if subtract else '+'}{amount}ч — {comment or role}"
            )
            await client.update_completed_work(
                tracking_id, float(total_for_task or 0), comment=history
            )
    finally:
        await client.close()

    return TimeEntryOut(
        id=entry.id,
        parent_work_item_id=entry.parent_work_item_id,
        tracking_work_item_id=entry.tracking_work_item_id,
        role=entry.role,
        activity=entry.activity,
        entry_date=entry.entry_date,
        hours=entry.hours,
        comment=entry.comment,
        cost_project=entry.cost_project,
        created_at=entry.created_at,
    )


def build_timesheet(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
    parent_items: list[dict[str, Any]],
    children_by_parent: dict[int, list[dict[str, Any]]] | None = None,
) -> TimesheetOut:
    end = period_end(period_start, view)
    entries = dedupe_time_entries(
        filter_entries_for_user(
            list(
                db.scalars(
                    select(TimeEntry).where(
                        TimeEntry.account_key == auth.account_key,
                        TimeEntry.entry_date >= period_start,
                        TimeEntry.entry_date <= end,
                    )
                ).all()
            ),
            auth,
        )
    )

    by_parent: dict[int, list[TimeEntry]] = defaultdict(list)
    for entry in entries:
        by_parent[entry.parent_work_item_id].append(entry)

    groups: list[TimesheetGroupOut] = []
    closed_groups: list[TimesheetGroupOut] = []

    parent_map = {int(item["id"]): item for item in parent_items}
    seen_parents = set(by_parent.keys())
    tracking_by_parent: dict[int, set[int]] = defaultdict(set)
    if seen_parents:
        for parent_id, tracking_id in db.execute(
            select(TimeEntry.parent_work_item_id, TimeEntry.tracking_work_item_id)
            .where(
                TimeEntry.account_key == auth.account_key,
                TimeEntry.parent_work_item_id.in_(seen_parents),
                TimeEntry.tracking_work_item_id.isnot(None),
            )
            .distinct()
        ):
            if tracking_id is not None:
                tracking_by_parent[int(parent_id)].add(int(tracking_id))

    for parent_id in sorted(seen_parents):
        item = parent_map.get(parent_id)
        if not item and parent_id in by_parent:
            item = {
                "id": parent_id,
                "title": f"#{parent_id}",
                "workItemType": "",
                "state": "",
                "areaPath": "",
                "kind": "other",
                "tfsUrl": "",
                "completedWork": 0,
            }
        if not item:
            continue

        parent_out = work_item_out(item)
        parent_entries = by_parent.get(parent_id, [])
        tracking_map: dict[tuple[str, str, int | None], TrackingRowOut] = {}
        entries_by_row: dict[tuple[str, str, int | None], list[TimeEntry]] = defaultdict(list)

        known_tracking_ids = set(tracking_by_parent.get(parent_id, set()))

        for entry in parent_entries:
            key = _tracking_row_key(entry)
            entries_by_row[key].append(entry)
            if key not in tracking_map:
                tracking_map[key] = TrackingRowOut(
                    tracking_work_item_id=entry.tracking_work_item_id,
                    role=entry.role,
                    activity=entry.activity,
                    title=tracking_title(entry.role, entry.activity),
                )
            row = tracking_map[key]
            day_key = entry.entry_date.isoformat()
            row.daily_hours[day_key] = round(row.daily_hours.get(day_key, 0) + entry.hours, 2)
            row.total_hours = round(row.total_hours + entry.hours, 2)
            if entry.tracking_work_item_id is not None:
                known_tracking_ids.add(entry.tracking_work_item_id)

        for key, row in tracking_map.items():
            role, activity = _pick_display_role_activity(entries_by_row[key])
            row.role = role
            row.activity = activity
            row.title = tracking_title(role, activity)

        tracking_rows = [
            row for row in tracking_map.values() if row.total_hours > 0
        ]
        group_total = round(sum(row.total_hours for row in tracking_rows), 2)
        if group_total <= 0:
            continue
        group = TimesheetGroupOut(
            parent=parent_out,
            children=[],
            tracking_rows=sorted(tracking_rows, key=lambda row: row.title),
            total_hours=group_total,
        )
        if parent_out.state in CLOSED_STATES:
            closed_groups.append(group)
        else:
            groups.append(group)

    day_totals, total_hours = sum_groups_hours(groups)
    closed_day_totals, closed_total_hours = sum_groups_hours(closed_groups)

    return TimesheetOut(
        period_start=period_start,
        period_end=end,
        view="month" if view == "month" else "week",
        day_totals=day_totals,
        total_hours=total_hours,
        closed_day_totals=closed_day_totals,
        closed_total_hours=closed_total_hours,
        groups=groups,
        closed_groups=closed_groups,
    )


def build_calendar_month(db: Session, auth: TfsAuth, *, year: int, month: int) -> MonthCalendarOut:
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    entries = dedupe_time_entries(
        filter_entries_for_user(
            list(
                db.scalars(
                    select(TimeEntry).where(
                        TimeEntry.account_key == auth.account_key,
                        TimeEntry.entry_date >= start,
                        TimeEntry.entry_date <= end,
                    )
                ).all()
            ),
            auth,
        )
    )
    by_day: dict[date, tuple[float, int]] = defaultdict(lambda: (0.0, 0))
    for entry in entries:
        hours, count = by_day[entry.entry_date]
        by_day[entry.entry_date] = (hours + entry.hours, count + 1)

    days = [
        CalendarDayOut(date=day, hours=round(hours, 2), entries_count=count)
        for day, (hours, count) in sorted(by_day.items())
    ]
    total = round(sum(day.hours for day in days), 2)
    return MonthCalendarOut(year=year, month=month, days=days, total_hours=total)


def build_calendar(
    db: Session,
    auth: TfsAuth,
    *,
    scope: str,
    year: int,
    month: int | None = None,
    quarter: int | None = None,
) -> CalendarOut:
    if scope == "month":
        resolved_month = month or date.today().month
        month_data = build_calendar_month(db, auth, year=year, month=resolved_month)
        return CalendarOut(
            scope="month",
            year=year,
            month=resolved_month,
            days=month_data.days,
            months=[month_data],
            total_hours=month_data.total_hours,
        )

    if scope == "quarter":
        resolved_quarter = quarter or quarter_for_month(month or date.today().month)
        months = [
            build_calendar_month(db, auth, year=year, month=quarter_start_month(resolved_quarter) + offset)
            for offset in range(3)
        ]
        total = round(sum(item.total_hours for item in months), 2)
        return CalendarOut(
            scope="quarter",
            year=year,
            quarter=resolved_quarter,
            months=months,
            total_hours=total,
        )

    months = [build_calendar_month(db, auth, year=year, month=item) for item in range(1, 13)]
    total = round(sum(item.total_hours for item in months), 2)
    return CalendarOut(scope="year", year=year, months=months, total_hours=total)


def count_weekday_goal(start: date, end: date, *, through: date | None = None) -> float:
    """Норма: 8ч за каждый пн–пт в периоде (до through включительно, если задан)."""
    cursor = start
    days = 0
    limit = through or end
    while cursor <= end and cursor <= limit:
        if cursor.weekday() < 5:
            days += 1
        cursor += timedelta(days=1)
    return float(days * 8)


def get_stats_summary(db: Session, auth: TfsAuth) -> dict[str, Any]:
    today = date.today()
    start = week_start(today)
    end = start + timedelta(days=6)

    week_entries = dedupe_time_entries(
        filter_entries_for_user(
            list(
                db.scalars(
                    select(TimeEntry).where(
                        TimeEntry.account_key == auth.account_key,
                        TimeEntry.entry_date >= start,
                        TimeEntry.entry_date <= end,
                    )
                ).all()
            ),
            auth,
        )
    )
    today_hours = sum_deduped_hours(week_entries, on_date=today)
    week_hours = sum_deduped_hours(week_entries, period_start=start, period_end=end)

    return {
        "todayHours": today_hours,
        "todayGoal": 8.0,
        "weekHours": round(week_hours, 2),
        "weekGoal": count_weekday_goal(start, end, through=today),
        "weekStart": start,
        "weekEnd": end,
    }


def list_recent_entries(db: Session, auth: TfsAuth, *, limit: int = 8) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(TimeEntry)
        .where(
            TimeEntry.account_key == auth.account_key,
            entry_ownership_clause(auth),
        )
        .order_by(desc(TimeEntry.created_at))
        .limit(max(limit * 4, limit))
    ).all()

    deduped_rows = dedupe_time_entries(list(rows))
    deduped_rows.sort(key=lambda item: item.created_at, reverse=True)
    rows = deduped_rows[:limit]

    parent_ids = {row.parent_work_item_id for row in rows}
    titles: dict[int, tuple[str, str]] = {}
    if parent_ids:
        recent_rows = db.scalars(
            select(RecentWorkItem).where(
                RecentWorkItem.account_key == auth.account_key,
                RecentWorkItem.work_item_id.in_(parent_ids),
            )
        ).all()
        for recent in recent_rows:
            titles[recent.work_item_id] = (recent.title, recent.kind)

    result: list[dict[str, Any]] = []
    for row in rows:
        title, kind = titles.get(row.parent_work_item_id, (f"#{row.parent_work_item_id}", "other"))
        result.append(
            {
                "id": row.id,
                "parentWorkItemId": row.parent_work_item_id,
                "parentTitle": title,
                "parentKind": kind,
                "role": row.role,
                "activity": row.activity,
                "entryDate": row.entry_date,
                "hours": row.hours,
                "costProject": row.cost_project,
                "createdAt": row.created_at,
            }
        )
    return result
