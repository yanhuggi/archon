"""Jira REST API provider implementation."""

import json
import logging
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from server.config import DEFAULT_MAX_ATTACHMENT_SIZE, JiraConfig
from server.files import OutputPathError, commit_output_file, resolve_output_path
from server.jql_cache import CacheResult, JsonJqlCache

_MAX_FIELD_LENGTH = 2000
_DEFAULT_MAX_RESULTS = 50
_MAX_SEARCH_RESULTS = 200
_MAX_CACHED_VALUE_SUGGESTIONS = 1000
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_ATTACHMENT_SIZE = DEFAULT_MAX_ATTACHMENT_SIZE
_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")
_DEFAULT_FIELDS = (
    "summary,description,status,assignee,reporter,issuetype,"
    "priority,labels,created,updated,subtasks,issuelinks,"
    "attachment,parent,components,versions,fixVersions,duedate,resolution"
)

LOGGER = logging.getLogger(__name__)


def _status_error_code(status: int) -> str:
    if status in {401, 403}:
        return "authentication_error"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    return "upstream_error"


def _json_error(message: str, code: str, **context: object) -> str:
    return json.dumps({**context, "error": message, "error_code": code}, ensure_ascii=False)


def _http_error_message(exc: httpx.HTTPStatusError) -> str:
    """Extract bounded Jira JSON errors without returning arbitrary HTML bodies."""

    status = exc.response.status_code
    details: list[str] = []
    try:
        payload = exc.response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        messages = payload.get("errorMessages")
        if isinstance(messages, list):
            details.extend(str(message) for message in messages if message)
        errors = payload.get("errors")
        if isinstance(errors, dict):
            details.extend(f"{field}: {message}" for field, message in errors.items())
        message = payload.get("message")
        if isinstance(message, str) and message:
            details.append(message)
    detail = " ".join(" ".join(details).split())[:500]
    return f"Jira returned HTTP {status}" + (f": {detail}" if detail else "")


class _AttachmentTooLarge(ValueError):
    pass


class _MetadataUnsupported(RuntimeError):
    pass


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item).strip()]


def _normalize_field_catalog(field_data: list, autocomplete_data: dict | None) -> list[dict]:
    fields: list[dict] = []
    aliases: dict[str, list[dict]] = {}

    def index(field: dict) -> None:
        candidates = [field["id"], field["name"], *field["clause_names"]]
        for candidate in candidates:
            aliases.setdefault(candidate.casefold(), []).append(field)

    for raw in field_data:
        if not isinstance(raw, dict):
            continue
        field_id = str(raw.get("id") or raw.get("key") or "").strip()
        name = str(raw.get("name") or field_id).strip()
        if not field_id or not name:
            continue
        clause_names = _string_list(raw.get("clauseNames"))
        if field_id not in clause_names:
            clause_names.append(field_id)
        match = re.fullmatch(r"customfield_(\d+)", field_id)
        if match and f"cf[{match.group(1)}]" not in clause_names:
            clause_names.append(f"cf[{match.group(1)}]")
        jql_clause = f"cf[{match.group(1)}]" if match else clause_names[0]
        schema = raw.get("schema") if isinstance(raw.get("schema"), dict) else {}
        field = {
            "id": field_id,
            "name": name,
            "clause_names": clause_names,
            "jql_clause": jql_clause,
            "custom": bool(raw.get("custom")),
            "searchable": _as_bool(raw.get("searchable")),
            "orderable": _as_bool(raw.get("orderable")),
            "schema_type": str(schema.get("type") or ""),
            "operators": [],
            "types": [],
        }
        fields.append(field)
        index(field)

    visible_fields = (autocomplete_data or {}).get("visibleFieldNames", [])
    if not isinstance(visible_fields, list):
        visible_fields = []
    for raw in visible_fields:
        if not isinstance(raw, dict):
            continue
        value = str(raw.get("value") or "").strip()
        cfid = str(raw.get("cfid") or "").strip()
        display_name = str(raw.get("displayName") or value or cfid).strip()
        candidates = [value, cfid, display_name]
        if " - " in display_name:
            candidates.extend(part.strip() for part in display_name.split(" - ", 1))
        matches = []
        for candidate in candidates:
            matches.extend(aliases.get(candidate.casefold(), []))
        matched = matches[0] if matches else None
        if matched is None:
            field_id = cfid or value
            if not field_id:
                continue
            custom_match = re.fullmatch(r"(?:customfield_|cf\[)(\d+)\]?", cfid)
            jql_clause = f"cf[{custom_match.group(1)}]" if custom_match else value or cfid
            matched = {
                "id": field_id,
                "name": display_name,
                "clause_names": [item for item in (value, cfid) if item],
                "jql_clause": jql_clause,
                "custom": bool(cfid),
                "searchable": _as_bool(raw.get("searchable", True)),
                "orderable": _as_bool(raw.get("orderable")),
                "schema_type": "",
                "operators": [],
                "types": [],
            }
            fields.append(matched)
            index(matched)
        if value and value not in matched["clause_names"]:
            matched["clause_names"].append(value)
        matched["operators"] = _string_list(raw.get("operators"))
        matched["types"] = _string_list(raw.get("types"))
        matched["searchable"] = _as_bool(raw.get("searchable", matched["searchable"]))
        matched["orderable"] = _as_bool(raw.get("orderable", matched["orderable"]))

    return sorted(fields, key=lambda item: (item["name"].casefold(), item["id"].casefold()))


def _jql_literal(value: str, schema_type: str = "") -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\([^\r\n]*\)", value):
        return value
    if schema_type.casefold() in {"integer", "long", "number", "float", "double"} and re.fullmatch(
        r"-?\d+(?:\.\d+)?", value
    ):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class JiraProvider:
    """Jira Server REST API client using session authentication."""

    def __init__(self, config: JiraConfig | None = None) -> None:
        self._fixed_config = config
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()
        self._jql_cache = JsonJqlCache(config or JiraConfig.from_env())

    def _config(self) -> JiraConfig:
        return self._fixed_config or JiraConfig.from_env()

    def _get_client(self) -> httpx.Client:
        if self._client is not None and self._client.is_closed:
            self._client = None
        if self._client is None:
            with self._lock:
                if self._client is not None and self._client.is_closed:
                    self._client = None
                if self._client is None:
                    config = self._config()
                    if not config.is_configured:
                        raise RuntimeError(
                            "JIRA_URL, JIRA_USERNAME, and JIRA_PASSWORD must all be configured"
                        )
                    self._client = httpx.Client(
                        timeout=config.timeout,
                        headers={"Accept": "application/json"},
                        base_url=config.url.rstrip("/") + "/",
                    )
                    try:
                        self._login(config.username, config.password)
                    except Exception:
                        self._client.close()
                        self._client = None
                        raise
        return self._client

    def _login(self, username: str, password: str) -> None:
        """Authenticate via Jira session API and store the session cookie."""
        resp = self._client.post(
            "rest/auth/1/session",
            json={"username": username, "password": password},
        )
        resp.raise_for_status()

    def close(self) -> None:
        """Close the HTTP client."""
        with self._lock:
            if self._client is not None and not self._client.is_closed:
                self._client.close()
            self._client = None

    def _invalidate_client(self) -> None:
        """Close and discard the current client so the next call creates a fresh session."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:  # noqa: BLE001 - invalidation must remain best effort
                LOGGER.debug("Failed to close invalid Jira client: %s", exc)
            self._client = None

    @staticmethod
    def _validate_issue_key(issue_key: str) -> str | None:
        """Return None if valid, else an error message."""
        if not _ISSUE_KEY_RE.fullmatch(issue_key):
            return f"Invalid issue key format: {issue_key!r}"
        return None

    @staticmethod
    def _validate_attachment_id(attachment_id: str) -> str | None:
        """Return None if valid, else an error message."""
        if not attachment_id.isdigit():
            return f"Invalid attachment ID: {attachment_id!r}"
        return None

    def _get_field_map(self) -> dict[str, str]:
        """Fetch custom field ID → name mapping from Jira (always fresh)."""
        try:
            client = self._get_client()
            resp = client.get("rest/api/2/field")
            resp.raise_for_status()
            return {
                f["id"]: f["name"]
                for f in resp.json()
                if f.get("custom", False)
            }
        except Exception as exc:  # noqa: BLE001 - custom fields are optional enrichment
            LOGGER.warning("Failed to fetch Jira field map: %s", exc)
            return {}

    # ── search_issues ──────────────────────────────────────────────

    def search_issues(
        self, jql: str, max_results: int = 50, start_at: int = 0, **kwargs
    ) -> str:
        max_results = min(max(max_results, 1), _MAX_SEARCH_RESULTS)
        start_at = max(start_at, 0)

        fields = "key,summary,status,assignee,issuetype,priority,labels,created,updated"
        try:
            client = self._get_client()
            resp = client.get(
                "rest/api/2/search",
                params={
                    "jql": jql,
                    "fields": fields,
                    "startAt": start_at,
                    "maxResults": max_results,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                return _json_error(
                    "Jira search response must be an object",
                    "invalid_provider_response",
                    jql=jql,
                    results=[],
                    result_count=0,
                )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                self._invalidate_client()
            if status == 400:
                self._jql_cache.invalidate()
            LOGGER.warning("Jira search returned HTTP %d", status)
            code = "invalid_jql" if status == 400 else _status_error_code(status)
            return _json_error(_http_error_message(exc), code, jql=jql, results=[], result_count=0)
        except httpx.RequestError as exc:
            LOGGER.warning("Jira search request failed: %s", exc)
            return _json_error(f"Request failed: {exc}", "upstream_error", jql=jql, results=[], result_count=0)
        except Exception as exc:
            LOGGER.exception("Jira search failed")
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(f"{type(exc).__name__}: {exc}", code, jql=jql, results=[], result_count=0)

        results = []
        for issue in data.get("issues", []):
            f = issue.get("fields", {})
            results.append({
                "key": issue.get("key", ""),
                "summary": f.get("summary", ""),
                "status": (f.get("status") or {}).get("name", ""),
                "assignee": (f.get("assignee") or {}).get("displayName", "Unassigned"),
                "issue_type": (f.get("issuetype") or {}).get("name", ""),
                "priority": (f.get("priority") or {}).get("name", ""),
                "labels": f.get("labels", []),
                "created": f.get("created", ""),
                "updated": f.get("updated", ""),
            })

        return json.dumps(
            {
                "jql": jql,
                "total": data.get("total", 0),
                "start_at": start_at,
                "max_results": max_results,
                "results": results,
                "result_count": len(results),
            },
            ensure_ascii=False,
        )

    # ── JQL metadata ───────────────────────────────────────────────

    def _fetch_jql_fields(self) -> dict:
        client = self._get_client()
        field_response = client.get("rest/api/2/field")
        field_response.raise_for_status()
        field_data = field_response.json()
        if not isinstance(field_data, list):
            raise TypeError("Jira field metadata response must be an array")

        autocomplete_data: dict | None = None
        warning: str | None = None
        try:
            response = client.get("rest/api/2/jql/autocompletedata")
            response.raise_for_status()
            candidate = response.json()
            if not isinstance(candidate, dict):
                raise TypeError("Jira JQL autocomplete response must be an object")
            autocomplete_data = candidate
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise
            warning = "JQL autocomplete metadata is unavailable; field details are limited"
            LOGGER.info("%s (HTTP %d)", warning, exc.response.status_code)
        except (httpx.RequestError, TypeError, ValueError) as exc:
            warning = "JQL autocomplete metadata is unavailable; field details are limited"
            LOGGER.info("%s: %s", warning, exc)

        result = {
            "fields": _normalize_field_catalog(field_data, autocomplete_data),
            "autocomplete_supported": autocomplete_data is not None,
        }
        if warning:
            result["warning"] = warning
        return result

    def _field_catalog(self, *, refresh: bool = False) -> CacheResult:
        if not self._config().is_configured:
            raise RuntimeError("JIRA_URL, JIRA_USERNAME, and JIRA_PASSWORD must all be configured")
        return self._jql_cache.get_fields(self._fetch_jql_fields, refresh=refresh)

    def search_jql_fields(
        self,
        query: str = "",
        max_results: int = 50,
        start_at: int = 0,
        refresh: bool = False,
        **kwargs,
    ) -> str:
        normalized_query = query.strip()
        max_results = min(max(max_results, 1), 200)
        start_at = max(start_at, 0)
        try:
            cached = self._field_catalog(refresh=refresh)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                self._invalidate_client()
            return _json_error(
                _http_error_message(exc),
                _status_error_code(status),
                query=normalized_query,
                fields=[],
                result_count=0,
            )
        except httpx.RequestError as exc:
            return _json_error(
                f"Request failed: {exc}",
                "upstream_error",
                query=normalized_query,
                fields=[],
                result_count=0,
            )
        except Exception as exc:  # noqa: BLE001 - normalize provider failures to MCP errors
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(
                f"{type(exc).__name__}: {exc}",
                code,
                query=normalized_query,
                fields=[],
                result_count=0,
            )

        fields = cached.data.get("fields", [])
        if not isinstance(fields, list):
            return _json_error(
                "Cached Jira field metadata is invalid",
                "invalid_provider_response",
                query=normalized_query,
                fields=[],
                result_count=0,
            )
        needle = normalized_query.casefold()
        if needle:
            fields = [
                field
                for field in fields
                if isinstance(field, dict)
                and needle
                in " ".join(
                    [
                        str(field.get("id", "")),
                        str(field.get("name", "")),
                        *_string_list(field.get("clause_names")),
                    ]
                ).casefold()
            ]
        total = len(fields)
        page = fields[start_at : start_at + max_results]
        next_start_at = start_at + len(page)
        payload = {
            "query": normalized_query,
            "total": total,
            "start_at": start_at,
            "max_results": max_results,
            "fields": page,
            "result_count": len(page),
            "has_more": next_start_at < total,
            "next_start_at": next_start_at if next_start_at < total else None,
            "cache": cached.metadata(),
            "autocomplete_supported": bool(cached.data.get("autocomplete_supported")),
        }
        if cached.data.get("warning"):
            payload["warning"] = cached.data["warning"]
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _resolve_jql_field(fields: list, requested: str) -> tuple[dict | None, list[dict]]:
        needle = requested.casefold()
        exact: dict[str, dict] = {}
        suggestions: dict[str, dict] = {}
        for field in fields:
            if not isinstance(field, dict):
                continue
            candidates = [
                str(field.get("id", "")),
                str(field.get("name", "")),
                *_string_list(field.get("clause_names")),
            ]
            if any(candidate.casefold() == needle for candidate in candidates):
                exact[str(field.get("id", ""))] = field
            elif any(needle in candidate.casefold() for candidate in candidates):
                suggestions[str(field.get("id", ""))] = field
        if len(exact) == 1:
            return next(iter(exact.values())), []
        matches = list(exact.values()) if exact else list(suggestions.values())[:10]
        return None, matches

    @staticmethod
    def _field_clause(field: dict) -> str:
        preferred = str(field.get("jql_clause") or "").strip()
        if preferred:
            return preferred
        field_id = str(field.get("id", ""))
        match = re.fullmatch(r"customfield_(\d+)", field_id)
        if match:
            return f"cf[{match.group(1)}]"
        clauses = _string_list(field.get("clause_names"))
        return clauses[0] if clauses else field_id

    def _fetch_jql_value_suggestions(
        self,
        field_clause: str,
        query: str,
        schema_type: str,
    ) -> dict:
        client = self._get_client()
        response = client.get(
            "rest/api/2/jql/autocompletedata/suggestions",
            params={"fieldName": field_clause, "fieldValue": query},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 405}:
                raise _MetadataUnsupported(
                    "This Jira Server does not provide JQL value suggestions"
                ) from exc
            raise
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise TypeError("Jira JQL value suggestions response is invalid")
        suggestions = []
        seen = set()
        for raw in data["results"]:
            if isinstance(raw, dict):
                value = str(raw.get("value") or "").strip()
                display_name = str(raw.get("displayName") or value).strip()
            else:
                value = str(raw).strip()
                display_name = value
            if not value or value in seen:
                continue
            seen.add(value)
            suggestions.append(
                {
                    "value": value,
                    "display_name": display_name,
                    "jql_literal": _jql_literal(value, schema_type),
                }
            )
            if len(suggestions) >= _MAX_CACHED_VALUE_SUGGESTIONS:
                break
        return {"suggestions": suggestions}

    def get_jql_value_suggestions(
        self,
        field: str,
        query: str = "",
        max_results: int = 50,
        refresh: bool = False,
        **kwargs,
    ) -> str:
        requested = field.strip()
        normalized_query = query.strip()
        max_results = min(max(max_results, 1), 200)
        try:
            field_cache = self._field_catalog(refresh=refresh)
            fields = field_cache.data.get("fields", [])
            if not isinstance(fields, list):
                raise TypeError("Cached Jira field metadata is invalid")
            resolved, matches = self._resolve_jql_field(fields, requested)
            if resolved is None:
                code = "ambiguous_jql_field" if len(matches) > 1 else "unknown_jql_field"
                return _json_error(
                    f"JQL field {requested!r} is not uniquely identifiable",
                    code,
                    field=requested,
                    matches=[
                        {
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "clause_names": item.get("clause_names", []),
                        }
                        for item in matches
                    ],
                    suggestions=[],
                    result_count=0,
                    cache=field_cache.metadata(),
                )
            field_clause = self._field_clause(resolved)
            schema_type = str(resolved.get("schema_type") or "")
            cached = self._jql_cache.get_values(
                field_clause,
                normalized_query,
                lambda: self._fetch_jql_value_suggestions(
                    field_clause,
                    normalized_query,
                    schema_type,
                ),
                refresh=refresh,
            )
        except _MetadataUnsupported as exc:
            return _json_error(
                str(exc),
                "metadata_unsupported",
                field=requested,
                suggestions=[],
                result_count=0,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                self._invalidate_client()
            return _json_error(
                _http_error_message(exc),
                _status_error_code(status),
                field=requested,
                suggestions=[],
                result_count=0,
            )
        except httpx.RequestError as exc:
            return _json_error(
                f"Request failed: {exc}",
                "upstream_error",
                field=requested,
                suggestions=[],
                result_count=0,
            )
        except Exception as exc:  # noqa: BLE001 - normalize provider failures to MCP errors
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(
                f"{type(exc).__name__}: {exc}",
                code,
                field=requested,
                suggestions=[],
                result_count=0,
            )

        suggestions = cached.data.get("suggestions", [])
        if not isinstance(suggestions, list):
            return _json_error(
                "Cached Jira value suggestions are invalid",
                "invalid_provider_response",
                field=requested,
                suggestions=[],
                result_count=0,
            )
        page = suggestions[:max_results]
        return json.dumps(
            {
                "field": {
                    "id": resolved.get("id"),
                    "name": resolved.get("name"),
                    "jql_clause": field_clause,
                },
                "query": normalized_query,
                "suggestions": page,
                "result_count": len(page),
                "truncated": len(suggestions) > len(page),
                "cache": cached.metadata(),
            },
            ensure_ascii=False,
        )

    # ── get_issue ──────────────────────────────────────────────────

    def get_issue_json(self, issue_key: str, **kwargs) -> dict:
        """Get issue details as structured JSON.

        Args:
            issue_key: Jira issue key (e.g. 'PROJ-123').

        Returns:
            Dictionary with issue details including attachments.
        """
        err = self._validate_issue_key(issue_key)
        if err:
            return {"error": err, "error_code": "invalid_issue_key"}

        field_map = self._get_field_map()
        custom_fields = ",".join(field_map.keys())
        fields_param = _DEFAULT_FIELDS
        if custom_fields:
            fields_param += "," + custom_fields

        try:
            client = self._get_client()
            resp = client.get(f"rest/api/2/issue/{issue_key}", params={"fields": fields_param})
            resp.raise_for_status()
            issue = resp.json()
            if not isinstance(issue, dict):
                return {"error": "Jira issue response must be an object", "error_code": "invalid_provider_response"}
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                self._invalidate_client()
            LOGGER.warning("Jira issue lookup returned HTTP %d", status)
            return {"error": _http_error_message(exc), "error_code": _status_error_code(status)}
        except httpx.RequestError as exc:
            LOGGER.warning("Jira issue request failed: %s", exc)
            return {"error": f"Request failed: {exc}", "error_code": "upstream_error"}
        except Exception as exc:
            LOGGER.exception("Jira structured issue lookup failed")
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return {"error": f"{type(exc).__name__}: {exc}", "error_code": code}

        f = issue.get("fields", {})

        def _user_name(user_obj: dict | None) -> str:
            if not user_obj:
                return "Unassigned"
            return user_obj.get("displayName", user_obj.get("name", ""))

        def _name(val: dict | None) -> str:
            return (val or {}).get("name", "")

        # Extract subtasks
        subtasks = []
        for st in f.get("subtasks", []):
            sf = st.get("fields", {})
            subtasks.append({
                "key": st.get("key", ""),
                "summary": sf.get("summary", ""),
                "status": (sf.get("status") or {}).get("name", ""),
            })

        # Extract issue links
        issue_links = []
        for link in f.get("issuelinks", []):
            lt = link.get("type", {})
            if "outwardIssue" in link:
                linked = link["outwardIssue"]
                lf = linked.get("fields") or {}
                issue_links.append({
                    "direction": lt.get("outward", lt.get("name", "")),
                    "issue": {
                        "key": linked.get("key", ""),
                        "summary": lf.get("summary", ""),
                        "status": (lf.get("status") or {}).get("name", ""),
                    },
                })
            elif "inwardIssue" in link:
                linked = link["inwardIssue"]
                lf = linked.get("fields") or {}
                issue_links.append({
                    "direction": lt.get("inward", lt.get("name", "")),
                    "issue": {
                        "key": linked.get("key", ""),
                        "summary": lf.get("summary", ""),
                        "status": (lf.get("status") or {}).get("name", ""),
                    },
                })

        # Extract attachments
        attachments = []
        for att in f.get("attachment", []):
            attachments.append({
                "id": att.get("id", ""),
                "filename": att.get("filename", ""),
                "size": att.get("size", 0),
                "mime_type": att.get("mimeType", ""),
                "author": _user_name(att.get("author")),
                "created": att.get("created", ""),
                "content_url": att.get("content", ""),
            })

        return {
            "key": issue.get("key", ""),
            "summary": f.get("summary", ""),
            "issue_type": _name(f.get("issuetype")),
            "status": _name(f.get("status")),
            "priority": _name(f.get("priority")),
            "resolution": _name(f.get("resolution")) or "Unresolved",
            "assignee": _user_name(f.get("assignee")),
            "reporter": _user_name(f.get("reporter")),
            "created": f.get("created", ""),
            "updated": f.get("updated", ""),
            "description": f.get("description", ""),
            "subtasks": subtasks,
            "issue_links": issue_links,
            "attachments": attachments,
        }

    def get_issue(self, issue_key: str, **kwargs) -> str:
        err = self._validate_issue_key(issue_key)
        if err:
            return _json_error(err, "invalid_issue_key", issue_key=issue_key)

        field_map = self._get_field_map()
        custom_fields = ",".join(field_map.keys())
        fields_param = _DEFAULT_FIELDS
        if custom_fields:
            fields_param += "," + custom_fields

        try:
            client = self._get_client()
            resp = client.get(f"rest/api/2/issue/{issue_key}", params={"fields": fields_param})
            resp.raise_for_status()
            issue = resp.json()
            if not isinstance(issue, dict):
                return _json_error(
                    "Jira issue response must be an object",
                    "invalid_provider_response",
                    issue_key=issue_key,
                )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                self._invalidate_client()
            LOGGER.warning("Jira issue lookup returned HTTP %d", status)
            return _json_error(_http_error_message(exc), _status_error_code(status), issue_key=issue_key)
        except httpx.RequestError as exc:
            LOGGER.warning("Jira issue request failed: %s", exc)
            return _json_error(f"Request failed: {exc}", "upstream_error", issue_key=issue_key)
        except Exception as exc:
            LOGGER.exception("Jira issue lookup failed")
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(f"{type(exc).__name__}: {exc}", code, issue_key=issue_key)

        f = issue.get("fields", {})
        key = issue.get("key", "")
        summary = f.get("summary", "")

        def _user_name(user_obj: dict | None) -> str:
            if not user_obj:
                return "未分配"
            return user_obj.get("displayName", user_obj.get("name", ""))

        def _name(val: dict | None) -> str:
            return (val or {}).get("name", "")

        def _render_custom_field(fid: str) -> str | None:
            val = f.get(fid)
            if val is None:
                return None
            if isinstance(val, dict):
                rendered = val.get("displayName") or val.get("value") or str(val)
            elif isinstance(val, list):
                items = [
                    item.get("value") or item.get("name") if isinstance(item, dict) else str(item)
                    for item in val
                ]
                rendered = ", ".join(str(item) for item in items)
            else:
                rendered = str(val)
            text = str(rendered).strip()
            if not text or text == "None":
                return None
            if len(text) > _MAX_FIELD_LENGTH:
                return text[:_MAX_FIELD_LENGTH] + "..."
            return text

        lines = [f"# {key} - {summary}", ""]

        # ── 问题详情 ──
        detail_rows = [
            ("类型", _name(f.get("issuetype"))),
            ("状态", _name(f.get("status"))),
            ("优先级", _name(f.get("priority"))),
            ("解决结果", _name(f.get("resolution")) or "未解决"),
        ]
        if f.get("versions"):
            detail_rows.append(("影响版本", ", ".join(_name(v) for v in f["versions"])))
        if f.get("fixVersions"):
            detail_rows.append(("修复版本", ", ".join(_name(v) for v in f["fixVersions"])))
        if f.get("components"):
            detail_rows.append(("组件", ", ".join(_name(c) for c in f["components"])))
        if f.get("labels"):
            detail_rows.append(("标签", ", ".join(f["labels"])))

        lines += ["**问题详情**", ""]
        lines += ["| 字段 | 值 |", "|---|---|"]
        for label, value in detail_rows:
            lines.append(f"| {label} | {value} |")

        # ── 用户 ──
        user_rows = [("经办人", _user_name(f.get("assignee")))]
        if f.get("reporter"):
            user_rows.append(("报告人", _user_name(f.get("reporter"))))
        for fid in ("customfield_10400", "customfield_10204", "customfield_10401"):
            label = field_map.get(fid)
            if label:
                val = _render_custom_field(fid)
                if val:
                    user_rows.append((label, val))

        lines += ["", "**用户**", ""]
        lines += ["| 字段 | 值 |", "|---|---|"]
        for label, value in user_rows:
            lines.append(f"| {label} | {value} |")

        # ── 日期 ──
        lines += ["", "**日期**", ""]
        lines += ["| 字段 | 值 |", "|---|---|"]
        if f.get("created"):
            lines.append(f"| 创建时间 | {f['created']} |")
        if f.get("updated"):
            lines.append(f"| 更新时间 | {f['updated']} |")

        # ── parent ──
        parent = f.get("parent")
        if parent:
            lines += ["", "**父任务：**", f"- {parent.get('key', '')} {(parent.get('fields') or {}).get('summary', '')}"]

        # ── description ──
        description = (f.get("description") or "").strip()
        if description:
            lines += ["", "**描述：**", ""]
            if len(description) > _MAX_FIELD_LENGTH:
                description = description[:_MAX_FIELD_LENGTH] + "..."
            lines.append(description)

        # ── 测试说明 ──
        test_desc = _render_custom_field("customfield_11122")
        if test_desc:
            lines += ["", "**测试说明：**", "", test_desc]

        # ── 研发设计说明 ──
        dev_desc = _render_custom_field("customfield_11145")
        if dev_desc:
            lines += ["", "**研发设计说明：**", "", dev_desc]

        # ── issue links ──
        links = f.get("issuelinks", [])
        if links:
            lines += ["", "**关联任务：**", ""]
            for link in links:
                lt = link.get("type", {})
                if "outwardIssue" in link:
                    linked = link["outwardIssue"]
                    lf = linked.get("fields") or {}
                    rel = lt.get("outward", lt.get("name", ""))
                    lines.append(
                        f"- {rel} {linked.get('key', '')} "
                        f"{lf.get('summary', '')} "
                        f"({(lf.get('status') or {}).get('name', '')})"
                    )
                elif "inwardIssue" in link:
                    linked = link["inwardIssue"]
                    lf = linked.get("fields") or {}
                    rel = lt.get("inward", lt.get("name", ""))
                    lines.append(
                        f"- {rel} {linked.get('key', '')} "
                        f"{lf.get('summary', '')} "
                        f"({(lf.get('status') or {}).get('name', '')})"
                    )

        # ── subtasks ──
        subtasks = f.get("subtasks", [])
        if subtasks:
            lines += ["", "**子任务：**", ""]
            for st in subtasks:
                sf = st.get("fields", {})
                lines.append(
                    f"- {st.get('key', '')} {sf.get('summary', '')} "
                    f"({(sf.get('status') or {}).get('name', '')})"
                )

        # ── attachments ──
        attachments = f.get("attachment", [])
        if attachments:
            lines += ["", "**附件：**", ""]
            for att in attachments:
                size_mb = att.get("size", 0) / 1024 / 1024
                author = (att.get("author") or {}).get("displayName", "")
                att_id = att.get("id", "")
                lines.append(
                    f"- {att.get('filename', '')} ({size_mb:.1f} MB) "
                    f"by {author} - {att.get('created', '')} "
                    f"[ID: {att_id}]"
                )

        # ── 其他信息 (remaining custom fields) ──
        skip_fields = {
            "customfield_10204", "customfield_10400", "customfield_10401",  # 用户区
            "customfield_11122", "customfield_11145",                       # 测试/设计说明
        }
        other_rows = []
        for fid, fname in sorted(field_map.items()):
            if fid in skip_fields:
                continue
            val = _render_custom_field(fid)
            if val:
                other_rows.append((fname, val))
        if other_rows:
            lines += ["", "**其他信息**", ""]
            lines += ["| 字段 | 值 |", "|---|---|"]
            for label, value in other_rows:
                lines.append(f"| {label} | {value} |")

        return "\n".join(lines)

    # ── get_comments ───────────────────────────────────────────────

    def get_comments(
        self, issue_key: str, max_results: int = 50, start_at: int = 0, **kwargs
    ) -> str:
        err = self._validate_issue_key(issue_key)
        if err:
            return _json_error(err, "invalid_issue_key", issue_key=issue_key, comments=[], comment_count=0)
        max_results = min(max(max_results, 1), 100)
        start_at = max(start_at, 0)

        try:
            client = self._get_client()
            resp = client.get(
                f"rest/api/2/issue/{issue_key}/comment",
                params={"maxResults": max_results, "startAt": start_at},
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                return _json_error(
                    "Jira comments response must be an object",
                    "invalid_provider_response",
                    issue_key=issue_key,
                    comments=[],
                    comment_count=0,
                )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                self._invalidate_client()
            LOGGER.warning("Jira comments returned HTTP %d", status)
            return _json_error(
                _http_error_message(exc),
                _status_error_code(status),
                issue_key=issue_key,
                comments=[],
                comment_count=0,
            )
        except httpx.RequestError as exc:
            LOGGER.warning("Jira comments request failed: %s", exc)
            return _json_error(
                f"Request failed: {exc}",
                "upstream_error",
                issue_key=issue_key,
                comments=[],
                comment_count=0,
            )
        except Exception as exc:
            LOGGER.exception("Jira comments lookup failed")
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(
                f"{type(exc).__name__}: {exc}",
                code,
                issue_key=issue_key,
                comments=[],
                comment_count=0,
            )

        comments = []
        for c in data.get("comments", []):
            body = c.get("body", "")
            if len(body) > _MAX_FIELD_LENGTH:
                body = body[:_MAX_FIELD_LENGTH] + "..."
            comments.append({
                "author": (c.get("author") or {}).get("displayName", ""),
                "body": body,
                "created": c.get("created", ""),
                "updated": c.get("updated", ""),
            })

        return json.dumps(
            {
                "issue_key": issue_key,
                "total": data.get("total", 0),
                "start_at": start_at,
                "max_results": max_results,
                "comments": comments,
                "comment_count": len(comments),
            },
            ensure_ascii=False,
        )

    # ── get_attachment ─────────────────────────────────────────────

    def get_attachment(self, attachment_id: str, save_to: str, **kwargs) -> str:
        err = self._validate_attachment_id(attachment_id)
        if err:
            return _json_error(err, "invalid_attachment_id", id=attachment_id)

        config = self._config()
        try:
            save_path = resolve_output_path(save_to, config)
        except OutputPathError as exc:
            return _json_error(str(exc), "invalid_output_path", id=attachment_id)

        try:
            client = self._get_client()
            meta_resp = client.get(f"rest/api/2/attachment/{attachment_id}")
            meta_resp.raise_for_status()
            meta = meta_resp.json()
            if not isinstance(meta, dict):
                return _json_error(
                    "Jira returned invalid attachment metadata",
                    "invalid_provider_response",
                    id=attachment_id,
                )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                self._invalidate_client()
            LOGGER.warning("Jira attachment metadata returned HTTP %d", status)
            return _json_error(_http_error_message(exc), _status_error_code(status), id=attachment_id)
        except httpx.RequestError as exc:
            LOGGER.warning("Jira attachment metadata request failed: %s", exc)
            return _json_error(f"Request failed: {exc}", "upstream_error", id=attachment_id)
        except Exception as exc:
            LOGGER.exception("Jira attachment metadata lookup failed")
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(f"{type(exc).__name__}: {exc}", code, id=attachment_id)

        filename = meta.get("filename", "")
        try:
            size = int(meta.get("size", 0))
        except (TypeError, ValueError):
            size = 0
        mime_type = meta.get("mimeType", "")
        content_url = meta.get("content", "")

        if not isinstance(content_url, str) or not content_url:
            return _json_error(
                f"Attachment {attachment_id} has no download URL",
                "invalid_provider_response",
                id=attachment_id,
                filename=filename,
            )
        if size > config.max_attachment_size:
            return _json_error(
                f"Attachment size ({size} bytes) exceeds limit ({config.max_attachment_size} bytes)",
                "attachment_too_large",
                id=attachment_id,
                filename=filename,
                size=size,
                mime_type=mime_type,
            )

        download_url = urljoin(config.url.rstrip("/") + "/", content_url)
        jira_origin = urlparse(config.url)
        download_origin = urlparse(download_url)
        jira_port = jira_origin.port or (443 if jira_origin.scheme == "https" else 80)
        download_port = download_origin.port or (443 if download_origin.scheme == "https" else 80)
        if (
            download_origin.scheme != jira_origin.scheme
            or download_origin.hostname != jira_origin.hostname
            or download_port != jira_port
        ):
            return _json_error(
                "Attachment download URL is outside the configured Jira origin",
                "invalid_attachment_url",
                id=attachment_id,
                filename=filename,
            )

        save_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".archon-jira-",
                suffix=".part",
                dir=save_path.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            written = 0
            with client.stream("GET", download_url) as download_resp:
                download_resp.raise_for_status()
                with temporary_path.open("wb") as output:
                    for chunk in download_resp.iter_bytes(chunk_size=8192):
                        written += len(chunk)
                        if written > config.max_attachment_size:
                            raise _AttachmentTooLarge(
                                f"Downloaded content exceeds limit ({config.max_attachment_size} bytes)"
                            )
                        output.write(chunk)
            commit_output_file(temporary_path, save_path, config)
            temporary_path = None
        except _AttachmentTooLarge as exc:
            return _json_error(str(exc), "attachment_too_large", id=attachment_id, filename=filename)
        except OutputPathError as exc:
            return _json_error(str(exc), "invalid_output_path", id=attachment_id, filename=filename)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                self._invalidate_client()
            LOGGER.warning("Jira attachment download returned HTTP %d", status)
            return _json_error(_http_error_message(exc), _status_error_code(status), id=attachment_id)
        except httpx.RequestError as exc:
            LOGGER.warning("Jira attachment download failed: %s", exc)
            return _json_error(f"Download failed: {exc}", "upstream_error", id=attachment_id)
        except Exception as exc:
            LOGGER.exception("Jira attachment download failed")
            return _json_error(f"{type(exc).__name__}: {exc}", "download_failed", id=attachment_id)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        return json.dumps(
            {
                "id": attachment_id,
                "filename": filename,
                "saved_to": str(save_path),
                "size": save_path.stat().st_size,
                "mime_type": mime_type,
            },
            ensure_ascii=False,
        )
