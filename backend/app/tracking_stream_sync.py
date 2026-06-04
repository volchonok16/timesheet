"""Импорт delta[7] с stream_get-time-tracking-results (тот же JSON, что /track в Oscar)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import TimeEntry
from app.http_auth import auth_attempts, build_http_auth
from app.tfs_auth import TfsAuth
from app.tfs_client import TfsClient
from app.time_service import (
    owner_unique_name_for,
    parse_tracking_title,
    period_end,
    touch_recent,
)
from app.time_sync import (
    load_existing_sync_keys,
    mark_synced,
    should_run_sync,
)

STREAM_SYNC_PREFIX = "stream:"


def _stream_sync_key(task_id: int, entry_date: date) -> str:
    return f"{STREAM_SYNC_PREFIX}{task_id}:{entry_date.isoformat()}"


def purge_stream_entries(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    period_end: date,
) -> int:
    rows = db.scalars(
        select(TimeEntry.id).where(
            TimeEntry.account_key == auth.account_key,
            TimeEntry.tfs_sync_key.like(f"{STREAM_SYNC_PREFIX}%"),
            TimeEntry.entry_date >= period_start,
            TimeEntry.entry_date <= period_end,
        )
    ).all()
    if not rows:
        return 0
    db.execute(delete(TimeEntry).where(TimeEntry.id.in_(rows)))
    return len(rows)


def purge_all_stream_entries(db: Session, auth: TfsAuth) -> int:
    rows = db.scalars(
        select(TimeEntry.id).where(
            TimeEntry.account_key == auth.account_key,
            or_(
                TimeEntry.tfs_sync_key.like(f"{STREAM_SYNC_PREFIX}%"),
                TimeEntry.tfs_sync_key.like("oscar:%"),
            ),
        )
    ).all()
    if not rows:
        return 0
    db.execute(delete(TimeEntry).where(TimeEntry.id.in_(rows)))
    return len(rows)


@dataclass(frozen=True)
class StreamRow:
    task_id: int
    parent_id: int
    role: str
    activity: str
    daily_hours: dict[date, float]
    parent_payload: dict[str, Any] | None


def parse_track_stream_payload(
    payload: Any,
    *,
    period_start: date,
    period_end: date,
) -> list[StreamRow]:
    if not isinstance(payload, list) or not payload:
        return []
    parents_raw = payload[0]
    if not isinstance(parents_raw, list):
        return []

    rows: list[StreamRow] = []
    for parent in parents_raw:
        if not isinstance(parent, dict):
            continue
        parent_id = int(parent.get("id") or 0)
        if not parent_id:
            continue
        children = parent.get("childs") or parent.get("children") or []
        if not isinstance(children, list):
            continue
        for child in children:
            if not isinstance(child, dict):
                continue
            task_id = int(child.get("id") or 0)
            if not task_id:
                continue
            title = str(child.get("title") or "")
            role, activity = parse_tracking_title(title)
            if not role:
                role = "—"
            if not activity:
                activity = title
            delta = child.get("delta")
            if not isinstance(delta, list):
                continue
            daily: dict[date, float] = {}
            for index, raw_hours in enumerate(delta[:7]):
                try:
                    hours = round(float(raw_hours or 0), 2)
                except (TypeError, ValueError):
                    hours = 0.0
                if hours <= 0:
                    continue
                entry_date = period_start + timedelta(days=index)
                if entry_date > period_end:
                    continue
                daily[entry_date] = hours
            if daily:
                rows.append(
                    StreamRow(
                        task_id=task_id,
                        parent_id=parent_id,
                        role=role,
                        activity=activity,
                        daily_hours=daily,
                        parent_payload=parent,
                    )
                )
    return rows


async def try_sync_from_track_stream(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
    force: bool = False,
) -> dict[str, Any] | None:
    if not settings.tracking_stream_enabled or not settings.tracking_stream_base_url:
        return None

    end = period_end(period_start, view)
    if not should_run_sync(db, auth, period_start=period_start, view=view, force=force):
        return {
            "imported": 0,
            "skipped": 0,
            "tasks_scanned": 0,
            "purged": 0,
            "period_start": period_start,
            "period_end": end,
            "cached": True,
            "source": "stream",
            "stream_ok": True,
        }

    owner_key = owner_unique_name_for(auth)
    if not owner_key:
        return None

    base_url = settings.tracking_stream_base_url.rstrip("/")
    errors: list[str] = []
    payload: Any | None = None
    for attempt in auth_attempts(auth):
        http_auth = build_http_auth(attempt.auth, use_ntlm=attempt.use_ntlm)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{base_url}/track",
        }
        if attempt.auth.cookie:
            headers["Cookie"] = attempt.auth.cookie
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=settings.tfs_timeout_seconds,
            verify=settings.tfs_verify_tls,
            follow_redirects=True,
        ) as client:
            try:
                response = await client.get(
                    "/stream_get-time-tracking-results",
                    params={"from": period_start.isoformat()},
                    headers=headers,
                    auth=http_auth,
                )
            except httpx.HTTPError as exc:
                errors.append(f"{attempt.label}: {exc}")
                continue
            if response.status_code != 200:
                errors.append(f"{attempt.label}: HTTP {response.status_code}")
                continue
            try:
                payload = response.json()
                break
            except ValueError:
                errors.append(f"{attempt.label}: not JSON")
                continue

    if payload is None:
        return {
            "imported": 0,
            "skipped": 0,
            "tasks_scanned": 0,
            "purged": 0,
            "period_start": period_start,
            "period_end": end,
            "cached": False,
            "source": "stream",
            "stream_ok": False,
            "message": "Stream недоступен: " + "; ".join(errors[:3]),
        }

    stream_rows = parse_track_stream_payload(
        payload, period_start=period_start, period_end=end
    )
    if not stream_rows:
        return {
            "imported": 0,
            "skipped": 0,
            "tasks_scanned": 0,
            "purged": 0,
            "period_start": period_start,
            "period_end": end,
            "cached": False,
            "source": "stream",
            "stream_ok": False,
            "message": "Stream пустой — разбор TFS",
        }

    if force:
        purged = purge_all_stream_entries(db, auth)
    else:
        purged = purge_stream_entries(
            db, auth, period_start=period_start, period_end=end
        )

    existing_keys = load_existing_sync_keys(
        db, auth, period_start=period_start, period_end=end
    )
    imported = 0
    skipped = 0
    parents_seen: set[int] = set()

    tfs_client = TfsClient(auth)
    try:
        for row in stream_rows:
            for entry_date, hours in row.daily_hours.items():
                sync_key = _stream_sync_key(row.task_id, entry_date)
                if sync_key in existing_keys:
                    skipped += 1
                    continue
                db.add(
                    TimeEntry(
                        account_key=auth.account_key,
                        parent_work_item_id=row.parent_id,
                        tracking_work_item_id=row.task_id,
                        role=row.role,
                        activity=row.activity,
                        entry_date=entry_date,
                        hours=hours,
                        comment="Stream /track",
                        cost_project=None,
                        tfs_sync_key=sync_key,
                        owner_unique_name=owner_key,
                    )
                )
                existing_keys.add(sync_key)
                imported += 1
            if row.parent_id not in parents_seen and row.parent_payload:
                parents_seen.add(row.parent_id)
                parent = row.parent_payload
                touch_recent(
                    db,
                    auth,
                    {
                        "id": row.parent_id,
                        "title": str(parent.get("title") or ""),
                        "workItemType": str(parent.get("type") or ""),
                        "state": str(parent.get("status") or ""),
                        "areaPath": str(parent.get("area") or ""),
                        "kind": "requirement",
                        "tfsUrl": str(parent.get("url") or ""),
                        "completedWork": 0,
                    },
                )
    finally:
        await tfs_client.close()

    mark_synced(db, auth, period_start=period_start, view=view)
    db.commit()

    return {
        "imported": imported,
        "skipped": skipped,
        "tasks_scanned": len(stream_rows),
        "purged": purged,
        "period_start": period_start,
        "period_end": end,
        "cached": False,
        "source": "stream",
        "stream_ok": imported > 0,
        "message": None if imported > 0 else "Stream без часов в периоде",
    }
