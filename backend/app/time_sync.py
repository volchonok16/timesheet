from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import AccountSyncState, TimeEntry
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


@dataclass(frozen=True)
class TrackingTarget:
    task_id: int
    parent_id: int


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


def filter_updates_for_sync(
    updates: list[dict[str, Any]],
    *,
    period_start: date,
) -> list[dict[str, Any]]:
    """Не разбираем всю историю с начала времён — только релевантные ревизии."""
    cutoff = period_start - timedelta(days=settings.tfs_sync_update_lookback_days)
    filtered: list[dict[str, Any]] = []
    for update in updates:
        revised = _parse_revised_date(update.get("revisedDate"))
        if revised < cutoff:
            continue
        fields = as_update_fields(update)
        if "Microsoft.VSTS.Scheduling.CompletedWork" in fields or "System.History" in fields:
            filtered.append(update)
    return filtered


def aggregate_slices_for_task(
    updates: list[dict[str, Any]],
    *,
    tracking_work_item_id: int,
    period_start: date,
) -> list[ParsedTimeSlice]:
    merged: list[ParsedTimeSlice] = []
    for update in filter_updates_for_sync(updates, period_start=period_start):
        merged.extend(
            parse_update_time_slices(update, tracking_work_item_id=tracking_work_item_id)
        )
    return merged


def should_run_sync(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
    force: bool,
) -> bool:
    if force:
        return True
    row = db.scalar(
        select(AccountSyncState).where(
            AccountSyncState.account_key == auth.account_key,
            AccountSyncState.period_start == period_start,
            AccountSyncState.view == view,
        )
    )
    if row is None:
        return True
    age = (datetime.utcnow() - row.synced_at).total_seconds()
    return age >= settings.tfs_sync_ttl_seconds


def mark_synced(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
) -> None:
    row = db.scalar(
        select(AccountSyncState).where(
            AccountSyncState.account_key == auth.account_key,
            AccountSyncState.period_start == period_start,
            AccountSyncState.view == view,
        )
    )
    now = datetime.utcnow()
    if row is None:
        db.add(
            AccountSyncState(
                account_key=auth.account_key,
                period_start=period_start,
                view=view,
                synced_at=now,
            )
        )
    else:
        row.synced_at = now


def load_existing_sync_keys(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    period_end: date,
) -> set[str]:
    rows = db.scalars(
        select(TimeEntry.tfs_sync_key).where(
            TimeEntry.account_key == auth.account_key,
            TimeEntry.tfs_sync_key.isnot(None),
            TimeEntry.entry_date >= period_start,
            TimeEntry.entry_date <= period_end,
        )
    ).all()
    return {str(key) for key in rows if key}


async def collect_tracking_targets(
    client: TfsClient,
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
    include_wiql: bool,
) -> list[TrackingTarget]:
    seen: set[int] = set()
    targets: list[TrackingTarget] = []

    def add(task_id: int, parent_id: int) -> None:
        if task_id not in seen:
            seen.add(task_id)
            targets.append(TrackingTarget(task_id=task_id, parent_id=parent_id))

    parent_ids: list[int] = []
    for row in list_recent(db, auth, limit=settings.tfs_sync_max_parents):
        parent_ids.append(row.id)
    parent_ids.extend(
        timesheet_parent_ids(
            db,
            auth,
            period_start=period_start,
            view=view,
            recent_limit=settings.tfs_sync_max_parents,
        )
    )
    parent_seen: set[int] = set()
    unique_parents: list[int] = []
    for parent_id in parent_ids:
        if parent_id not in parent_seen:
            parent_seen.add(parent_id)
            unique_parents.append(parent_id)
    unique_parents = unique_parents[: settings.tfs_sync_max_parents]

    if unique_parents:
        child_lists = await asyncio.gather(
            *[client.get_child_tasks(pid) for pid in unique_parents],
            return_exceptions=True,
        )
        for parent_id, children in zip(unique_parents, child_lists):
            if isinstance(children, BaseException):
                continue
            for child in children:
                if not is_tracking_child_item(child):
                    continue
                add(int(child["id"]), parent_id)

    if include_wiql:
        lookback = period_start - timedelta(days=settings.tfs_sync_update_lookback_days)
        wiql_ids = await client.find_task_ids_with_completed_work(
            changed_since=lookback,
            limit=settings.tfs_sync_max_tasks,
        )
        for task_id in wiql_ids:
            if task_id in seen:
                continue
            parent_id = await client.get_parent_work_item_id(task_id)
            if parent_id is not None:
                add(task_id, parent_id)

    return targets[: settings.tfs_sync_max_tasks]


def _assigned_to_user(fields: dict[str, Any], user_hint: str, username: str | None) -> bool:
    if not user_hint:
        return True
    assigned = str(fields.get("System.AssignedTo") or "").casefold()
    if not assigned:
        return True
    if user_hint in assigned:
        return True
    uname = (username or "").casefold()
    return bool(uname and uname in assigned)


async def sync_time_from_tfs(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
    force: bool = False,
) -> dict[str, Any]:
    end = period_end(period_start, view)
    if not should_run_sync(db, auth, period_start=period_start, view=view, force=force):
        return {
            "imported": 0,
            "skipped": 0,
            "tasks_scanned": 0,
            "period_start": period_start,
            "period_end": end,
            "cached": True,
        }

    client = TfsClient(auth)
    imported = 0
    skipped = 0
    tasks_scanned = 0
    parents_touched: set[int] = set()
    existing_keys = load_existing_sync_keys(
        db, auth, period_start=period_start, period_end=end
    )

    try:
        targets = await collect_tracking_targets(
            client,
            db,
            auth,
            period_start=period_start,
            view=view,
            include_wiql=force,
        )
        local_tracking_ids = db.scalars(
            select(TimeEntry.tracking_work_item_id, TimeEntry.parent_work_item_id)
            .where(
                TimeEntry.account_key == auth.account_key,
                TimeEntry.tracking_work_item_id.isnot(None),
                TimeEntry.entry_date >= period_start,
                TimeEntry.entry_date <= end,
            )
            .distinct()
        ).all()
        seen_tasks = {t.task_id for t in targets}
        for tracking_id, parent_id in local_tracking_ids:
            if tracking_id is None or parent_id is None:
                continue
            tid, pid = int(tracking_id), int(parent_id)
            if tid not in seen_tasks:
                targets.append(TrackingTarget(task_id=tid, parent_id=pid))
                seen_tasks.add(tid)

        targets = targets[: settings.tfs_sync_max_tasks]
        if not targets:
            mark_synced(db, auth, period_start=period_start, view=view)
            db.commit()
            return {
                "imported": 0,
                "skipped": 0,
                "tasks_scanned": 0,
                "period_start": period_start,
                "period_end": end,
                "cached": False,
            }

        user_hint = (await client.get_authenticated_user_name() or auth.username or "").casefold()
        task_ids = [target.task_id for target in targets]
        parent_ids = list({target.parent_id for target in targets})

        def _raw_item_id(item: dict[str, Any]) -> int:
            raw_id = item.get("id")
            if raw_id is not None:
                return int(raw_id)
            fields = item.get("fields") or {}
            return int(fields["System.Id"])

        raw_tasks = await client.get_work_items_batch(
            task_ids,
            fields=[
                "System.Id",
                "System.Title",
                "System.AssignedTo",
                settings.cost_project_field,
            ],
        )
        raw_parents = await client.get_work_items_batch(parent_ids)
        tasks_by_id = {_raw_item_id(item): item for item in raw_tasks}
        parents_by_id = {_raw_item_id(item): item for item in raw_parents}

        sem = asyncio.Semaphore(max(1, settings.tfs_sync_parallel))

        async def process_target(target: TrackingTarget) -> tuple[int, int]:
            async with sem:
                task_id = target.task_id
                parent_id = target.parent_id
                task_item = tasks_by_id.get(task_id)
                if task_item is None:
                    return 0, 1

                fields = task_item.get("fields") or {}
                if not _assigned_to_user(fields, user_hint, auth.username):
                    return 0, 1

                title = str(fields.get("System.Title") or f"#{task_id}")
                role, activity = parse_tracking_title(title)
                if not role:
                    role = "—"
                if not activity:
                    activity = title

                updates = await client.get_work_item_updates(task_id)
                parent_item = parents_by_id.get(parent_id)
                cost_project = client.read_cost_project_value(task_item)
                if not cost_project and parent_item:
                    cost_project = client.read_cost_project_value(parent_item)

                local_imported = 0
                local_skipped = 0
                for slice_ in aggregate_slices_for_task(
                    updates,
                    tracking_work_item_id=task_id,
                    period_start=period_start,
                ):
                    if not entry_in_period(
                        slice_.entry_date, period_start=period_start, period_end=end
                    ):
                        continue
                    if slice_.sync_key in existing_keys:
                        local_skipped += 1
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
                            cost_project=cost_project,
                            tfs_sync_key=slice_.sync_key,
                        )
                    )
                    existing_keys.add(slice_.sync_key)
                    local_imported += 1

                parents_touched.add(parent_id)
                return local_imported, local_skipped

        results = await asyncio.gather(
            *[process_target(target) for target in targets],
            return_exceptions=True,
        )
        for result in results:
            tasks_scanned += 1
            if isinstance(result, BaseException):
                skipped += 1
                continue
            imp, sk = result
            imported += imp
            skipped += sk

        for parent_id in parents_touched:
            parent_item = parents_by_id.get(parent_id)
            if parent_item:
                touch_recent(db, auth, client.normalize_item(parent_item))

        mark_synced(db, auth, period_start=period_start, view=view)
        db.commit()
    finally:
        await client.close()

    return {
        "imported": imported,
        "skipped": skipped,
        "tasks_scanned": tasks_scanned,
        "period_start": period_start,
        "period_end": end,
        "cached": False,
    }
