import json
from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class TfsAuth:
    base_url: str
    project: str
    project_id: str | None = None
    domain: str | None = None
    pat: str | None = None
    username: str | None = None
    password: str | None = None
    cookie: str | None = None
    extra_headers: dict[str, str] | None = None

    def has_credentials(self) -> bool:
        return bool(
            self.pat
            or (self.username and self.password)
            or self.cookie
            or self.extra_headers
        )

    @property
    def account_key(self) -> str:
        if self.username:
            return self.username.strip().lower()
        if self.pat:
            return f"pat:{self.pat[:8]}"
        return "anonymous"


def parse_extra_headers(raw: str | dict[str, str] | None) -> dict[str, str] | None:
    if not raw:
        return None
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("extraHeaders must be a JSON object")
    return {str(key): str(value) for key, value in parsed.items()}


def build_tfs_auth(
    *,
    base_url: str | None = None,
    project: str | None = None,
    project_id: str | None = None,
    domain: str | None = None,
    pat: str | None = None,
    username: str | None = None,
    password: str | None = None,
    cookie: str | None = None,
    extra_headers: str | dict[str, str] | None = None,
) -> TfsAuth:
    resolved_project = (project or "").strip()
    if not resolved_project:
        raise ValueError("project is required")

    return TfsAuth(
        base_url=(base_url or settings.tfs_base_url).rstrip("/"),
        project=resolved_project,
        project_id=(project_id or "").strip() or None,
        domain=(domain or "").strip() or None,
        pat=(pat or "").strip() or None,
        username=(username or "").strip() or None,
        password=password or None,
        cookie=(cookie or "").strip() or None,
        extra_headers=parse_extra_headers(extra_headers),
    )
