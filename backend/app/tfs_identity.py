"""Разбор идентификатора пользователя TFS из connectionData / profile."""

from __future__ import annotations

from typing import Any

from app.json_utils import as_dict
from app.tfs_auth import TfsIdentity


def _display_from_blob(value: Any) -> str | None:
    if isinstance(value, dict):
        identity = value.get("identityRef") if isinstance(value.get("identityRef"), dict) else value
        return identity.get("displayName") or value.get("distinctDisplayName")
    if isinstance(value, str):
        return value.split("<")[0].strip() if "<" in value else value.strip() or None
    return None


def _first_non_empty(*values: Any) -> str | None:
    for raw in values:
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return None


def identity_from_connection_user(user: dict[str, Any]) -> TfsIdentity:
    """connectionData.authenticatedUser (в т.ч. Tele2 без uniqueName)."""
    id_ref = as_dict(user.get("identityRef"))
    provider = _first_non_empty(user.get("providerDisplayName"), id_ref.get("providerDisplayName"))
    unique = _first_non_empty(
        user.get("uniqueName"),
        id_ref.get("uniqueName"),
        user.get("principalName"),
        id_ref.get("principalName"),
        provider,
    )
    email = _first_non_empty(
        user.get("mailAddress"),
        user.get("emailAddress"),
        id_ref.get("mailAddress"),
        id_ref.get("emailAddress"),
    )
    if not email and unique and "@" in unique:
        email = unique.split("\\")[-1] if "\\" in unique else unique
    return TfsIdentity(
        display_name=_first_non_empty(
            _display_from_blob(user),
            _display_from_blob(id_ref),
            user.get("customDisplayName"),
            user.get("providerDisplayName"),
        ),
        unique_name=unique,
        descriptor=_first_non_empty(user.get("descriptor"), id_ref.get("descriptor")),
        identity_id=_first_non_empty(user.get("id"), id_ref.get("id")),
        email=email,
    )


def _profile_core_value(core: dict[str, Any], name: str) -> str | None:
    entry = core.get(name)
    if isinstance(entry, dict):
        return _first_non_empty(entry.get("value"))
    return _first_non_empty(entry)


def identity_from_profile(profile: dict[str, Any]) -> TfsIdentity:
    core = as_dict(profile.get("coreAttributes"))
    display = _profile_core_value(core, "DisplayName")
    email = _profile_core_value(core, "EmailAddress")
    alias = _first_non_empty(
        _profile_core_value(core, "Alias"),
        profile.get("publicAlias"),
        profile.get("coreRevision"),
    )
    unique = _first_non_empty(alias, email)
    return TfsIdentity(
        display_name=display,
        unique_name=unique,
        descriptor=_first_non_empty(profile.get("descriptor")),
        identity_id=_first_non_empty(profile.get("id")),
        email=email,
    )


def merge_tfs_identities(*items: TfsIdentity | None) -> TfsIdentity | None:
    parts = [item for item in items if item is not None]
    if not parts:
        return None
    return TfsIdentity(
        display_name=_first_non_empty(*(p.display_name for p in parts)),
        unique_name=_first_non_empty(*(p.unique_name for p in parts)),
        descriptor=_first_non_empty(*(p.descriptor for p in parts)),
        identity_id=_first_non_empty(*(p.identity_id for p in parts)),
        email=_first_non_empty(*(p.email for p in parts)),
    )


def connection_user_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("authenticatedUser", "authorizedUser", "user"):
        user = as_dict(payload.get(key))
        if user:
            return user
    return None
