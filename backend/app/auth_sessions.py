import secrets
from threading import Lock

from app.tfs_auth import TfsAuth

_lock = Lock()
_sessions: dict[str, TfsAuth] = {}


def create_session(auth: TfsAuth) -> str:
    session_id = secrets.token_urlsafe(32)
    with _lock:
        _sessions[session_id] = auth
    return session_id


def get_session(session_id: str | None) -> TfsAuth | None:
    if not session_id:
        return None
    with _lock:
        return _sessions.get(session_id)


def delete_session(session_id: str | None) -> None:
    if not session_id:
        return
    with _lock:
        _sessions.pop(session_id, None)
