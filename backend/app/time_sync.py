from __future__ import annotations

import asyncio
import html
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import AccountSyncState, TimeEntry
from app.auth_sessions import update_session
from app.tfs_auth import TfsAuth, attach_tfs_identity, tfs_login_unique_name
from app.tfs_identity import identity_from_auth_login
from app.tfs_client import TfsClient
from app.time_service import (
    ROLE_LABELS,
    backfill_entry_owners,
    entry_ownership_clause,
    owner_unique_name_for,
    parse_tracking_title,
    period_end,
    touch_recent,
)


def _period_entry_count(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    period_end: date,
) -> int:
    return int(
        db.scalar(
            select(func.count(TimeEntry.id)).where(
                TimeEntry.account_key == auth.account_key,
                TimeEntry.entry_date >= period_start,
                TimeEntry.entry_date <= period_end,
                TimeEntry.hours > 0,
                entry_ownership_clause(auth),
            )
        )
        or 0
    )

HISTORY_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}):\s*(?:([+-]))?([\d.,]+)\s*ч(?:\s*[—-]\s*(.*))?$",
    re.MULTILINE,
)

HTML_TAG = re.compile(r"<[^>]+>")


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


def _history_increment_text(fields: dict[str, Any]) -> str:
    """Только текст, добавленный в этой ревизии (не весь тред комментариев)."""
    history_raw = fields.get("System.History")
    if isinstance(history_raw, str):
        return _plain_history_text(history_raw.strip())
    if not isinstance(history_raw, dict):
        return ""
    new_val = str(history_raw.get("newValue") or "").strip()
    if not new_val:
        return ""
    old_val = str(history_raw.get("oldValue") or "").strip()
    if not old_val:
        return _plain_history_text(new_val)
    if new_val.startswith(old_val):
        return _plain_history_text(new_val[len(old_val) :].lstrip("\n\r"))
    old_lines = {line.strip() for line in old_val.splitlines() if line.strip()}
    if not old_lines:
        return ""
    added = [line for line in new_val.splitlines() if line.strip() and line.strip() not in old_lines]
    if not added:
        return ""
    if all(not HISTORY_LINE.match(line) for line in added):
        return ""
    return _plain_history_text("\n".join(added))


def _history_text_for_parse(fields: dict[str, Any]) -> str:
    """Инкремент ревизии; если diff пустой — весь newValue (как в Oscar/TFS треде)."""
    incremental = _history_increment_text(fields)
    if incremental:
        return incremental
    history_raw = fields.get("System.History")
    if isinstance(history_raw, str):
        return _plain_history_text(history_raw.strip())
    if not isinstance(history_raw, dict):
        return ""
    full = _plain_history_text(str(history_raw.get("newValue") or ""))
    if not full:
        return ""
    lines = [line for line in full.splitlines() if HISTORY_LINE.search(line.strip())]
    return "\n".join(lines)


def _plain_history_text(value: str) -> str:
    """TFS часто отдаёт System.History как HTML; для парсинга нужна обычная строка."""
    text = html.unescape(value)
    text = re.sub(r"</(?:div|p|br|li|tr)>", "\n", text, flags=re.IGNORECASE)
    text = HTML_TAG.sub("", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def parse_update_time_slices(
    update: dict[str, Any],
    *,
    tracking_work_item_id: int,
) -> list[ParsedTimeSlice]:
    fields = as_update_fields(update)
    rev = int(update.get("rev") or update.get("id") or 0)
    revised_date = _parse_revised_date(update.get("revisedDate"))

    history_text = _history_text_for_parse(fields)

    slices: list[ParsedTimeSlice] = []
    for index, match in enumerate(HISTORY_LINE.finditer(history_text)):
        entry_date = date.fromisoformat(match.group(1))
        sign_raw = match.group(2)
        sign = -1.0 if sign_raw == "-" else 1.0
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

    if not slices:
        delta = _completed_work_delta(fields)
        if delta is not None:
            slices.append(
                ParsedTimeSlice(
                    entry_date=revised_date,
                    hours=delta,
                    comment="Completed Work",
                    sync_key=f"tfs:{tracking_work_item_id}:rev{rev}:cw",
                )
            )

    return slices


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


def _identity_tokens_from_blob(blob: Any) -> set[str]:
    tokens: set[str] = set()
    if not isinstance(blob, dict):
        return tokens
    for key in (
        "id",
        "displayName",
        "uniqueName",
        "name",
        "descriptor",
        "mailAddress",
        "emailAddress",
    ):
        raw = blob.get(key)
        if raw:
            tokens.add(str(raw).casefold())
    id_ref = blob.get("identityRef")
    if isinstance(id_ref, dict):
        tokens |= _identity_tokens_from_blob(id_ref)
    return tokens


def _strong_identity_tokens(blob: Any) -> set[str]:
    if not isinstance(blob, dict):
        return set()
    tokens: set[str] = set()
    for key in ("uniqueName", "name", "descriptor", "id", "mailAddress", "emailAddress"):
        raw = blob.get(key)
        if raw:
            tokens.add(str(raw).casefold().strip())
    id_ref = blob.get("identityRef")
    if isinstance(id_ref, dict):
        tokens |= _strong_identity_tokens(id_ref)
    return tokens


def _tokens_match(author_tokens: set[str], current_user_tokens: set[str]) -> bool:
    if not author_tokens or not current_user_tokens:
        return False
    return bool(author_tokens & current_user_tokens)


def update_revised_by_current_user(
    update: dict[str, Any],
    *,
    current_user_tokens: set[str],
    current_user_strong_tokens: set[str],
) -> bool:
    """Только ревизии владельца PAT."""
    author = update.get("revisedBy")
    author_strong = _strong_identity_tokens(author)
    if author_strong and current_user_strong_tokens:
        return bool(author_strong & current_user_strong_tokens)
    if current_user_tokens:
        return _tokens_match(_identity_tokens_from_blob(author), current_user_tokens)
    return False


def assignee_tokens_from_fields(fields: dict[str, Any]) -> set[str]:
    raw = fields.get("System.AssignedTo")
    if isinstance(raw, dict):
        return _identity_tokens_from_blob(raw)
    if isinstance(raw, str) and raw.strip():
        name = raw.split("<")[0].strip() if "<" in raw else raw.strip()
        return {name.casefold()} if name else set()
    return set()


def work_item_assigned_to_current_user(
    fields: dict[str, Any],
    *,
    current_user_tokens: set[str],
    current_user_strong_tokens: set[str],
) -> bool:
    """Дочерняя «Роль — активность» должна быть назначена на текущего пользователя."""
    raw_assignee = fields.get("System.AssignedTo")
    assignee_strong = (
        _strong_identity_tokens(raw_assignee) if isinstance(raw_assignee, dict) else set()
    )
    if not assignee_strong:
        return True
    if not current_user_strong_tokens:
        return False
    return bool(assignee_strong & current_user_strong_tokens)


def work_item_created_by_current_user(
    fields: dict[str, Any],
    *,
    current_user_tokens: set[str],
    current_user_strong_tokens: set[str],
) -> bool:
    """Задачу завели вы (как дочернюю «Аналитик — …» под требованием)."""
    raw_creator = fields.get("System.CreatedBy")
    creator_strong = (
        _strong_identity_tokens(raw_creator) if isinstance(raw_creator, dict) else set()
    )
    if creator_strong and current_user_strong_tokens:
        return bool(creator_strong & current_user_strong_tokens)
    if current_user_tokens:
        return _tokens_match(_identity_tokens_from_blob(raw_creator), current_user_tokens)
    return False


def work_item_owned_by_current_user(
    fields: dict[str, Any],
    *,
    current_user_tokens: set[str],
    current_user_strong_tokens: set[str],
) -> bool:
    """
    Как /track в Oscar: ваша дочерняя задача списания —
    назначена на вас, создана вами или без чужого assignee.
    """
    raw_assignee = fields.get("System.AssignedTo")
    if isinstance(raw_assignee, dict) and _strong_identity_tokens(raw_assignee):
        if not work_item_assigned_to_current_user(
            fields,
            current_user_tokens=current_user_tokens,
            current_user_strong_tokens=current_user_strong_tokens,
        ):
            return False
        return True
    if work_item_created_by_current_user(
        fields,
        current_user_tokens=current_user_tokens,
        current_user_strong_tokens=current_user_strong_tokens,
    ):
        return True
    return work_item_assigned_to_current_user(
        fields,
        current_user_tokens=current_user_tokens,
        current_user_strong_tokens=current_user_strong_tokens,
    )


async def resolve_current_user_tokens(
    client: TfsClient, auth: TfsAuth
) -> tuple[set[str], set[str]]:
    """(полные токены, надёжные uniqueName/descriptor/id) из сессии или connectionData."""
    strong = set(auth.identity_strong_tokens())
    tokens = set(auth.identity_match_tokens())
    from_form = identity_from_auth_login(auth)
    if from_form:
        strong |= from_form.strong_tokens()
        tokens |= from_form.match_tokens()
    if strong or tokens:
        return tokens, strong
    identity = await client.get_authenticated_user_identity()
    if identity:
        return identity.match_tokens(), identity.strong_tokens()
    return set(), set()


def filter_updates_for_sync(
    updates: list[dict[str, Any]],
    *,
    period_start: date,
    current_user_tokens: set[str],
    current_user_strong_tokens: set[str],
    trust_me_task: bool = False,
) -> list[dict[str, Any]]:
    """Релевантные ревизии периода: только автор PAT и изменения часов/истории."""
    cutoff = period_start - timedelta(days=settings.tfs_sync_update_lookback_days)
    filtered: list[dict[str, Any]] = []
    for update in updates:
        if not trust_me_task and not update_revised_by_current_user(
            update,
            current_user_tokens=current_user_tokens,
            current_user_strong_tokens=current_user_strong_tokens,
        ):
            continue
        revised = _parse_revised_date(update.get("revisedDate"))
        if revised < cutoff:
            continue
        fields = as_update_fields(update)
        if "System.History" in fields or "Microsoft.VSTS.Scheduling.CompletedWork" in fields:
            filtered.append(update)
    return filtered


def aggregate_slices_for_task(
    updates: list[dict[str, Any]],
    *,
    tracking_work_item_id: int,
    period_start: date,
    current_user_tokens: set[str],
    current_user_strong_tokens: set[str],
    trust_me_task: bool = False,
) -> list[ParsedTimeSlice]:
    def _collect(*, trust_author: bool) -> list[ParsedTimeSlice]:
        slices: list[ParsedTimeSlice] = []
        for update in filter_updates_for_sync(
            updates,
            period_start=period_start,
            current_user_tokens=current_user_tokens,
            current_user_strong_tokens=current_user_strong_tokens,
            trust_me_task=trust_author,
        ):
            slices.extend(
                parse_update_time_slices(update, tracking_work_item_id=tracking_work_item_id)
            )
        return slices

    merged = _collect(trust_author=False)
    if not merged and trust_me_task:
        merged = _collect(trust_author=True)
    return merged


def merge_slices_by_day(
    slices: list[ParsedTimeSlice],
    *,
    period_start: date,
    period_end: date,
) -> dict[date, float]:
    """Сумма часов по дням недели (как delta[7] в сетке /track)."""
    daily: dict[date, float] = defaultdict(float)
    for slice_ in slices:
        if not entry_in_period(slice_.entry_date, period_start=period_start, period_end=period_end):
            continue
        daily[slice_.entry_date] = round(daily[slice_.entry_date] + slice_.hours, 2)
    return dict(daily)


def grid_sync_key(task_id: int, entry_date: date) -> str:
    return f"grid:{task_id}:{entry_date.isoformat()}"


def purge_all_imported_tfs_entries(db: Session, auth: TfsAuth) -> int:
    """Удаляет все импортированные из TFS строки аккаунта (чужие/дубли при смене логики)."""
    rows = db.scalars(
        select(TimeEntry.id).where(
            TimeEntry.account_key == auth.account_key,
            TimeEntry.tfs_sync_key.isnot(None),
        )
    ).all()
    if not rows:
        return 0
    db.execute(delete(TimeEntry).where(TimeEntry.id.in_(rows)))
    return len(rows)


def purge_entries_not_owned_by_user(db: Session, auth: TfsAuth) -> int:
    """Удаляет только чужой импорт из TFS (ручные списания не трогаем)."""
    owner = owner_unique_name_for(auth)
    if not owner:
        return 0
    rows = db.scalars(
        select(TimeEntry.id).where(
            TimeEntry.account_key == auth.account_key,
            TimeEntry.tfs_sync_key.isnot(None),
            or_(
                TimeEntry.owner_unique_name.is_(None),
                func.lower(TimeEntry.owner_unique_name) != owner,
            ),
        )
    ).all()
    if not rows:
        return 0
    db.execute(delete(TimeEntry).where(TimeEntry.id.in_(rows)))
    return len(rows)


def purge_imported_tfs_entries(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    period_end: date,
) -> int:
    """Удаляет импортированные из TFS строки в периоде перед пересборкой."""
    rows = db.scalars(
        select(TimeEntry.id).where(
            TimeEntry.account_key == auth.account_key,
            TimeEntry.tfs_sync_key.isnot(None),
            TimeEntry.entry_date >= period_start,
            TimeEntry.entry_date <= period_end,
        )
    ).all()
    if not rows:
        return 0
    db.execute(
        delete(TimeEntry).where(TimeEntry.id.in_(rows))
    )
    return len(rows)


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
    if age >= settings.tfs_sync_ttl_seconds:
        return True
    end = period_end(period_start, view)
    return _period_entry_count(
        db, auth, period_start=period_start, period_end=end
    ) == 0


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
    current_user_tokens: set[str],
    current_user_strong_tokens: set[str],
) -> list[TrackingTarget]:
    """
    Как stream в Oscar: ваши дочерние «Роль — активность» под требованием/ЗНИ,
    где вы назначены, создали или меняли задачу; часы — из вашей History/Completed Work.
    """
    seen: set[int] = set()
    targets: list[TrackingTarget] = []

    def add(task_id: int, parent_id: int) -> None:
        if task_id not in seen:
            seen.add(task_id)
            targets.append(TrackingTarget(task_id=task_id, parent_id=parent_id))

    lookback = period_start - timedelta(days=settings.tfs_sync_update_lookback_days)
    wiql_ids: list[int] = []
    if include_wiql:
        wiql_ids.extend(await client.find_tracking_tasks_for_me(changed_since=lookback))
        login_name = tfs_login_unique_name(auth)
        if login_name:
            wiql_ids.extend(
                await client.find_task_ids_changed_by_user(
                    unique_name=login_name,
                    changed_since=lookback,
                )
            )
            wiql_ids.extend(
                await client.find_tracking_tasks_assigned_to_user(
                    unique_name=login_name,
                    changed_since=lookback,
                )
            )
            wiql_ids.extend(
                await client.find_task_ids_created_by_user(
                    unique_name=login_name,
                    changed_since=lookback,
                )
            )
    for task_id in wiql_ids:
        parent_id = await client.get_parent_work_item_id(task_id)
        if parent_id is not None:
            add(task_id, parent_id)

    end = period_end(period_start, view)
    local_rows = db.execute(
        select(TimeEntry.tracking_work_item_id, TimeEntry.parent_work_item_id).where(
            TimeEntry.account_key == auth.account_key,
            TimeEntry.tracking_work_item_id.isnot(None),
            TimeEntry.entry_date >= period_start,
            TimeEntry.entry_date <= end,
            TimeEntry.hours > 0,
            entry_ownership_clause(auth),
        )
    ).all()
    for tracking_id, parent_id in local_rows:
        if tracking_id is None or parent_id is None:
            continue
        add(int(tracking_id), int(parent_id))

    return targets[: settings.tfs_sync_max_tasks]


async def sync_time_from_tfs(
    db: Session,
    auth: TfsAuth,
    *,
    period_start: date,
    view: str,
    force: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    """
    Сетка недели из TFS (как /track в Oscar по смыслу): дочерние «Роль — активность»,
    часы по дням пн–вс из History/Completed Work. Запросов в Oscar нет.
    """
    from app.auth_service import ensure_auth_identity

    end = period_end(period_start, view)

    identity_client = TfsClient(auth)
    try:
        auth = await ensure_auth_identity(identity_client, auth)
        if session_id and auth.identity_match_tokens():
            update_session(session_id, auth)
    finally:
        await identity_client.close()

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

    try:
        from app.auth_service import ensure_auth_identity as _ensure_identity

        auth = await _ensure_identity(client, auth)
        if session_id and auth.identity_match_tokens():
            update_session(session_id, auth)
        user_tokens, user_strong_tokens = await resolve_current_user_tokens(client, auth)
        if not user_tokens:
            raise ValueError(
                "Не удалось определить пользователя TFS по PAT. Выйдите и войдите снова."
            )
        owner_key = owner_unique_name_for(auth)
        backfill_entry_owners(db, auth)
        # Только выбранная неделя (как delta[7]), без полной истории аккаунта.
        purged = purge_imported_tfs_entries(
            db, auth, period_start=period_start, period_end=end
        )
        if force:
            purged += purge_entries_not_owned_by_user(db, auth)
        existing_keys = load_existing_sync_keys(
            db, auth, period_start=period_start, period_end=end
        )
        targets = await collect_tracking_targets(
            client,
            db,
            auth,
            period_start=period_start,
            view=view,
            include_wiql=True,
            current_user_tokens=user_tokens,
            current_user_strong_tokens=user_strong_tokens,
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
                "purged": purged,
                "period_start": period_start,
                "period_end": end,
                "cached": False,
                "source": "tfs-grid",
                "message": "TFS: не найдено ваших задач списания за неделю.",
            }

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
                "System.CreatedBy",
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
                title = str(fields.get("System.Title") or f"#{task_id}")
                if not is_tracking_child_item(
                    {"id": task_id, "title": title, "kind": "task"}
                ):
                    return 0, 1
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

                slices = aggregate_slices_for_task(
                    updates,
                    tracking_work_item_id=task_id,
                    period_start=period_start,
                    current_user_tokens=user_tokens,
                    current_user_strong_tokens=user_strong_tokens,
                    trust_me_task=True,
                )
                daily_hours = merge_slices_by_day(
                    slices, period_start=period_start, period_end=end
                )
                if not daily_hours:
                    return 0, 1
                local_imported = 0
                local_skipped = 0
                for entry_date, hours in daily_hours.items():
                    if abs(hours) < 0.01:
                        continue
                    sync_key = grid_sync_key(task_id, entry_date)
                    if sync_key in existing_keys:
                        local_skipped += 1
                        continue
                    db.add(
                        TimeEntry(
                            account_key=auth.account_key,
                            parent_work_item_id=parent_id,
                            tracking_work_item_id=task_id,
                            role=role,
                            activity=activity,
                            entry_date=entry_date,
                            hours=hours,
                            comment=None,
                            cost_project=cost_project,
                            tfs_sync_key=sync_key,
                            owner_unique_name=owner_key,
                        )
                    )
                    existing_keys.add(sync_key)
                    local_imported += 1

                if daily_hours:
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
        if session_id and auth.identity_match_tokens():
            update_session(session_id, auth)
    finally:
        await client.close()

    return {
        "imported": imported,
        "skipped": skipped,
        "tasks_scanned": tasks_scanned,
        "purged": purged,
        "period_start": period_start,
        "period_end": end,
        "cached": False,
        "source": "tfs-grid",
        "message": (
            None
            if imported > 0
            else (
                "TFS: задачи найдены, но часов в History/Completed Work за эту неделю нет."
                if tasks_scanned > 0
                else "TFS: не найдено ваших задач списания за неделю (проверьте PAT и логин)."
            )
        ),
    }
