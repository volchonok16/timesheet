import html
import json
from urllib.parse import quote, urlparse

import httpx
from fastapi import HTTPException

from app.auth_sessions import create_session
from app.db import SessionLocal
from app.time_service import backfill_entry_owners
from app.config import settings
from app.http_auth import auth_attempts
from app.schemas import AuthLoginOut
from app.tfs_auth import TfsAuth, attach_tfs_identity
from app.tfs_identity import identity_from_auth_login, identity_has_tokens, merge_tfs_identities
from app.tfs_client import TfsClient, wiql_quote


def default_app_url() -> str:
    return settings.app_public_url.rstrip("/")


def default_api_url() -> str:
    return settings.api_public_url.rstrip("/")


async def probe_tfs(client: TfsClient, auth: TfsAuth) -> tuple[bool, int | None]:
    last_status: int | None = None
    response = await client.client.get(
        "/_apis/connectionData",
        params={"connectOptions": "includeServices", "lastChangeId": "-1", "api-version": "5.0"},
    )
    last_status = response.status_code
    if response.status_code == 200:
        return True, last_status
    if response.status_code in {401, 403}:
        return False, last_status

    response = await client.client.get("/_apis/projects", params={"$top": "1", "api-version": "5.1"})
    last_status = response.status_code
    if response.status_code == 200:
        return True, last_status
    if response.status_code in {401, 403}:
        return False, last_status

    try:
        await client.run_wiql(
            f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = {wiql_quote(auth.project)}"
        )
        return True, 200
    except httpx.HTTPStatusError as exc:
        return exc.response.status_code not in {401, 403}, exc.response.status_code

    return False, last_status


async def resolve_working_auth(auth: TfsAuth) -> tuple[TfsAuth, str]:
    attempts = auth_attempts(auth)
    if not attempts:
        raise HTTPException(status_code=400, detail="Укажите логин и пароль, PAT или Cookie.")

    errors: list[str] = []
    for attempt in attempts:
        client = TfsClient(attempt.auth, use_ntlm=attempt.use_ntlm)
        try:
            ok, status = await probe_tfs(client, attempt.auth)
            if ok:
                return attempt.auth, attempt.label
            errors.append(f"{attempt.label}: HTTP {status or '?'}")
        except httpx.HTTPError as exc:
            errors.append(f"{attempt.label}: {exc}")
        finally:
            await client.close()

    login = (auth.username or "").strip()
    if "@" in login:
        hint = (
            "Для входа вида name@t2.ru TFS за NetScaler обычно не принимает пароль через API. "
            "Создайте PAT: TFS → иконка пользователя → Personal access tokens."
        )
    else:
        hint = "Проверьте логин/пароль как на tfs.t2.ru. Надёжный вариант — PAT."
    raise HTTPException(
        status_code=401,
        detail=f"TFS не принял учётные данные ({'; '.join(errors[:6])}). {hint}",
    )


async def ensure_auth_identity(client: TfsClient, auth: TfsAuth) -> TfsAuth:
    """Сохраняет в сессии логин: форма входа + connectionData/profile/WIQL @Me."""
    from_form = identity_from_auth_login(auth)
    from_api = await client.get_authenticated_user_identity()
    merged = merge_tfs_identities(from_form, from_api)
    if identity_has_tokens(merged):
        return attach_tfs_identity(auth, merged)
    if from_form:
        return attach_tfs_identity(auth, from_form)
    return auth


async def login_with_auth(auth: TfsAuth) -> AuthLoginOut:
    if not auth.has_credentials():
        raise HTTPException(status_code=400, detail="Укажите логин и пароль, PAT или Cookie.")

    resolved, _ = await resolve_working_auth(auth)
    client = TfsClient(resolved)
    try:
        resolved = await ensure_auth_identity(client, resolved)
    finally:
        await client.close()
    if not resolved.identity_match_tokens():
        raise HTTPException(
            status_code=401,
            detail=(
                "TFS принял PAT, но не удалось определить ваш логин. "
                "При входе по токену укажите email или логин (user@t2.ru или TELE2\\user) "
                "в поле «Логин TFS». Проверьте URL коллекции: https://tfs.t2.ru/tfs/Main."
            ),
        )
    session_id = create_session(resolved)
    db = SessionLocal()
    try:
        backfill_entry_owners(db, resolved)
        db.commit()
    finally:
        db.close()
    return AuthLoginOut(
        session_id=session_id,
        base_url=resolved.base_url,
        project=resolved.project,
        project_id=resolved.project_id,
    )


def bridge_result_html(session_id: str, app_url: str, error: str | None = None) -> str:
    safe_app = html.escape(app_url)
    if error:
        message = html.escape(error)
        return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>TFS Timesheet</title></head>
<body style="font-family:system-ui,sans-serif;padding:2rem;">
  <h1>Не удалось подключить сессию</h1><p>{message}</p>
  <p><a href="{safe_app}">Вернуться</a></p>
</body></html>"""

    redirect = f"{safe_app}?session={quote(session_id, safe='')}"
    payload = json.dumps({"type": "tfs-bridge", "sessionId": session_id})
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>TFS Timesheet</title></head>
<body style="font-family:system-ui,sans-serif;padding:2rem;">
  <h1>Сессия TFS подключена</h1>
  <p>Окно закроется автоматически.</p>
  <script>
    (function () {{
      var payload = {payload};
      try {{
        if (window.opener && !window.opener.closed) {{
          window.opener.postMessage(payload, {json.dumps(default_app_url())});
        }}
      }} catch (e) {{}}
      setTimeout(function () {{ window.location.href = {json.dumps(redirect)}; }}, 400);
    }})();
  </script>
</body></html>"""
