"""Tele2 TFS: вкладка «Время» (Logrocon) — ListDelta через /tsapi."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.http_auth import build_http_auth, pat_http_auth_candidates
from app.tfs_auth import TfsAuth


def resolve_tsapi_base_url(tfs_base_url: str | None = None) -> str:
    explicit = (settings.tfs_tsapi_base_url or "").strip()
    if explicit:
        return explicit.rstrip("/")
    base = (tfs_base_url or settings.tfs_base_url).rstrip("/")
    parsed = urlparse(base)
    return f"{parsed.scheme}://{parsed.netloc}/tsapi"


@dataclass(frozen=True)
class TsapiDeltaRow:
    """Строка списания из GetListDeltaByWorkitemIDMod."""

    delta_id: int
    work_item_id: int
    period_date: date
    duration_minutes: int
    user_id: str
    comment: str | None = None

    @property
    def hours(self) -> float:
        return round(self.duration_minutes / 60.0, 2)


def _parse_period_date(raw: Any) -> date | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    for sep in (".", "-", "/"):
        parts = text.split(sep)
        if len(parts) != 3:
            continue
        try:
            a, b, c = (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            continue
        if c < 100:
            c += 2000
        if a > 31:
            year, month, day = a, b, c
        else:
            day, month, year = a, b, c
        if 1 <= day <= 31 and 1 <= month <= 12:
            return date(year, month, day)
    return None


def parse_list_delta_payload(
    payload: Any, *, work_item_id: int
) -> list[TsapiDeltaRow]:
    if not isinstance(payload, dict):
        return []
    rows: list[TsapiDeltaRow] = []
    for item in payload.get("ListDelta") or []:
        if not isinstance(item, dict):
            continue
        delta_id = int(item.get("ID") or 0)
        period = _parse_period_date(item.get("PeriodDate"))
        if not delta_id or period is None:
            continue
        try:
            duration = int(item.get("Duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        user_id = str(item.get("AD_UserID") or "").strip()
        if duration <= 0 or not user_id:
            continue
        comment = item.get("TextComment")
        rows.append(
            TsapiDeltaRow(
                delta_id=delta_id,
                work_item_id=work_item_id,
                period_date=period,
                duration_minutes=duration,
                user_id=user_id,
                comment=str(comment).strip() if comment else None,
            )
        )
    return rows


def _ad_account_tails(value: str) -> set[str]:
    """Хвост логина AD (после домена) или локальная часть email — для сопоставления с ListDelta."""
    raw = value.casefold().strip()
    if not raw:
        return set()
    tails = {raw}
    if "\\" in raw:
        tails.add(raw.split("\\")[-1])
    if "@" in raw:
        tails.add(raw.split("@")[0])
    return {item for item in tails if len(item) >= 3}


def delta_user_matches_auth(delta_user: str, auth: TfsAuth) -> bool:
    """
    Строка ListDelta (AD_UserID) только для текущей сессии.
    Другой пользователь с тем же PAT не увидит чужие часы: у него другие tokens.
    """
    needle = delta_user.casefold().strip()
    if not needle:
        return False
    tokens = auth.identity_match_tokens()
    if not tokens:
        return False
    if needle in tokens:
        return True
    needle_tails = _ad_account_tails(needle)
    if not needle_tails:
        return False
    auth_tails: set[str] = set()
    for token in tokens:
        auth_tails |= _ad_account_tails(token)
    return bool(needle_tails & auth_tails)


class TfsTsapiClient:
    def __init__(self, auth: TfsAuth) -> None:
        headers = {
            "Accept": "application/json, text/javascript, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        if auth.cookie:
            headers["Cookie"] = auth.cookie
        self.auth = auth
        self.base_url = resolve_tsapi_base_url(auth.base_url)
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=build_http_auth(auth, use_ntlm=False),
            headers=headers,
            timeout=settings.tfs_timeout_seconds,
            verify=settings.tfs_verify_tls,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _fetch_list_delta_page(
        self,
        *,
        path: str,
        params: dict[str, Any],
        user: str,
        pat: str,
    ) -> list[TsapiDeltaRow]:
        work_item_id = int(params["WI_ID"])
        if self.auth.pat:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                auth=(user, pat),
                headers=self.client.headers,
                timeout=settings.tfs_timeout_seconds,
                verify=settings.tfs_verify_tls,
                follow_redirects=True,
            ) as probe:
                response = await probe.get(path, params=params)
        else:
            response = await self.client.get(path, params=params)
        response.raise_for_status()
        payload = response.json()
        return parse_list_delta_payload(payload, work_item_id=work_item_id)

    async def get_work_item_deltas(
        self, work_item_id: int, *, page: int = 1, take: int = 200
    ) -> list[TsapiDeltaRow]:
        path = "/WorkItemFormTab/GetListDeltaByWorkitemIDMod"
        params: dict[str, Any] = {"WI_ID": work_item_id, "page": page, "take": take}
        errors: list[str] = []
        auth_pairs = pat_http_auth_candidates(self.auth) or [("", "")]
        best: list[TsapiDeltaRow] = []
        for user, pat in auth_pairs:
            label = user or "(пустой логин)"
            try:
                rows = await self._fetch_list_delta_page(
                    path=path, params=params, user=user, pat=pat
                )
                if len(rows) > len(best):
                    best = rows
            except Exception as exc:
                errors.append(f"{label}: {exc}")
        if best or not errors:
            return best
        raise httpx.HTTPError("tsapi ListDelta: " + "; ".join(errors[:4]))

    async def save_delta(
        self,
        *,
        work_item_id: int,
        period_date: date,
        duration_minutes: int,
        user_id: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Запись списания (как вкладка «Время»). Endpoint уточняется по HAR сохранения."""
        period_iso = datetime.combine(period_date, datetime.min.time()).isoformat()
        bodies = [
            {
                "WI_ID": work_item_id,
                "PeriodDate": period_iso,
                "Duration": duration_minutes,
                "AD_UserID": user_id,
                "TextComment": comment,
            },
            {
                "wi_ID": work_item_id,
                "periodDate": period_iso,
                "duration": duration_minutes,
                "ad_UserID": user_id,
                "textComment": comment,
            },
        ]
        paths = (
            "WorkItemFormTab/SaveListDeltaByWorkitemIDMod",
            "WorkItemFormTab/InsertListDeltaByWorkitemIDMod",
            "WorkItemFormTab/SaveDeltaByWorkitemIDMod",
            "WorkItemFormTab/AddListDeltaByWorkitemIDMod",
        )
        errors: list[str] = []
        for path in paths:
            for body in bodies:
                for as_query in (False, True):
                    try:
                        if as_query:
                            response = await self.client.post(path, params=body)
                        else:
                            response = await self.client.post(
                                path,
                                json=body,
                                headers={
                                    **self.client.headers,
                                    "Content-Type": "application/json",
                                },
                            )
                    except httpx.HTTPError as exc:
                        errors.append(f"{path}: {exc}")
                        continue
                    if response.status_code in {200, 201, 204}:
                        try:
                            return response.json() if response.content else {"ok": True}
                        except ValueError:
                            return {"ok": True}
                    errors.append(f"{path}: HTTP {response.status_code}")
        raise httpx.HTTPError(
            "tsapi save failed (" + "; ".join(errors[:4]) + "). "
            "Запишите HAR при сохранении на вкладке «Время» в TFS."
        )
