import asyncio
import re
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings
from app.http_auth import build_http_auth
from app.json_utils import as_dict, as_list, as_relation_list, as_work_item_list
from app.tfs_auth import TfsAuth, TfsIdentity
from app.tfs_identity import (
    connection_user_from_payload,
    identity_from_auth_login,
    identity_from_connection_user,
    identity_from_identity_ref,
    identity_from_profile,
    identity_from_work_item_fields,
    identity_has_tokens,
    merge_tfs_identities,
)


def wiql_escape(value: str) -> str:
    return value.replace("'", "''")


def wiql_quote(value: str) -> str:
    return f"'{wiql_escape(value)}'"


def _api_version_candidates(preferred: str | None = None) -> tuple[str, ...]:
    ordered = (preferred or settings.tfs_api_version, "6.1", "6.0", "5.1")
    seen: set[str] = set()
    result: list[str] = []
    for version in ordered:
        version = version.strip()
        if not version or version in seen:
            continue
        seen.add(version)
        result.append(version)
    return tuple(result)


def identity_display(value: Any) -> str | None:
    if isinstance(value, dict):
        identity = value.get("identityRef") if isinstance(value.get("identityRef"), dict) else value
        return identity.get("displayName") or value.get("distinctDisplayName")
    if isinstance(value, str):
        return value.split("<")[0].strip() if "<" in value else value
    return None


def _classification_field_path(path: str, structure_type: str) -> str:
    parts = [part for part in path.split("\\") if part]
    if len(parts) > 1 and parts[1].casefold() == structure_type.casefold():
        parts.pop(1)
    return "\\".join(parts)


def _walk_classification_nodes(nodes: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        result.append(node)
        for child in as_list(node.get("children")):
            walk(child)

    for node in as_list(nodes):
        walk(node)
    return result


class TfsClient:
    def __init__(self, tfs_auth: TfsAuth, *, use_ntlm: bool = True) -> None:
        if not tfs_auth.has_credentials():
            raise ValueError("TFS credentials are not configured.")

        headers = {"Accept": "application/json"}
        http_auth = build_http_auth(tfs_auth, use_ntlm=use_ntlm)
        if tfs_auth.cookie:
            headers["Cookie"] = tfs_auth.cookie
        if tfs_auth.extra_headers:
            headers.update(tfs_auth.extra_headers)

        self.tfs_auth = tfs_auth
        self.project = tfs_auth.project
        self.project_id = tfs_auth.project_id
        self.base_url = tfs_auth.base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=http_auth,
            headers=headers,
            timeout=settings.tfs_timeout_seconds,
            verify=settings.tfs_verify_tls,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def get_connection_authenticated_user(self) -> dict[str, Any] | None:
        for api_version in _api_version_candidates("5.0"):
            response = await self.client.get(
                "/_apis/connectionData",
                params={
                    "connectOptions": "includeServices",
                    "lastChangeId": "-1",
                    "api-version": api_version,
                },
            )
            if response.status_code != 200:
                continue
            payload = response.json()
            if not isinstance(payload, dict):
                continue
            user = connection_user_from_payload(payload)
            if user:
                return user
        return None

    async def get_profile_identity(self) -> TfsIdentity | None:
        paths = (
            "/_apis/profile/profiles/me",
            f"/{self.project}/_apis/profile/profiles/me",
        )
        for path in paths:
            for api_version in _api_version_candidates("6.0"):
                response = await self.client.get(path, params={"api-version": api_version})
                if response.status_code != 200:
                    continue
                payload = response.json()
                if not isinstance(payload, dict):
                    continue
                identity = identity_from_profile(payload)
                if identity_has_tokens(identity):
                    return identity
        return None

    async def search_identity(self, filter_value: str) -> TfsIdentity | None:
        needle = filter_value.strip()
        if not needle:
            return None
        for api_version in _api_version_candidates("5.1"):
            for search_filter in ("General", "AccountName", "DisplayName"):
                response = await self.client.get(
                    "/_apis/identities",
                    params={
                        "searchFilter": search_filter,
                        "filterValue": needle,
                        "queryMembership": "None",
                        "api-version": api_version,
                    },
                )
                if response.status_code != 200:
                    continue
                payload = response.json()
                if not isinstance(payload, dict):
                    continue
                for item in as_list(payload.get("value")):
                    if not isinstance(item, dict):
                        continue
                    identity = identity_from_identity_ref(item)
                    if identity_has_tokens(identity):
                        return identity
        return None

    async def get_identity_via_me_wiql(self) -> TfsIdentity | None:
        """Узнать владельца PAT через @Me на любой недавней задаче."""
        project = wiql_quote(self.project)
        wiql = (
            f"SELECT TOP 1 [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject] = {project} AND ("
            "[System.ChangedBy] = @Me OR [System.AssignedTo] = @Me OR [System.CreatedBy] = @Me"
            ") ORDER BY [System.ChangedDate] DESC"
        )
        try:
            payload = await self.run_wiql(wiql)
        except httpx.HTTPError:
            return None
        item_id: int | None = None
        for item in as_list(payload.get("workItems")):
            if isinstance(item, dict) and item.get("id"):
                item_id = int(item["id"])
                break
        if item_id is None:
            return None
        items = await self.get_work_items_batch(
            [item_id],
            fields=[
                "System.Id",
                "System.ChangedBy",
                "System.AssignedTo",
                "System.CreatedBy",
            ],
        )
        for item in items:
            fields = as_dict(item.get("fields"))
            identity = identity_from_work_item_fields(fields)
            if identity_has_tokens(identity):
                return identity
        return None

    async def get_authenticated_user_identity(self) -> TfsIdentity | None:
        """Владелец PAT: connectionData, profile, WIQL @Me, identities search, логин из формы."""
        from_connection: TfsIdentity | None = None
        user = await self.get_connection_authenticated_user()
        if user:
            parsed = identity_from_connection_user(user)
            if identity_has_tokens(parsed):
                from_connection = parsed
        from_profile = await self.get_profile_identity()
        from_me = await self.get_identity_via_me_wiql()
        merged = merge_tfs_identities(from_connection, from_profile, from_me)
        if identity_has_tokens(merged):
            return merged
        for needle in (
            self.tfs_auth.tfs_unique_name,
            self.tfs_auth.tfs_email,
            self.tfs_auth.username,
        ):
            if not needle:
                continue
            found = await self.search_identity(str(needle))
            if identity_has_tokens(found):
                return merge_tfs_identities(merged, found)
        from_login = identity_from_auth_login(self.tfs_auth)
        return merge_tfs_identities(merged, from_login)

    async def get_authenticated_user_name(self) -> str | None:
        identity = await self.get_authenticated_user_identity()
        return identity.display_name if identity else None

    def work_item_url(self, item_id: int) -> str:
        return f"{self.base_url}/{self.project}/_workitems/edit/{item_id}"

    async def run_wiql(self, query: str) -> dict[str, Any]:
        normalized = " ".join(line.strip() for line in query.strip().splitlines())
        last_response: httpx.Response | None = None
        for api_version in _api_version_candidates():
            response = await self.client.post(
                f"/{self.project}/_apis/wit/wiql",
                params={"api-version": api_version},
                json={"query": normalized},
            )
            last_response = response
            if response.status_code == 200:
                body = response.json()
                return body if isinstance(body, dict) else {}
            if response.status_code == 400 and "out of range" in response.text.lower():
                continue
        if last_response is not None:
            last_response.raise_for_status()
        raise httpx.HTTPError("WIQL request failed")

    async def get_work_item(self, item_id: int, *, expand: str | None = "Relations") -> dict[str, Any]:
        params: dict[str, str] = {}
        if expand:
            params["$expand"] = expand
        last_response: httpx.Response | None = None
        for api_version in _api_version_candidates():
            response = await self.client.get(
                f"/_apis/wit/workItems/{item_id}",
                params={**params, "api-version": api_version},
            )
            last_response = response
            if response.status_code == 200:
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
        if last_response is not None:
            last_response.raise_for_status()
        raise httpx.HTTPError(f"Failed to load work item {item_id}")

    async def get_work_items_batch(self, ids: list[int], fields: list[str] | None = None) -> list[dict[str, Any]]:
        if not ids:
            return []
        default_fields = [
            "System.Id",
            "System.Title",
            "System.WorkItemType",
            "System.State",
            "System.AreaPath",
            "System.TeamProject",
            "Microsoft.VSTS.Scheduling.CompletedWork",
        ]
        result: list[dict[str, Any]] = []
        for offset in range(0, len(ids), settings.tfs_batch_size):
            chunk = ids[offset : offset + settings.tfs_batch_size]
            body: dict[str, Any] = {"ids": chunk, "errorPolicy": 2, "fields": fields or default_fields}
            response = await self._post_with_api_versions(
                f"/{self.project}/_apis/wit/workItemsBatch",
                json=body,
            )
            response.raise_for_status()
            batch = response.json()
            result.extend(as_work_item_list(batch.get("value") if isinstance(batch, dict) else None))
            await asyncio.sleep(settings.tfs_request_delay_seconds)
        return result

    async def search_work_items(self, query_text: str, *, limit: int = 20) -> list[dict[str, Any]]:
        text = query_text.strip()
        if not text:
            return []

        project = wiql_quote(self.project)
        types = ", ".join(
            wiql_quote(item)
            for item in (
                settings.change_request_type_name,
                settings.requirement_type_name,
                settings.error_type_name,
                settings.task_type_name,
            )
        )

        if text.isdigit():
            ids = [int(text)]
        else:
            safe = wiql_escape(text)
            wiql = (
                f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = {project} "
                f"AND [System.WorkItemType] IN ({types}) "
                f"AND [System.Title] CONTAINS '{safe}' "
                f"ORDER BY [System.ChangedDate] DESC"
            )
            payload = await self.run_wiql(wiql)
            ids = [item["id"] for item in as_list(payload.get("workItems")) if isinstance(item, dict)][:limit]

        items = await self.get_work_items_batch(ids[:limit])
        return [self.normalize_item(item) for item in items]

    async def get_work_item_updates(self, item_id: int) -> list[dict[str, Any]]:
        last_response: httpx.Response | None = None
        for api_version in _api_version_candidates():
            response = await self.client.get(
                f"/_apis/wit/workitems/{item_id}/updates",
                params={"api-version": api_version},
            )
            last_response = response
            if response.status_code == 200:
                payload = response.json()
                if isinstance(payload, dict):
                    return as_list(payload.get("value"))
                return []
        if last_response is not None:
            last_response.raise_for_status()
        raise httpx.HTTPError(f"Failed to load updates for work item {item_id}")

    async def get_parent_work_item_id(self, item_id: int) -> int | None:
        item = await self.get_work_item(item_id, expand="Relations")
        for relation in as_relation_list(item.get("relations")):
            rel = relation.get("rel") or ""
            if rel not in ("System.LinkTypes.Hierarchy-Reverse", "Parent"):
                continue
            url = str(relation.get("url") or "")
            try:
                return int(url.rstrip("/").split("/")[-1])
            except ValueError:
                continue
        return None

    async def _wiql_task_ids(self, wiql: str, *, limit: int) -> list[int]:
        payload = await self.run_wiql(wiql)
        ids: list[int] = []
        for item in as_list(payload.get("workItems")):
            if not isinstance(item, dict):
                continue
            try:
                ids.append(int(item["id"]))
            except (KeyError, TypeError, ValueError):
                continue
            if len(ids) >= limit:
                break
        return ids

    async def find_tracking_tasks_for_me(
        self,
        *,
        changed_since: date,
        limit: int = 80,
    ) -> list[int]:
        """Дочерние «Роль — активность»: назначены, изменены или созданы вами (@Me)."""
        project = wiql_quote(self.project)
        task_type = wiql_quote(settings.task_type_name)
        since = changed_since.isoformat()
        ids: list[int] = []
        for clause in (
            "[System.AssignedTo] = @Me",
            "[System.ChangedBy] = @Me",
            "[System.CreatedBy] = @Me",
        ):
            wiql = (
                f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = {project} "
                f"AND [System.WorkItemType] = {task_type} "
                f"AND {clause} "
                f"AND [System.ChangedDate] >= '{since}' "
                f"ORDER BY [System.ChangedDate] DESC"
            )
            try:
                found = await self._wiql_task_ids(wiql, limit=limit)
            except Exception:
                found = []
            for task_id in found:
                if task_id not in ids:
                    ids.append(task_id)
                if len(ids) >= limit:
                    return ids
        return ids

    async def find_parent_work_items_for_me(
        self,
        *,
        changed_since: date,
        limit: int = 50,
    ) -> list[int]:
        """ЗНИ/требования/ошибки, с которыми работает текущий пользователь (@Me)."""
        project = wiql_quote(self.project)
        since = changed_since.isoformat()
        parent_types = (
            settings.change_request_type_name,
            settings.requirement_type_name,
            settings.error_type_name,
        )
        ids: list[int] = []
        for type_name in parent_types:
            work_type = wiql_quote(type_name)
            for clause in (
                "[System.AssignedTo] = @Me",
                "[System.ChangedBy] = @Me",
                "[System.CreatedBy] = @Me",
            ):
                wiql = (
                    f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = {project} "
                    f"AND [System.WorkItemType] = {work_type} "
                    f"AND {clause} "
                    f"AND [System.ChangedDate] >= '{since}' "
                    f"ORDER BY [System.ChangedDate] DESC"
                )
                try:
                    found = await self._wiql_task_ids(wiql, limit=limit)
                except Exception:
                    found = []
                for work_id in found:
                    if work_id not in ids:
                        ids.append(work_id)
                    if len(ids) >= limit:
                        return ids
        return ids

    async def find_tracking_tasks_assigned_to_user(
        self,
        *,
        unique_name: str,
        changed_since: date,
        limit: int = 80,
    ) -> list[int]:
        project = wiql_quote(self.project)
        task_type = wiql_quote(settings.task_type_name)
        user = wiql_quote(unique_name.strip())
        wiql = (
            f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = {project} "
            f"AND [System.WorkItemType] = {task_type} "
            f"AND [System.AssignedTo] = {user} "
            f"AND [System.ChangedDate] >= '{changed_since.isoformat()}' "
            f"ORDER BY [System.ChangedDate] DESC"
        )
        return await self._wiql_task_ids(wiql, limit=limit)

    async def find_task_ids_created_by_user(
        self,
        *,
        unique_name: str,
        changed_since: date,
        limit: int = 80,
    ) -> list[int]:
        """Задачи, которые вы создали (завели под требованием)."""
        project = wiql_quote(self.project)
        task_type = wiql_quote(settings.task_type_name)
        user = wiql_quote(unique_name.strip())
        wiql = (
            f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = {project} "
            f"AND [System.WorkItemType] = {task_type} "
            f"AND [System.CreatedBy] = {user} "
            f"AND [System.ChangedDate] >= '{changed_since.isoformat()}' "
            f"ORDER BY [System.ChangedDate] DESC"
        )
        return await self._wiql_task_ids(wiql, limit=limit)

    async def find_task_ids_changed_by_user(
        self,
        *,
        unique_name: str,
        changed_since: date,
        limit: int = 80,
    ) -> list[int]:
        """Задачи, которые менял текущий пользователь."""
        project = wiql_quote(self.project)
        task_type = wiql_quote(settings.task_type_name)
        user = wiql_quote(unique_name.strip())
        wiql = (
            f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = {project} "
            f"AND [System.WorkItemType] = {task_type} "
            f"AND [System.ChangedBy] = {user} "
            f"AND [System.ChangedDate] >= '{changed_since.isoformat()}' "
            f"ORDER BY [System.ChangedDate] DESC"
        )
        return await self._wiql_task_ids(wiql, limit=limit)

    async def find_task_ids_with_completed_work(
        self,
        *,
        changed_since: date,
        limit: int = 80,
    ) -> list[int]:
        project = wiql_quote(self.project)
        task_type = wiql_quote(settings.task_type_name)
        wiql = (
            f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = {project} "
            f"AND [System.WorkItemType] = {task_type} "
            f"AND [Microsoft.VSTS.Scheduling.CompletedWork] > 0 "
            f"AND [System.ChangedDate] >= '{changed_since.isoformat()}' "
            f"ORDER BY [System.ChangedDate] DESC"
        )
        payload = await self.run_wiql(wiql)
        ids: list[int] = []
        for item in as_list(payload.get("workItems")):
            if not isinstance(item, dict):
                continue
            try:
                ids.append(int(item["id"]))
            except (KeyError, TypeError, ValueError):
                continue
            if len(ids) >= limit:
                break
        return ids

    async def get_child_tasks(self, parent_id: int) -> list[dict[str, Any]]:
        parent = await self.get_work_item(parent_id, expand="Relations")
        child_ids: list[int] = []
        for relation in as_relation_list(parent.get("relations")):
            attributes = as_dict(relation.get("attributes"))
            if attributes.get("name") != "Child":
                continue
            url = relation.get("url", "")
            try:
                child_ids.append(int(url.rstrip("/").split("/")[-1]))
            except ValueError:
                continue
        if not child_ids:
            return []
        items = await self.get_work_items_batch(
            child_ids,
            fields=["System.Id", "System.Title", "System.State", "System.AssignedTo", "System.WorkItemType", "System.AreaPath"],
        )
        return [self.normalize_item(item) for item in items]

    async def find_child_task(self, parent_id: int, title: str) -> dict[str, Any] | None:
        for child in await self.get_child_tasks(parent_id):
            if child.get("title") == title:
                return child
        return None

    async def create_child_task(
        self,
        parent_id: int,
        *,
        title: str,
        area_path: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parent_url = f"{self.base_url}/_apis/wit/workItems/{parent_id}"
        task_type = settings.task_type_name
        patch_ops: list[dict[str, Any]] = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.AreaPath", "value": area_path},
            {
                "op": "add",
                "path": "/relations/-",
                "value": {"rel": "System.LinkTypes.Hierarchy-Reverse", "url": parent_url},
            },
        ]
        for field_name, value in (extra_fields or {}).items():
            if value not in (None, ""):
                patch_ops.append({"op": "add", "path": f"/fields/{field_name}", "value": value})
        return await self.patch_work_item_new(task_type, patch_ops)

    async def set_work_item_field(self, item_id: int, field_name: str, value: Any) -> dict[str, Any]:
        return await self.patch_work_item(
            item_id,
            [{"op": "replace", "path": f"/fields/{field_name}", "value": value}],
        )

    async def _get_with_api_versions(self, path: str, *, params: dict[str, str] | None = None) -> httpx.Response:
        last_response: httpx.Response | None = None
        query = dict(params or {})
        for api_version in _api_version_candidates():
            response = await self.client.get(path, params={**query, "api-version": api_version})
            last_response = response
            if response.status_code == 200:
                return response
            if response.status_code == 400 and "out of range" in response.text.lower():
                continue
        if last_response is not None:
            return last_response
        raise httpx.HTTPError(f"GET failed for {path}")

    def _wit_project_prefixes(self) -> list[str]:
        prefixes = [f"/{self.project}"]
        if self.project_id and self.project_id != self.project:
            prefixes.append(f"/{self.project_id}")
        return prefixes

    def _task_type_names(self) -> list[str]:
        names: list[str] = []
        for candidate in (settings.task_type_name, settings.task_type_name.casefold()):
            if candidate and candidate not in names:
                names.append(candidate)
        return names

    async def get_work_item_type_field(self, work_item_type: str, field_ref: str) -> dict[str, Any]:
        encoded_type = quote(work_item_type, safe="")
        last_response: httpx.Response | None = None
        for prefix in self._wit_project_prefixes():
            path = f"{prefix}/_apis/wit/workitemtypes/{encoded_type}/fields/{field_ref}"
            response = await self._get_with_api_versions(path)
            last_response = response
            if response.status_code == 200:
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
        response = await self._get_with_api_versions(f"/_apis/wit/fields/{field_ref}")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def read_cost_project_value(self, item: dict[str, Any]) -> str | None:
        field_ref = settings.cost_project_field
        value = self.read_field_value(item, field_ref)
        if value:
            return value
        fields = as_dict(item.get("fields"))
        for key, raw in fields.items():
            key_lower = str(key).casefold()
            if "project" in key_lower and "control" in key_lower:
                wrapped = {"fields": {key: raw}}
                candidate = self.read_field_value(wrapped, key)
                if candidate:
                    return candidate
        return None

    def _parse_fps_allowed_values(self, text: str) -> list[str]:
        """Справочник из правил формы TFS (FPS), поле project.control ≈ rule id 527."""
        values: list[str] = []
        patterns = (
            r'"527"\s*,\s*1\s*,\s*\["AllowedValues",\s*\[\[(.*?)\]\]\]',
            r'\\"527\\"\s*,\s*1\s*,\s*\[\\"AllowedValues\\"\s*,\s*\[\[(.*?)\]\]\]',
            r'\["AllowedValues",\s*\[\[(.*?)\]\]\]',
            r'\[\\"AllowedValues\\"\s*,\s*\[\[(.*?)\]\]\]',
        )
        for pattern in patterns:
            for block in re.findall(pattern, text, flags=re.DOTALL):
                for match in re.findall(r'"([^"\\]+(?:\\.[^"\\]*)*)"', block):
                    cleaned = match.replace('\\"', '"').strip()
                    if cleaned and cleaned not in values:
                        values.append(cleaned)
                if values and "B2B" in " ".join(values[:5]):
                    return values
        return values

    async def fetch_cost_projects_from_task_form(self) -> list[str]:
        """Правила формы «Задача» (как в tfs.t2.ru.har — B2B 2026, Digital Suite, …)."""
        encoded_type = quote(settings.task_type_name, safe="")
        paths = [
            f"/{self.project}/_workitems/edit/new?type={encoded_type}&__rt=fps&__ver=2",
        ]
        if self.project_id:
            paths.append(
                f"/{self.project_id}/_workitems/edit/new?type={encoded_type}&__rt=fps&__ver=2"
            )
        for path in paths:
            try:
                response = await self.client.get(path, headers={"Accept": "text/html,*/*"})
                if response.status_code != 200:
                    continue
                values = self._parse_fps_allowed_values(response.text)
                if values:
                    return values
            except httpx.HTTPError:
                continue
        return []

    async def query_distinct_cost_projects_wiql(self, *, limit: int = 250) -> list[str]:
        field_ref = settings.cost_project_field
        wiql = (
            "SELECT [System.Id] "
            "FROM WorkItems "
            f"WHERE [System.TeamProject] = {wiql_quote(self.project)} "
            f"AND [System.WorkItemType] = {wiql_quote(settings.task_type_name)} "
            f"AND [{field_ref}] <> '' "
            "ORDER BY [System.ChangedDate] DESC"
        )
        try:
            payload = await self.run_wiql(wiql)
        except httpx.HTTPError:
            return []
        ids = [
            int(item["id"])
            for item in as_list(payload.get("workItems"))
            if isinstance(item, dict) and item.get("id") is not None
        ][:limit]
        if not ids:
            return []
        items = await self.get_work_items_batch(
            ids,
            fields=["System.Id", "System.WorkItemType", field_ref],
        )
        values: list[str] = []
        for item in items:
            value = self.read_cost_project_value(item)
            if value:
                values.append(value)
        return list(dict.fromkeys(values))

    def _collect_picklist_values(self, options: list[str], raw: Any) -> None:
        if raw is None:
            return
        if isinstance(raw, str):
            value = raw.strip()
            if value:
                options.append(value)
            return
        if isinstance(raw, list):
            for item in raw:
                self._collect_picklist_values(options, item)
            return
        if not isinstance(raw, dict):
            return
        for key in ("value", "displayName", "name", "text"):
            candidate = raw.get(key)
            if isinstance(candidate, str) and candidate.strip():
                options.append(candidate.strip())
        for key in ("allowedValues", "items", "picklistItems", "listItems", "values"):
            if key in raw:
                self._collect_picklist_values(options, raw[key])
        if "listMetadata" in raw:
            self._collect_picklist_values(options, raw["listMetadata"])

    def _merge_cost_project_options(self, *sources: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for source in sources:
            for value in source:
                cleaned = value.strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                merged.append(cleaned)
        return merged

    async def get_cost_project_options(self, work_item_type: str | None = None) -> list[str]:
        """Справочник «Проект учёта затрат» (project.control) — тип «Задача», не ЗНИ."""
        field_ref = settings.cost_project_field
        rest_options: list[str] = []

        try:
            response = await self._get_with_api_versions(f"/_apis/wit/fields/{field_ref}")
            if response.status_code == 200:
                meta = response.json()
                if isinstance(meta, dict):
                    self._collect_picklist_values(rest_options, meta.get("allowedValues"))
                    self._collect_picklist_values(rest_options, meta.get("listMetadata"))
        except httpx.HTTPError:
            pass

        types_to_try = self._task_type_names()
        if work_item_type and work_item_type not in types_to_try:
            types_to_try.append(work_item_type)

        for wit in types_to_try:
            try:
                meta = await self.get_work_item_type_field(wit, field_ref)
                self._collect_picklist_values(rest_options, meta)
            except httpx.HTTPError:
                pass

            for prefix in self._wit_project_prefixes():
                try:
                    encoded_type = quote(wit, safe="")
                    response = await self._get_with_api_versions(
                        f"{prefix}/_apis/wit/workitemtypes/{encoded_type}/fields"
                    )
                    if response.status_code != 200:
                        continue
                    payload = response.json()
                    for row in as_list(payload.get("value") if isinstance(payload, dict) else payload):
                        if not isinstance(row, dict):
                            continue
                        if row.get("referenceName") == field_ref or row.get("name") == field_ref:
                            self._collect_picklist_values(rest_options, row)
                            break
                except httpx.HTTPError:
                    pass

            if rest_options and wit == settings.task_type_name:
                break

        fps_options = await self.fetch_cost_projects_from_task_form()
        wiql_options = await self.query_distinct_cost_projects_wiql()

        return self._merge_cost_project_options(rest_options, fps_options, wiql_options)

    async def collect_cost_projects_from_child_tasks(self, parent_id: int) -> list[str]:
        """Значения project.control с уже существующих дочерних задач под ЗНИ/требованием."""
        field_ref = settings.cost_project_field
        parent = await self.get_work_item(parent_id, expand="Relations")
        child_ids: list[int] = []
        for relation in as_relation_list(parent.get("relations")):
            attributes = as_dict(relation.get("attributes"))
            if attributes.get("name") != "Child":
                continue
            url = relation.get("url", "")
            try:
                child_ids.append(int(url.rstrip("/").split("/")[-1]))
            except ValueError:
                continue
        if not child_ids:
            return []

        batch_fields = [
            "System.Id",
            "System.WorkItemType",
            field_ref,
        ]
        pairs: list[tuple[int, str]] = []
        items = await self.get_work_items_batch(child_ids, fields=batch_fields)
        for item in items:
            fields = as_dict(item.get("fields"))
            if fields.get("System.WorkItemType") != settings.task_type_name:
                continue
            value = self.read_cost_project_value(item)
            if not value:
                continue
            item_id = int(item.get("id") or fields.get("System.Id") or 0)
            pairs.append((item_id, value))
        pairs.sort(key=lambda row: row[0], reverse=True)
        values: list[str] = []
        for _, value in pairs:
            if value not in values:
                values.append(value)
        return values

    def read_field_value(self, item: dict[str, Any], field_ref: str) -> str | None:
        fields = as_dict(item.get("fields"))
        value = fields.get(field_ref)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for key in ("displayName", "uniqueName", "name", "value"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        return None

    async def get_latest_iteration_path(
        self,
        *,
        area_path: str | None = None,
    ) -> str | None:
        response = await self._get_with_api_versions(
            f"/{self.project}/_apis/wit/classificationNodes",
            params={"$depth": "15"},
        )
        response.raise_for_status()
        payload = response.json()
        roots = payload.get("value") if isinstance(payload, dict) else payload
        candidates: list[tuple[str, str, str]] = []

        for node in _walk_classification_nodes(roots):
            if node.get("structureType") != "iteration":
                continue
            if node.get("hasChildren"):
                continue
            raw_path = node.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            field_path = _classification_field_path(raw_path, "Iteration")
            attributes = as_dict(node.get("attributes"))
            finish_date = str(attributes.get("finishDate") or "")
            start_date = str(attributes.get("startDate") or "")
            if finish_date or start_date:
                candidates.append((finish_date, start_date, field_path))

        if not candidates:
            return None

        prefixes: list[str] = []
        if area_path:
            area_parts = [part for part in area_path.split("\\") if part]
            if len(area_parts) > 1:
                prefixes.append(
                    "\\".join((area_parts[0], "Общие", area_parts[1])) + "\\"
                )
        prefixes.append(f"{self.project}\\Общие\\")

        preferred = [
            item
            for item in candidates
            if any(
                item[2].casefold().startswith(prefix.casefold())
                for prefix in prefixes
            )
        ]
        pool = preferred or candidates
        today = date.today().isoformat()
        current_or_past = [
            item for item in pool if not item[1] or item[1][:10] <= today
        ]
        _finish_date, _start_date, field_path = max(current_or_past or pool)
        return field_path

    async def patch_work_item(self, item_id: int, patch_ops: list[dict[str, Any]]) -> dict[str, Any]:
        path = f"/{self.project}/_apis/wit/workitems/{item_id}"
        headers = {"Content-Type": "application/json-patch+json"}
        last_response: httpx.Response | None = None
        for api_version in _api_version_candidates():
            response = await self.client.patch(
                path,
                params={"api-version": api_version},
                json=patch_ops,
                headers=headers,
            )
            last_response = response
            if response.status_code in (200, 201):
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
            if response.status_code == 400 and "out of range" in response.text.lower():
                continue
        if last_response is not None:
            last_response.raise_for_status()
        raise httpx.HTTPError(f"PATCH failed for work item {item_id}")

    async def patch_work_item_new(self, work_item_type: str, patch_ops: list[dict[str, Any]]) -> dict[str, Any]:
        encoded_type = quote(work_item_type, safe="")
        path = f"/{self.project}/_apis/wit/workitems/${encoded_type}"
        headers = {"Content-Type": "application/json-patch+json"}
        last_response: httpx.Response | None = None
        for api_version in _api_version_candidates():
            response = await self.client.post(
                path,
                params={"api-version": api_version},
                json=patch_ops,
                headers=headers,
            )
            last_response = response
            if response.status_code in (200, 201):
                payload = response.json()
                normalized = self.normalize_item(payload if isinstance(payload, dict) else {})
                return normalized
            if response.status_code == 400 and "out of range" in response.text.lower():
                continue
        if last_response is not None:
            last_response.raise_for_status()
        raise httpx.HTTPError("Create work item failed")

    async def update_completed_work(
        self,
        item_id: int,
        hours: float,
        *,
        comment: str | None = None,
    ) -> dict[str, Any]:
        completed = round(hours, 2)
        ops: list[dict[str, Any]] = [
            {
                "op": "replace",
                "path": "/fields/Microsoft.VSTS.Scheduling.CompletedWork",
                "value": completed,
            },
            {
                "op": "replace",
                "path": f"/fields/{settings.remaining_work_field}",
                "value": -completed,
            },
        ]
        if comment:
            ops.append({"op": "add", "path": "/fields/System.History", "value": comment})
        return await self.patch_work_item(item_id, ops)

    async def _post_with_api_versions(self, path: str, *, json: dict[str, Any]) -> httpx.Response:
        last_response: httpx.Response | None = None
        for api_version in _api_version_candidates():
            response = await self.client.post(path, params={"api-version": api_version}, json=json)
            last_response = response
            if response.status_code == 200:
                return response
            if response.status_code == 400 and "out of range" in response.text.lower():
                continue
        if last_response is not None:
            return last_response
        raise httpx.HTTPError(f"Request failed for {path}")

    def normalize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        fields = as_dict(item.get("fields"))
        work_item_type = fields.get("System.WorkItemType", "")
        return {
            "id": item.get("id") or fields.get("System.Id"),
            "title": fields.get("System.Title", ""),
            "workItemType": work_item_type,
            "state": fields.get("System.State", ""),
            "areaPath": fields.get("System.AreaPath", ""),
            "teamProject": fields.get("System.TeamProject", self.project),
            "completedWork": fields.get("Microsoft.VSTS.Scheduling.CompletedWork") or 0,
            "assignedTo": fields.get("System.AssignedTo"),
            "tfsUrl": self.work_item_url(int(item.get("id") or fields.get("System.Id") or 0)),
            "kind": self.item_kind(work_item_type),
        }

    def item_kind(self, work_item_type: str) -> str:
        if work_item_type == settings.change_request_type_name:
            return "change_request"
        if work_item_type == settings.requirement_type_name:
            return "requirement"
        if work_item_type == settings.error_type_name:
            return "error"
        if work_item_type == settings.task_type_name:
            return "task"
        return "other"
