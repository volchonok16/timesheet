import json
import secrets
from dataclasses import asdict

from app.db import AuthSessionRow, SessionLocal
from app.tfs_auth import TfsAuth


def _serialize(auth: TfsAuth) -> str:
    return json.dumps(asdict(auth))


def _deserialize(raw: str) -> TfsAuth:
    data = json.loads(raw)
    return TfsAuth(**data)


def create_session(auth: TfsAuth) -> str:
    session_id = secrets.token_urlsafe(32)
    db = SessionLocal()
    try:
        db.add(AuthSessionRow(session_id=session_id, payload=_serialize(auth)))
        db.commit()
    finally:
        db.close()
    return session_id


def get_session(session_id: str | None) -> TfsAuth | None:
    if not session_id:
        return None
    db = SessionLocal()
    try:
        row = db.get(AuthSessionRow, session_id)
        if row is None:
            return None
        return _deserialize(row.payload)
    finally:
        db.close()


def delete_session(session_id: str | None) -> None:
    if not session_id:
        return
    db = SessionLocal()
    try:
        row = db.get(AuthSessionRow, session_id)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()
