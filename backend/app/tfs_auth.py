import json
from dataclasses import dataclass, replace

from app.config import settings


@dataclass(frozen=True)
class TfsIdentity:
    """Пользователь TFS из connectionData (точно для PAT и логина)."""

    display_name: str | None = None
    unique_name: str | None = None
    descriptor: str | None = None
    identity_id: str | None = None
    email: str | None = None

    def match_tokens(self) -> set[str]:
        tokens: set[str] = set()
        for raw in (
            self.display_name,
            self.unique_name,
            self.descriptor,
            self.identity_id,
            self.email,
        ):
            if not raw:
                continue
            value = str(raw).casefold().strip()
            if value:
                tokens.add(value)
            if "\\" in value:
                tokens.add(value.split("\\")[-1])
            if "@" in value:
                tokens.add(value.split("@")[0])
            if self.display_name and " " in self.display_name:
                tokens.add(self.display_name.split()[0].casefold())
        return tokens


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
    tfs_display_name: str | None = None
    tfs_unique_name: str | None = None
    tfs_descriptor: str | None = None
    tfs_identity_id: str | None = None
    tfs_email: str | None = None

    def tfs_identity(self) -> TfsIdentity:
        return TfsIdentity(
            display_name=self.tfs_display_name,
            unique_name=self.tfs_unique_name,
            descriptor=self.tfs_descriptor,
            identity_id=self.tfs_identity_id,
            email=self.tfs_email,
        )

    def identity_match_tokens(self) -> set[str]:
        tokens = self.tfs_identity().match_tokens()
        if self.username:
            user = self.username.casefold().strip()
            if user:
                tokens.add(user)
                if "\\" in user:
                    tokens.add(user.split("\\")[-1])
                if "@" in user:
                    tokens.add(user.split("@")[0])
        return tokens

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
        tfs_display_name=None,
        tfs_unique_name=None,
        tfs_descriptor=None,
        tfs_identity_id=None,
        tfs_email=None,
    )


def attach_tfs_identity(auth: TfsAuth, identity: TfsIdentity) -> TfsAuth:
    return replace(
        auth,
        tfs_display_name=identity.display_name,
        tfs_unique_name=identity.unique_name,
        tfs_descriptor=identity.descriptor,
        tfs_identity_id=identity.identity_id,
        tfs_email=identity.email,
    )
