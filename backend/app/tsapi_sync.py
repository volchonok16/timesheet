"""Синхронизация недели из TFS tsapi (PeriodDate + Duration), без Oscar и без History."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import AccountSyncState, TimeEntry
from app.tfs_auth import TfsAuth
from app.tfs_client import TfsClient
from app.tfs_tsapi import TfsTsapiClient, TsapiDeltaRow, delta_user_matches_auth
from app.time_service import (
    delete_duplicate_entries_in_period,
    owner_unique_name_for,
    parse_tracking_title,
    period_end,
    touch_recent,
)
from app.time_sync import (
    collect_tracking_targets,
    is_tracking_child_item,
    load_existing_sync_keys,
    mark_synced,
    purge_imported_tfs_entries,
    should_run_sync,
)

TSAPI_SYNC_PREFIX = "tsapi:"


def tsapi_sync_key(delta_id: int) -> str:
    return f"{TSAPI_SYNC_PREFIX}{delta_id}"


@dataclass(frozen=True)
class PendingTsapiEntry:
    parent_id: int
    task_id: int
    role: str
    activity: str
    row: TsapiDeltaRow


def _title_for_target(titles_by_id: dict[int, str], task_id: int) -> tuple[str, str]:
    title = titles_by_id.get(task_id) or f"#{task_id}"
    role, activity = parse_tracking_title(title)
    if not role:
        role = "—"
    if not activity:
        activity = title
    return role, activity


def _should_scan_tracking_task(title: str, task_id: int) -> bool:
    if not title:
        return True
    return is_tracking_child_item({"id": task_id, "title": title, "kind": "task"})


async def _collect_pending_tsapi_entries(
    *,
    targets: list,
    titles_by_id: dict[int, str],
    tsapi: TfsTsapiClient,
    auth: TfsAuth,
    period_start: date,
    period_end: date,
) -> tuple[list[PendingTsapiEntry], int, int]:
    pending: list[PendingTsapiEntry] = []
    tasks_scanned = 0
    skipped = 0

    for target in targets:
        title = titles_by_id.get(target.task_id, "")
        if title and not _should_scan_tracking_task(title, target.task_id):
            skipped += 1
            continue
        tasks_scanned += 1
        try:
            deltas = await tsapi.get_work_item_deltas(target.task_id)
        except Exception:
            skipped += 1
            continue

        role, activity = _title_for_target(titles_by_id, target.task_id)
        for row in deltas:
            if not delta_user_matches_auth(row.user_id, auth):
                continue
            if row.period_date < period_start or row.period_date > period_end:
                continue
            pending.append(
                PendingTsapiEntry(
                    parent_id=target.parent_id,
                    task_id=target.task_id,
                    role=role,
                    activity=activity,
                    row=row,
                )
            )

    return pending, tasks_scanned, skipped


def _clear_sync_state(
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
    if row is not None:
        db.delete(row)


async def sync_from_tsapi(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
    force: bool = False,
    user_tokens: set[str] | None = None,
    user_strong_tokens: set[str] | None = None,
) -> dict[str, Any]:
    end = period_end(period_start, view)
    if not settings.tfs_tsapi_enabled:
        return {
            "imported": 0,
            "skipped": 0,
            "tasks_scanned": 0,
            "purged": 0,
            "period_start": period_start,
            "period_end": end,
            "cached": False,
            "source": "tsapi",
            "message": "TFS tsapi отключён (TFS_TSAPI_ENABLED=false).",
        }

    if not should_run_sync(db, auth, period_start=period_start, view=view, force=force):
        return {
            "imported": 0,
            "skipped": 0,
            "tasks_scanned": 0,
            "purged": 0,
            "period_start": period_start,
            "period_end": end,
            "cached": True,
            "source": "tsapi",
        }

    owner_key = owner_unique_name_for(auth)
    if not owner_key:
        return {
            "imported": 0,
            "skipped": 0,
            "tasks_scanned": 0,
            "purged": 0,
            "period_start": period_start,
            "period_end": end,
            "cached": False,
            "source": "tsapi",
            "message": "Укажите логин TFS при входе.",
        }

    tokens = user_tokens or auth.identity_match_tokens()
    strong = user_strong_tokens or auth.identity_strong_tokens()

    tfs = TfsClient(auth)
    try:
        targets = await collect_tracking_targets(
            tfs,
            db,
            auth,
            period_start=period_start,
            view=view,
            include_wiql=True,
            current_user_tokens=tokens,
            current_user_strong_tokens=strong,
        )
        targets = targets[: settings.tfs_sync_max_tasks]
        task_ids = [t.task_id for t in targets]
        parent_ids = list({t.parent_id for t in targets})
        titles_by_id: dict[int, str] = {}
        if task_ids:
            for item in await tfs.get_work_items_batch(task_ids, fields=["System.Id", "System.Title"]):
                fields = item.get("fields") or {}
                tid = int(item.get("id") or fields.get("System.Id") or 0)
                if tid:
                    titles_by_id[tid] = str(fields.get("System.Title") or "")
        if parent_ids:
            for item in await tfs.get_work_items_batch(parent_ids):
                touch_recent(db, auth, tfs.normalize_item(item))
    finally:
        await tfs.close()

    tsapi = TfsTsapiClient(auth)
    try:
        pending, tasks_scanned, skipped = await _collect_pending_tsapi_entries(
            targets=targets,
            titles_by_id=titles_by_id,
            tsapi=tsapi,
            auth=auth,
            period_start=period_start,
            period_end=end,
        )
    finally:
        await tsapi.close()

    purged = 0
    if force and pending:
        purged = purge_imported_tfs_entries(
            db, auth, period_start=period_start, period_end=end
        )
    elif force and not pending:
        _clear_sync_state(db, auth, period_start=period_start, view=view)
        db.commit()
        return {
            "imported": 0,
            "skipped": skipped,
            "tasks_scanned": tasks_scanned,
            "purged": 0,
            "period_start": period_start,
            "period_end": end,
            "cached": False,
            "source": "tsapi",
            "message": (
                "TFS «Время»: за неделю нет ваших ListDelta (PeriodDate). "
                "Старые строки в табеле не удалены."
            ),
        }

    existing_keys = load_existing_sync_keys(
        db, auth, period_start=period_start, period_end=end
    )

    imported = 0
    for item in pending:
        sync_key = tsapi_sync_key(item.row.delta_id)
        if sync_key in existing_keys:
            skipped += 1
            continue
        db.add(
            TimeEntry(
                account_key=auth.account_key,
                parent_work_item_id=item.parent_id,
                tracking_work_item_id=item.task_id,
                role=item.role,
                activity=item.activity,
                entry_date=item.row.period_date,
                hours=item.row.hours,
                comment=item.row.comment,
                cost_project=None,
                tfs_sync_key=sync_key,
                owner_unique_name=owner_key,
            )
        )
        existing_keys.add(sync_key)
        imported += 1

    removed_dupes = delete_duplicate_entries_in_period(
        db, auth, period_start=period_start, period_end=end
    )
    if imported > 0 or not force:
        mark_synced(db, auth, period_start=period_start, view=view)
    else:
        _clear_sync_state(db, auth, period_start=period_start, view=view)
    db.commit()

    return {
        "imported": imported,
        "skipped": skipped,
        "tasks_scanned": tasks_scanned,
        "purged": purged,
        "removed_dupes": removed_dupes,
        "period_start": period_start,
        "period_end": end,
        "cached": False,
        "source": "tsapi",
        "message": (
            None
            if imported > 0
            else (
                "TFS «Время»: нет новых строк ListDelta за неделю."
                if not force
                else "TFS «Время»: ListDelta за неделю пустой после пересборки."
            )
        ),
    }
