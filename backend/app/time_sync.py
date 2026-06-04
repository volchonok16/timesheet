from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import TimeEntry
from app.tfs_auth import TfsAuth
from app.tfs_client import TfsClient
from app.time_service import (
    ROLE_LABELS,
    list_recent,
    parse_tracking_title,
    period_end,
    timesheet_parent_ids,
    touch_recent,
)

HISTORY_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}):\s*([+-])([\d.,]+)ч(?:\s*[—-]\s*(.*))?$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ParsedTimeSlice:
    entry_date: date
    hours: float
    comment: str | None
    sync_key: str


def _parse_hours_token(token: str) -> float:
    normalized = token.strip().replace(",", ".")
    return round(float(normalized), 2)


def _parse_revised_date(raw: Any) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str) and raw:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    return date.today()


def _completed_work_delta(fields: dict[str, Any]) -> float | None:
    change = fields.get("Microsoft.VSTS.Scheduling.CompletedWork")
    if not isinstance(change, dict):
        return None
    old_raw = change.get("oldValue")
    new_raw = change.get("newValue")
    old_val = 0.0 if old_raw in (None, "") else float(old_raw)
    new_val = 0.0 if new_raw in (None, "") else float(new_raw)
    delta = round(new_val - old_val, 2)
    if abs(delta) < 0.01:
        return None
    return delta


def parse_update_time_slices(
    update: dict[str, Any],
    *,
    tracking_work_item_id: int,
) -> list[ParsedTimeSlice]:
    fields = as_update_fields(update)
    rev = int(update.get("rev") or update.get("id") or 0)
    revised_date = _parse_revised_date(update.get("revisedDate"))

    history_raw = fields.get("System.History")
    history_text = ""
    if isinstance(history_raw, dict):
        history_text = str(history_raw.get("newValue") or "")
    elif isinstance(history_raw, str):
        history_text = history_raw

    slices: list[ParsedTimeSlice] = []
    for index, match in enumerate(HISTORY_LINE.finditer(history_text)):
        entry_date = date.fromisoformat(match.group(1))
        sign = -1.0 if match.group(2) == "-" else 1.0
        hours = round(sign * _parse_hours_token(match.group(3)), 2)
        if abs(hours) < 0.01:
            continue
        comment = (match.group(4) or "").strip() or None
        slices.append(
            ParsedTimeSlice(
                entry_date=entry_date,
                hours=hours,
                comment=comment,
                sync_key=f"tfs:{tracking_work_item_id}:rev{rev}:h{index}",
            )
        )

    if slices:
        return slices

    delta = _completed_work_delta(fields)
    if delta is None:
        return []

    return [
        ParsedTimeSlice(
            entry_date=revised_date,
            hours=delta,
            comment="Импорт из TFS (Completed Work)",
            sync_key=f"tfs:{tracking_work_item_id}:rev{rev}:cw",
        )
    ]


def as_update_fields(update: dict[str, Any]) -> dict[str, Any]:
    fields = update.get("fields")
    return fields if isinstance(fields, dict) else {}


def entry_in_period(entry_date: date, *, period_start: date, period_end: date) -> bool:
    return period_start <= entry_date <= period_end


def is_tracking_child_item(child: dict[str, Any]) -> bool:
    title = str(child.get("title") or "")
    if " - " not in title:
        return False
    role, _activity = parse_tracking_title(title)
    return role in ROLE_LABELS


def week_delta_from_slices(
    slices: list[ParsedTimeSlice],
    *,
    period_start: date,
) -> list[float]:
    """Как в Oscar: 7 чисел — часы по дням недели с period_start (понедельник)."""
    end = period_start + timedelta(days=6)
    daily: dict[date, float] = {}
    for slice_ in slices:
        if period_start <= slice_.entry_date <= end:
            daily[slice_.entry_date] = round(daily.get(slice_.entry_date, 0) + slice_.hours, 2)
    return [round(daily.get(period_start + timedelta(days=i), 0), 2) for i in range(7)]


def aggregate_slices_for_task(
    updates: list[dict[str, Any]],
    *,
    tracking_work_item_id: int,
) -> list[ParsedTimeSlice]:
    merged: list[ParsedTimeSlice] = []
    for update in updates:
        merged.extend(
            parse_update_time_slices(update, tracking_work_item_id=tracking_work_item_id)
        )
    return merged


async def collect_tracking_task_ids(
    client: TfsClient,
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
) -> list[int]:
    """
    Как Oscar stream_get-time-tracking-results: недавние родители → дочерние «Роль - Активность».
    Плюс WIQL по задачам с Completed Work (с запасом по дате изменения).
    """
    seen: set[int] = set()
    ordered: list[int] = []

    def add(task_id: int) -> None:
        if task_id not in seen:
            seen.add(task_id)
            ordered.append(task_id)

    parent_ids: list[int] = []
    for row in list_recent(db, auth, limit=40):
        parent_ids.append(row.id)
    parent_ids.extend(
        timesheet_parent_ids(db, auth, period_start=period_start, view=view, recent_limit=40)
    )
    parent_seen: set[int] = set()
    for parent_id in parent_ids:
        if parent_id in parent_seen:
            continue
        parent_seen.add(parent_id)
        for child in await client.get_child_tasks(parent_id):
            if not is_tracking_child_item(child):
                continue
            add(int(child["id"]))

    lookback = period_start - timedelta(days=90)
    wiql_ids = await client.find_task_ids_with_completed_work(
        changed_since=lookback,
        limit=settings.tfs_sync_max_tasks,
    )
    for task_id in wiql_ids:
        add(task_id)

    return ordered[: settings.tfs_sync_max_tasks]


def sync_key_exists(db: Session, auth: TfsAuth, sync_key: str) -> bool:
    existing = db.scalar(
        select(TimeEntry.id).where(
            TimeEntry.account_key == auth.account_key,
            TimeEntry.tfs_sync_key == sync_key,
        )
    )
    return existing is not None


async def sync_time_from_tfs(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
) -> dict[str, int]:
    end = period_end(period_start, view)
    client = TfsClient(auth)
    imported = 0
    skipped = 0
    tasks_scanned = 0

    try:
        task_ids = await collect_tracking_task_ids(
            client, db, auth, period_start=period_start, view=view
        )
        local_tracking_ids = db.scalars(
            select(TimeEntry.tracking_work_item_id)
            .where(
                TimeEntry.account_key == auth.account_key,
                TimeEntry.tracking_work_item_id.isnot(None),
                TimeEntry.entry_date >= period_start,
                TimeEntry.entry_date <= end,
            )
            .distinct()
        ).all()
        for raw_id in local_tracking_ids:
            if raw_id is not None and int(raw_id) not in task_ids:
                task_ids.append(int(raw_id))

        user_hint = (await client.get_authenticated_user_name() or auth.username or "").casefold()

        for task_id in task_ids:
            tasks_scanned += 1
            parent_id = await client.get_parent_work_item_id(task_id)
            if parent_id is None:
                skipped += 1
                continue

            task_item = await client.get_work_item(task_id)
            fields = task_item.get("fields") or {}
            if user_hint:
                assigned = str(fields.get("System.AssignedTo") or "").casefold()
                if assigned and user_hint not in assigned:
                    uname = (auth.username or "").casefold()
                    if not uname or uname not in assigned:
                        skipped += 1
                        continue

            title = str(fields.get("System.Title") or f"#{task_id}")
            role, activity = parse_tracking_title(title)
            if not role:
                role = "—"
            if not activity:
                activity = title

            updates = await client.get_work_item_updates(task_id)
            parent_item = await client.get_work_item(parent_id)
            parent_norm = client.normalize_item(parent_item)

            # Все ревизии (как Oscar): даты берём из истории «YYYY-MM-DD: +Nч», не из revisedDate.
            for slice_ in aggregate_slices_for_task(
                updates, tracking_work_item_id=task_id
            ):
                if not entry_in_period(slice_.entry_date, period_start=period_start, period_end=end):
                    continue
                if sync_key_exists(db, auth, slice_.sync_key):
                    skipped += 1
                    continue

                db.add(
                    TimeEntry(
                        account_key=auth.account_key,
                        parent_work_item_id=parent_id,
                        tracking_work_item_id=task_id,
                        role=role,
                        activity=activity,
                        entry_date=slice_.entry_date,
                        hours=slice_.hours,
                        comment=slice_.comment,
                        cost_project=client.read_cost_project_value(task_item)
                        or client.read_cost_project_value(parent_item),
                        tfs_sync_key=slice_.sync_key,
                    )
                )
                imported += 1

            touch_recent(db, auth, parent_norm)

        db.commit()
    finally:
        await client.close()

    return {
        "imported": imported,
        "skipped": skipped,
        "tasks_scanned": tasks_scanned,
        "period_start": period_start,
        "period_end": end,
    }
