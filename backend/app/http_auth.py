from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.tfs_auth import TfsAuth

EMAIL_HOST_AD_DOMAINS: dict[str, list[str]] = {
    # В ListDelta TFS часто AD_UserID = T2RU\user, в форме входа — TELE2\user или email.
    "t2.ru": ["TELE2", "T2", "T2RU"],
    "tele2.ru": ["TELE2", "T2RU"],
}


def format_username(username: str, domain: str | None = None) -> str:
    user = username.strip()
    if not user:
        return ""
    if "\\" in user or "@" in user:
        return user
    domain_value = (domain or "").strip()
    if domain_value:
        return f"{domain_value}\\{user}"
    return user


def is_email_login(username: str) -> bool:
    return "@" in username.strip()


def expand_login_usernames(raw_user: str, domain: str | None = None) -> list[str]:
    user = raw_user.strip()
    if not user:
        return []

    result: list[str] = []

    def push(value: str) -> None:
        if value and value not in result:
            result.append(value)

    push(user)
    if "@" in user:
        local, _, host = user.partition("@")
        host_key = host.strip().lower()
        push(local)
        for ad_name in EMAIL_HOST_AD_DOMAINS.get(host_key, []):
            push(f"{ad_name}\\{local}")
        if domain:
            push(format_username(local, domain))
    else:
        if domain:
            push(format_username(user, domain))
            push(f"{user}@{domain}")

    return result


AD_DOMAIN_ALIASES: dict[str, list[str]] = {
    "tele2": ["T2RU", "T2"],
    "t2": ["T2RU", "TELE2"],
    "t2ru": ["TELE2"],
}


def ad_unique_name_aliases(unique_name: str) -> list[str]:
    """TELE2\\user в сессии, ListDelta — T2RU\\user: оба варианта для Basic/PAT."""
    raw = unique_name.strip()
    if not raw or "\\" not in raw:
        return [raw] if raw else []
    domain, user = raw.split("\\", 1)
    user = user.strip()
    if not user:
        return [raw]
    result: list[str] = []
    for value in (raw, user):
        if value and value not in result:
            result.append(value)
    for alt in AD_DOMAIN_ALIASES.get(domain.casefold(), []):
        candidate = f"{alt}\\{user}"
        if candidate not in result:
            result.append(candidate)
    return result


def pat_http_auth_candidates(auth: TfsAuth) -> list[tuple[str, str]]:
    """On-prem TFS tsapi: Basic(user, PAT). Пустой логин — последним (часто пустой ListDelta)."""
    if not auth.pat:
        return []
    pat = auth.pat.strip()
    seen: set[tuple[str, str]] = set()
    named: list[tuple[str, str]] = []

    def push(user: str) -> None:
        key = (user, pat)
        if key not in seen:
            seen.add(key)
            named.append(key)

    if auth.tfs_unique_name:
        for login in ad_unique_name_aliases(auth.tfs_unique_name):
            push(login)
    for raw in (
        auth.username,
        *expand_login_usernames((auth.username or "").strip(), auth.domain),
    ):
        if raw and str(raw).strip():
            for login in ad_unique_name_aliases(str(raw).strip()):
                push(login)
            push(str(raw).strip())
    push("")
    return named


def build_http_auth(auth: TfsAuth, *, use_ntlm: bool = True) -> Any | None:
    if auth.pat:
        return pat_http_auth_candidates(auth)[0]
    if auth.username and auth.password:
        username = auth.username.strip()
        if use_ntlm and not is_email_login(username):
            try:
                from httpx_ntlm import HttpNtlmAuth

                login_name = format_username(username, auth.domain)
                return HttpNtlmAuth(login_name, auth.password)
            except ImportError:
                pass
        login_name = format_username(username, auth.domain) if not is_email_login(username) else username
        return (login_name, auth.password)
    return None


@dataclass(frozen=True)
class AuthAttempt:
    label: str
    auth: TfsAuth
    use_ntlm: bool


def password_auth_candidates(auth: TfsAuth) -> list[AuthAttempt]:
    if not auth.username or not auth.password:
        return []
    raw_user = auth.username.strip()
    domain = (auth.domain or "").strip()
    email_login = is_email_login(raw_user)
    attempts: list[AuthAttempt] = []
    for username in expand_login_usernames(raw_user, domain or None):
        variant = replace(auth, username=username, pat=None, cookie=None, extra_headers=None)
        attempts.append(AuthAttempt(f"Basic ({username})", variant, False))
        if not email_login and domain and "\\" not in username:
            attempts.append(AuthAttempt(f"NTLM ({format_username(username, domain)})", variant, True))
    return attempts


def auth_attempts(auth: TfsAuth) -> list[AuthAttempt]:
    if auth.pat:
        return [AuthAttempt("PAT", auth, False)]
    if auth.cookie:
        return [AuthAttempt("Cookie", auth, False)]
    if auth.username and auth.password:
        return password_auth_candidates(auth)
    if auth.extra_headers:
        return [AuthAttempt("Headers", auth, False)]
    return []
