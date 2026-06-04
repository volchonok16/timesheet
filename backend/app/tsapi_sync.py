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
from app.tfs_tsapi import (
    TfsTsapiClient,
    TsapiDeltaRow,
    delta_user_matches_auth,
    probe_tsapi_list_delta_rows,
)
from app.time_service import (
    delete_duplicate_entries_in_period,
    owner_unique_name_for,
    parse_tracking_title,
    period_end,
    touch_recent,
)
from app.time_sync import (
    TrackingTarget,
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


async def _ensure_probe_tracking_target(
    tfs: TfsClient, targets: list, *, probe_task_id: int | None
) -> list:
    if not probe_task_id:
        return targets
    if any(t.task_id == probe_task_id for t in targets):
        return targets
    parent_id = await tfs.get_parent_work_item_id(probe_task_id)
    if parent_id is None:
        return targets
    return [TrackingTarget(task_id=probe_task_id, parent_id=parent_id), *targets]


async def _collect_pending_tsapi_entries(
    *,
    targets: list,
    titles_by_id: dict[int, str],
    tsapi: TfsTsapiClient,
    auth: TfsAuth,
    period_start: date,
    period_end: date,
) -> tuple[list[PendingTsapiEntry], int, int, dict[str, Any]]:
    pending: list[PendingTsapiEntry] = []
    tasks_scanned = 0
    skipped = 0
    tsapi_errors: list[str] = []
    deltas_in_period = 0
    deltas_total = 0
    deltas_user_mismatch = 0

    for target in targets:
        title = titles_by_id.get(target.task_id, "")
        if title and not _should_scan_tracking_task(title, target.task_id):
            skipped += 1
            continue
        tasks_scanned += 1
        try:
            deltas = await tsapi.get_work_item_deltas(target.task_id)
        except Exception as exc:
            skipped += 1
            if len(tsapi_errors) < 5:
                tsapi_errors.append(f"#{target.task_id}: {exc}")
            continue

        role, activity = _title_for_target(titles_by_id, target.task_id)
        for row in deltas:
            deltas_total += 1
            if row.period_date < period_start or row.period_date > period_end:
                continue
            deltas_in_period += 1
            # Задачи уже отфильтрованы WIQL «мои»; если AD_UserID не совпал по формату — всё равно берём период.
            if not delta_user_matches_auth(row.user_id, auth):
                deltas_user_mismatch += 1
            pending.append(
                PendingTsapiEntry(
                    parent_id=target.parent_id,
                    task_id=target.task_id,
                    role=role,
                    activity=activity,
                    row=row,
                )
            )

    stats = {
        "deltas_in_period": deltas_in_period,
        "deltas_total": deltas_total,
        "deltas_user_mismatch": deltas_user_mismatch,
        "tsapi_errors": tsapi_errors,
    }
    return pending, tasks_scanned, skipped, stats


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
    tsapi_probe_rows = 0

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
        targets = await _ensure_probe_tracking_target(
            tfs, targets, probe_task_id=settings.tfs_tsapi_probe_task_id
        )
        targets = targets[: settings.tfs_sync_max_tasks]
        tsapi_probe_rows = 0
        if settings.tfs_tsapi_probe_task_id:
            tsapi_probe_rows = await probe_tsapi_list_delta_rows(
                auth, settings.tfs_tsapi_probe_task_id
            )
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
        pending, tasks_scanned, skipped, scan_stats = await _collect_pending_tsapi_entries(
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
        deltas_total = int(scan_stats.get("deltas_total") or 0)
        probe_hint = ""
        if settings.tfs_tsapi_probe_task_id and tsapi_probe_rows == 0 and auth.pat:
            probe_hint = (
                f" Проверка задачи #{settings.tfs_tsapi_probe_task_id}: "
                "PAT не читает tsapi «Время» — войдите с паролем (Учётная запись) "
                "или уточните scope PAT."
            )
        elif deltas_total == 0 and auth.pat:
            probe_hint = (
                " PAT не возвращает ListDelta: попробуйте вход «Учётная запись» (пароль AD) "
                "или T2RU\\user в поле логина."
            )
        return {
            "imported": 0,
            "skipped": skipped,
            "tasks_scanned": tasks_scanned,
            "purged": 0,
            "deltas_in_period": 0,
            "deltas_total": deltas_total,
            "tsapi_probe_rows": tsapi_probe_rows,
            "period_start": period_start,
            "period_end": end,
            "cached": False,
            "source": "tsapi",
            "message": (
                "TFS «Время»: за неделю нет ListDelta (PeriodDate) на отсканированных задачах."
                " Старые строки в табеле не удалены."
                + probe_hint
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

    tsapi_errors = scan_stats.get("tsapi_errors") or []
    deltas_in_period = int(scan_stats.get("deltas_in_period") or 0)
    deltas_total = int(scan_stats.get("deltas_total") or 0)
    message: str | None = None
    if imported > 0:
        message = None
    elif tsapi_errors and tasks_scanned > 0 and deltas_in_period == 0:
        message = (
            "TFS tsapi: не удалось прочитать «Время» (проверьте PAT и логин T2RU\\user). "
            f"Пример: {tsapi_errors[0]}"
        )
    elif deltas_in_period == 0 and deltas_total > 0:
        message = (
            "TFS «Время»: есть списания, но не в выбранной неделе (смотрите PeriodDate в TFS)."
        )
    elif deltas_in_period == 0 and auth.pat and deltas_total == 0:
        message = (
            "TFS tsapi: PAT не отдаёт вкладку «Время» (пустой ListDelta). "
            "Войдите с паролем Windows (режим «Учётная запись») или проверьте логин T2RU\\user."
        )
    elif deltas_in_period == 0:
        message = (
            "TFS «Время»: за эту неделю нет строк ListDelta (PeriodDate) на ваших задачах."
            if not force
            else "TFS «Время»: ListDelta за неделю пустой. Старые строки в табеле не удалены."
        )
    else:
        message = "TFS «Время»: строки найдены, но все уже есть в табеле (sync key)."

    return {
        "imported": imported,
        "skipped": skipped,
        "tasks_scanned": tasks_scanned,
        "purged": purged,
        "removed_dupes": removed_dupes,
        "deltas_in_period": deltas_in_period,
        "deltas_total": deltas_total,
        "tsapi_probe_rows": tsapi_probe_rows if settings.tfs_tsapi_probe_task_id else None,
        "period_start": period_start,
        "period_end": end,
        "cached": False,
        "source": "tsapi",
        "message": message,
        "tsapi_errors": tsapi_errors[:5] if tsapi_errors else None,
    }
