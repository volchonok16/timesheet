"""Импорт списаний через API Oscar (как /track в oscar.k8s-mn.ds.t2.ru)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import settings
from app.db import RecentWorkItem
from app.http_auth import build_http_auth
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
    purge_all_imported_tfs_entries,
    purge_entries_not_owned_by_user,
    purge_imported_tfs_entries,
    should_run_sync,
)
from app.db import TimeEntry
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class OscarChildRow:
    task_id: int
    parent_id: int
    title: str
    role: str
    activity: str
    daily_hours: dict[date, float]


def parse_oscar_stream_payload(
    payload: Any,
    *,
    period_start: date,
    period_end: date,
) -> list[OscarChildRow]:
    """Разбор ответа stream_get-time-tracking-results."""
    if not isinstance(payload, list) or len(payload) < 1:
        return []
    parents_raw = payload[0]
    if not isinstance(parents_raw, list):
        return []

    rows: list[OscarChildRow] = []
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
                    OscarChildRow(
                        task_id=task_id,
                        parent_id=parent_id,
                        title=title,
                        role=role,
                        activity=activity,
                        daily_hours=daily,
                    )
                )
    return rows


class OscarClient:
    def __init__(self, auth: TfsAuth, *, base_url: str | None = None) -> None:
        self.auth = auth
        self.base_url = (base_url or settings.oscar_api_base_url or "").rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.auth.cookie:
            headers["Cookie"] = self.auth.cookie
        return headers

    async def fetch_stream(self, period_start: date) -> Any | None:
        if not self.base_url:
            return None
        auth = build_http_auth(self.auth, use_ntlm=False)
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=settings.tfs_timeout_seconds,
            verify=settings.tfs_verify_tls,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                "/stream_get-time-tracking-results",
                params={"from": period_start.isoformat()},
                headers=self._headers(),
                auth=auth,
            )
            if response.status_code != 200:
                return None
            return response.json()


async def sync_time_from_oscar(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
    force: bool = False,
) -> dict[str, Any] | None:
    """Синхронизация через Oscar API. None — Oscar недоступен, нужен fallback TFS."""
    if not settings.oscar_sync_enabled or not settings.oscar_api_base_url:
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
            "source": "oscar",
        }

    owner_key = owner_unique_name_for(auth)
    if not owner_key:
        return None

    oscar = OscarClient(auth)
    payload = await oscar.fetch_stream(period_start)
    if payload is None:
        return None

    rows = parse_oscar_stream_payload(payload, period_start=period_start, period_end=end)
    if not rows:
        return {
            "imported": 0,
            "skipped": 0,
            "tasks_scanned": 0,
            "purged": 0,
            "period_start": period_start,
            "period_end": end,
            "cached": False,
            "source": "oscar",
            "message": "Oscar недоступен или пустой ответ — нужна сессия Oscar или PAT с доступом",
        }

    if force:
        purged = purge_all_imported_tfs_entries(db, auth)
        purged += purge_entries_not_owned_by_user(db, auth)
    else:
        purged = purge_imported_tfs_entries(db, auth, period_start=period_start, period_end=end)

    existing_keys = load_existing_sync_keys(
        db, auth, period_start=period_start, period_end=end
    )
    imported = 0
    skipped = 0
    parents_seen: set[int] = set()

    tfs_client = TfsClient(auth)
    try:
        for row in rows:
            for entry_date, hours in row.daily_hours.items():
                sync_key = f"oscar:{row.task_id}:{entry_date.isoformat()}"
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
                        comment="Импорт из Oscar",
                        cost_project=None,
                        tfs_sync_key=sync_key,
                        owner_unique_name=owner_key,
                    )
                )
                existing_keys.add(sync_key)
                imported += 1

            if row.parent_id not in parents_seen:
                parents_seen.add(row.parent_id)
                try:
                    parent_item = await tfs_client.get_work_item(row.parent_id)
                    normalized = tfs_client.normalize_item(parent_item)
                    touch_recent(db, auth, normalized)
                except Exception:
                    pass
    finally:
        await tfs_client.close()

    mark_synced(db, auth, period_start=period_start, view=view)
    db.commit()

    return {
        "imported": imported,
        "skipped": skipped,
        "tasks_scanned": len(rows),
        "purged": purged,
        "period_start": period_start,
        "period_end": end,
        "cached": False,
        "source": "oscar",
    }
