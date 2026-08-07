"""Jira REST API provider implementation."""

import base64
import json
import logging
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from server.config import DEFAULT_MAX_ATTACHMENT_SIZE, JiraConfig
from server.files import OutputPathError, commit_output_file, resolve_output_path
from server.jql_cache import CacheResult, JsonJqlCache

_MAX_FIELD_LENGTH = 2000
_MAX_OTHER_FIELD_ROWS = 60
_MAX_OTHER_FIELD_LENGTH = 500
# Per-section item caps plus one overall budget for the rendered issue. The
# per-section caps keep every section represented; the budget is the backstop
# for pathological instances where many sections are individually near their cap.
_MAX_LIST_ITEMS = 50
_MAX_INLINE_VALUES = 30
_MAX_ISSUE_CHARS = 60_000
_MAX_SEARCH_RESULTS = 200
_MAX_CACHED_VALUE_SUGGESTIONS = 1000
_DEFAULT_MAX_ATTACHMENT_SIZE = DEFAULT_MAX_ATTACHMENT_SIZE
_SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")
_COMMENT_ID_RE = re.compile(r"^\d+$")
_MAX_COMMENT_LENGTH = 32767
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


def _object_text(value: object, key: str, default: str = "") -> str:
    if not isinstance(value, dict):
        return default
    candidate = value.get(key)
    return str(candidate) if candidate is not None else default


def _table_cell(value: object) -> str:
    """Render untrusted Jira text as one Markdown table cell.

    A raw ``|`` or newline in a Jira value would otherwise split the cell and
    let issue content forge extra columns or rows in the rendered table.
    """

    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _bounded(items: list, limit: int) -> tuple[list, int]:
    """Return at most ``limit`` items plus how many were left out."""

    return items[:limit], max(len(items) - limit, 0)


def _inline_values(values: object, render: Callable[[object], str]) -> str:
    """Join a Jira multi-value field, capping how many values are listed."""

    items = values if isinstance(values, list) else []
    shown, omitted = _bounded(items, _MAX_INLINE_VALUES)
    text = ", ".join(render(item) for item in shown)
    return f"{text} (+{omitted})" if omitted else text


def _render_within_budget(lines: list[str], limit: int) -> str:
    """Join ``lines`` into at most ``limit`` characters.

    The truncation notice is counted against the budget rather than appended
    after it, so the returned string is never longer than ``limit``.
    """

    notice = f"（内容超出 {limit} 字符上限，已截断）"
    rendered = "\n".join(lines)
    if len(rendered) <= limit:
        return rendered

    # Reserve room for the blank separator line and the notice itself.
    budget = max(limit - len(notice) - 2, 0)
    total = 0
    kept: list[str] = []
    for line in lines:
        remaining = budget - total
        if len(line) + 1 > remaining:
            # Keep the head of the overflowing line rather than dropping it, so a
            # single long section still contributes what fits.
            if remaining > 0:
                kept.append(line[:remaining])
            break
        kept.append(line)
        total += len(line) + 1
    return "\n".join([*kept, "", notice])[:limit]


def _normalize_comment(comment: dict, fallback_body: str = "") -> dict:
    """Return a bounded, consistent comment representation."""
    body = str(comment.get("body") or fallback_body)
    if len(body) > _MAX_FIELD_LENGTH:
        body = body[:_MAX_FIELD_LENGTH] + "..."
    return {
        "id": str(comment.get("id") or ""),
        "author": _object_text(comment.get("author"), "displayName"),
        "body": body,
        "created": comment.get("created", ""),
        "updated": comment.get("updated", ""),
    }


def _normalize_transition(raw: dict) -> dict:
    """Return bounded transition metadata suitable for MCP output."""
    destination = raw.get("to") if isinstance(raw.get("to"), dict) else {}
    raw_fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    fields = []
    for field_id, metadata in raw_fields.items():
        if not isinstance(metadata, dict):
            continue
        schema = metadata.get("schema") if isinstance(metadata.get("schema"), dict) else {}
        allowed_values = []
        raw_allowed_values = metadata.get("allowedValues")
        if isinstance(raw_allowed_values, list):
            for value in raw_allowed_values[:50]:
                if isinstance(value, dict):
                    value_id = value.get("id")
                    value_name = value.get("name") or value.get("value")
                    allowed_values.append({
                        "id": str(value_id) if value_id is not None else "",
                        "name": str(value_name) if value_name is not None else "",
                    })
                elif value is not None:
                    allowed_values.append({"id": "", "name": str(value)})
        fields.append({
            "id": str(field_id),
            "name": str(metadata.get("name") or field_id),
            "required": bool(metadata.get("required")),
            "schema_type": str(schema.get("type") or ""),
            "operations": _string_list(metadata.get("operations")),
            "allowed_values": allowed_values,
        })
    return {
        "id": str(raw.get("id") or ""),
        "name": str(raw.get("name") or ""),
        "to": {
            "id": str(destination.get("id") or ""),
            "name": str(destination.get("name") or ""),
        },
        "fields": fields,
    }


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
        self._field_map_lock = threading.Lock()
        self._field_map: dict[str, str] | None = None
        self._field_map_fetched_at = 0.0
        self._jql_cache = JsonJqlCache(config or JiraConfig.from_env())

    def _config(self) -> JiraConfig:
        return self._fixed_config or JiraConfig.from_env()

    def _get_client(self) -> httpx.Client:
        client = self._client
        if client is not None and not client.is_closed:
            return client
        with self._lock:
            client = self._client
            if client is not None and not client.is_closed:
                return client
            self._client = None
            config = self._config()
            if not config.is_configured:
                raise RuntimeError(
                    "JIRA_URL, JIRA_USERNAME, and JIRA_PASSWORD must all be configured"
                )
            # Authenticate before publishing the client. Publishing first would
            # let another thread take the fast path above and issue a request on
            # a connection that has no session cookie yet.
            client = httpx.Client(
                timeout=config.timeout,
                headers={"Accept": "application/json"},
                base_url=config.url.rstrip("/") + "/",
            )
            try:
                self._login(client, config.username, config.password)
            except Exception:
                client.close()
                raise
            self._client = client
            return client

    @staticmethod
    def _login(client: httpx.Client, username: str, password: str) -> None:
        """Authenticate via Jira session API and store the session cookie."""
        resp = client.post(
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

    def _invalidate_client(self, expected: httpx.Client | None = None) -> None:
        """Close and discard the current client so the next call creates a fresh session.

        When ``expected`` is given, only that client is discarded. This keeps a
        stale 401 from throwing away a session another thread just established.
        """

        with self._lock:
            if self._client is None or (expected is not None and self._client is not expected):
                return
            stale = self._client
            self._client = None
        try:
            stale.close()
        except Exception as exc:  # noqa: BLE001 - invalidation must remain best effort
            LOGGER.debug("Failed to close invalid Jira client: %s", exc)

    def _may_retry_after_401(self, method: str) -> bool:
        """Decide whether a 401 on ``method`` may be retried after re-authenticating.

        Jira sessions expire on the server's schedule, so the first call after an
        idle period can fail even though the credentials are still valid. Retrying
        a read is always safe.

        Writes are not retried by default. A 401 from Jira itself means the
        request was rejected before it was applied, but a reverse proxy or SSO
        gateway can rewrite the response *after* forwarding the request, which
        would turn a retry into a duplicate comment or a second transition.
        Deployments that have verified their edge can opt in with
        ``JIRA_RETRY_MUTATIONS_ON_401``.
        """

        if method.upper() in _SAFE_HTTP_METHODS:
            return True
        return self._config().retry_mutations_on_401

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Send one authenticated Jira request, re-authenticating once on 401."""

        last_attempt = 1
        for attempt in (0, last_attempt):
            client = self._get_client()
            response = getattr(client, method.lower())(url, **kwargs)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 401:
                    raise
                # Drop exactly this session so the retry, and any later call,
                # authenticates again instead of reusing a rejected cookie.
                self._invalidate_client(client)
                if attempt == last_attempt or not self._may_retry_after_401(method):
                    raise
                LOGGER.info("Jira session rejected %s %s; re-authenticating once", method, url)
                continue
            return response
        raise AssertionError("unreachable")  # pragma: no cover - loop always returns or raises

    @contextmanager
    def _stream(self, method: str, url: str, **kwargs) -> Iterator[httpx.Response]:
        """Stream one authenticated Jira response, re-authenticating once on 401.

        Downloads go through the same session recovery as _request; without it an
        expired session would surface an error that the very next call succeeds at.
        """

        last_attempt = 1
        for attempt in (0, last_attempt):
            client = self._get_client()
            with client.stream(method, url, **kwargs) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    # A streamed error response has no buffered body yet, and
                    # _http_error_message needs one to extract Jira's message.
                    exc.response.read()
                    if exc.response.status_code != 401:
                        raise
                    self._invalidate_client(client)
                    if attempt == last_attempt or not self._may_retry_after_401(method):
                        raise
                    LOGGER.info(
                        "Jira session rejected stream %s %s; re-authenticating once", method, url
                    )
                    continue
                yield response
                return
        raise AssertionError("unreachable")  # pragma: no cover - loop always returns or raises

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

    @staticmethod
    def _validate_comment_id(comment_id: str) -> str | None:
        """Return None if valid, else an error message."""
        if not _COMMENT_ID_RE.fullmatch(comment_id):
            return f"Invalid comment ID: {comment_id!r}"
        return None

    def _get_field_map(self) -> dict[str, str]:
        """Return a short-lived custom field map used to enrich issue details."""

        config = self._config()
        with self._field_map_lock:
            now = time.monotonic()
            if (
                self._field_map is not None
                and now - self._field_map_fetched_at < config.jql_field_refresh_interval
            ):
                return self._field_map.copy()

            try:
                data = self._request("GET", "rest/api/2/field").json()
                if not isinstance(data, list):
                    raise TypeError("Jira field metadata response must be an array")
                field_map = {
                    str(field["id"]): str(field["name"])
                    for field in data
                    if isinstance(field, dict)
                    and field.get("custom", False)
                    and field.get("id")
                    and field.get("name")
                }
                self._field_map = field_map
                self._field_map_fetched_at = now
                return field_map.copy()
            except Exception as exc:  # noqa: BLE001 - custom fields are optional enrichment
                LOGGER.warning("Failed to fetch Jira field map: %s", exc)
                return self._field_map.copy() if self._field_map is not None else {}

    # ── search_issues ──────────────────────────────────────────────

    def search_issues(
        self, jql: str, max_results: int = 50, start_at: int = 0, **kwargs
    ) -> str:
        max_results = min(max(max_results, 1), _MAX_SEARCH_RESULTS)
        start_at = max(start_at, 0)

        fields = "key,summary,status,assignee,issuetype,priority,labels,created,updated"
        try:
            data = self._request(
                "GET",
                "rest/api/2/search",
                params={
                    "jql": jql,
                    "fields": fields,
                    "startAt": start_at,
                    "maxResults": max_results,
                },
            ).json()
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

        issues = data.get("issues", [])
        total = data.get("total", 0)
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            return _json_error(
                "Jira search response contains an invalid total",
                "invalid_provider_response",
                jql=jql,
                results=[],
                result_count=0,
            )
        if not isinstance(issues, list) or any(not isinstance(issue, dict) for issue in issues):
            return _json_error(
                "Jira search response contains an invalid issues list",
                "invalid_provider_response",
                jql=jql,
                results=[],
                result_count=0,
            )

        results = []
        for issue in issues:
            f = issue.get("fields", {})
            if not isinstance(f, dict):
                return _json_error(
                    "Jira search response contains invalid issue fields",
                    "invalid_provider_response",
                    jql=jql,
                    results=[],
                    result_count=0,
                )
            results.append({
                "key": issue.get("key", ""),
                "summary": f.get("summary", ""),
                "status": _object_text(f.get("status"), "name"),
                "assignee": _object_text(f.get("assignee"), "displayName", "Unassigned"),
                "issue_type": _object_text(f.get("issuetype"), "name"),
                "priority": _object_text(f.get("priority"), "name"),
                "labels": _string_list(f.get("labels")),
                "created": f.get("created", ""),
                "updated": f.get("updated", ""),
            })

        return json.dumps(
            {
                "jql": jql,
                "total": total,
                "start_at": start_at,
                "max_results": max_results,
                "results": results,
                "result_count": len(results),
                "has_more": start_at + len(results) < total,
                "next_start_at": start_at + len(results)
                if start_at + len(results) < total
                else None,
            },
            ensure_ascii=False,
        )

    # ── JQL metadata ───────────────────────────────────────────────

    def _fetch_jql_fields(self) -> dict:
        field_data = self._request("GET", "rest/api/2/field").json()
        if not isinstance(field_data, list):
            raise TypeError("Jira field metadata response must be an array")

        autocomplete_data: dict | None = None
        warning: str | None = None
        try:
            candidate = self._request("GET", "rest/api/2/jql/autocompletedata").json()
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
        try:
            response = self._request(
                "GET",
                "rest/api/2/jql/autocompletedata/suggestions",
                params={"fieldName": field_clause, "fieldValue": query},
            )
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

        # This projection renders only standard fields, so it deliberately skips
        # the custom-field map that get_issue needs. Requesting every custom
        # field here would add a metadata round trip and inflate the Jira
        # response with values that are then discarded.
        try:
            issue = self._request(
                "GET", f"rest/api/2/issue/{issue_key}", params={"fields": _DEFAULT_FIELDS}
            ).json()
            if not isinstance(issue, dict) or not isinstance(issue.get("fields", {}), dict):
                return {"error": "Jira issue response must be an object", "error_code": "invalid_provider_response"}
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
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
            return _object_text(user_obj, "displayName") or _object_text(
                user_obj, "name", "Unassigned"
            )

        def _name(val: dict | None) -> str:
            return _object_text(val, "name")

        # Extract subtasks
        subtasks = []
        for st in f.get("subtasks", []) if isinstance(f.get("subtasks", []), list) else []:
            if not isinstance(st, dict):
                continue
            sf = st.get("fields", {}) if isinstance(st.get("fields", {}), dict) else {}
            subtasks.append({
                "key": st.get("key", ""),
                "summary": sf.get("summary", ""),
                "status": _object_text(sf.get("status"), "name"),
            })

        # Extract issue links
        issue_links = []
        for link in f.get("issuelinks", []) if isinstance(f.get("issuelinks", []), list) else []:
            if not isinstance(link, dict):
                continue
            lt = link.get("type", {}) if isinstance(link.get("type", {}), dict) else {}
            if "outwardIssue" in link:
                linked = link["outwardIssue"]
                if not isinstance(linked, dict):
                    continue
                lf = linked.get("fields") or {}
                if not isinstance(lf, dict):
                    lf = {}
                issue_links.append({
                    "direction": lt.get("outward", lt.get("name", "")),
                    "issue": {
                        "key": linked.get("key", ""),
                        "summary": lf.get("summary", ""),
                        "status": _object_text(lf.get("status"), "name"),
                    },
                })
            elif "inwardIssue" in link:
                linked = link["inwardIssue"]
                if not isinstance(linked, dict):
                    continue
                lf = linked.get("fields") or {}
                if not isinstance(lf, dict):
                    lf = {}
                issue_links.append({
                    "direction": lt.get("inward", lt.get("name", "")),
                    "issue": {
                        "key": linked.get("key", ""),
                        "summary": lf.get("summary", ""),
                        "status": _object_text(lf.get("status"), "name"),
                    },
                })

        # Extract attachments
        attachments = []
        for att in f.get("attachment", []) if isinstance(f.get("attachment", []), list) else []:
            if not isinstance(att, dict):
                continue
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
            issue = self._request(
                "GET", f"rest/api/2/issue/{issue_key}", params={"fields": fields_param}
            ).json()
            if not isinstance(issue, dict) or not isinstance(issue.get("fields", {}), dict):
                return _json_error(
                    "Jira issue response must be an object with fields",
                    "invalid_provider_response",
                    issue_key=issue_key,
                )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
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
        summary = str(f.get("summary", ""))
        if len(summary) > _MAX_FIELD_LENGTH:
            summary = summary[:_MAX_FIELD_LENGTH] + "..."

        def _user_name(user_obj: dict | None) -> str:
            return _object_text(user_obj, "displayName") or _object_text(
                user_obj, "name", "未分配"
            )

        def _name(val: dict | None) -> str:
            return _object_text(val, "name")

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
            detail_rows.append(("影响版本", _inline_values(f["versions"], _name)))
        if f.get("fixVersions"):
            detail_rows.append(("修复版本", _inline_values(f["fixVersions"], _name)))
        if f.get("components"):
            detail_rows.append(("组件", _inline_values(f["components"], _name)))
        if f.get("labels"):
            labels, omitted_labels = _bounded(_string_list(f["labels"]), _MAX_INLINE_VALUES)
            detail_rows.append(
                ("标签", ", ".join(labels) + (f" (+{omitted_labels})" if omitted_labels else ""))
            )

        lines += ["**问题详情**", ""]
        lines += ["| 字段 | 值 |", "|---|---|"]
        for label, value in detail_rows:
            lines.append(f"| {_table_cell(label)} | {_table_cell(value)} |")

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
            lines.append(f"| {_table_cell(label)} | {_table_cell(value)} |")

        # ── 日期 ──
        lines += ["", "**日期**", ""]
        lines += ["| 字段 | 值 |", "|---|---|"]
        if f.get("created"):
            lines.append(f"| 创建时间 | {_table_cell(f['created'])} |")
        if f.get("updated"):
            lines.append(f"| 更新时间 | {_table_cell(f['updated'])} |")

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
        links = f.get("issuelinks", []) if isinstance(f.get("issuelinks", []), list) else []
        if links:
            links, omitted_links = _bounded(links, _MAX_LIST_ITEMS)
            lines += ["", "**关联任务：**", ""]
            for link in links:
                if not isinstance(link, dict):
                    continue
                lt = link.get("type", {}) if isinstance(link.get("type", {}), dict) else {}
                if "outwardIssue" in link:
                    linked = link["outwardIssue"]
                    if not isinstance(linked, dict):
                        continue
                    lf = linked.get("fields") or {}
                    if not isinstance(lf, dict):
                        lf = {}
                    rel = lt.get("outward", lt.get("name", ""))
                    lines.append(
                        f"- {rel} {linked.get('key', '')} "
                        f"{lf.get('summary', '')} "
                        f"({_object_text(lf.get('status'), 'name')})"
                    )
                elif "inwardIssue" in link:
                    linked = link["inwardIssue"]
                    if not isinstance(linked, dict):
                        continue
                    lf = linked.get("fields") or {}
                    if not isinstance(lf, dict):
                        lf = {}
                    rel = lt.get("inward", lt.get("name", ""))
                    lines.append(
                        f"- {rel} {linked.get('key', '')} "
                        f"{lf.get('summary', '')} "
                        f"({_object_text(lf.get('status'), 'name')})"
                    )
            if omitted_links:
                lines.append(f"- （另有 {omitted_links} 条关联未显示）")

        # ── subtasks ──
        subtasks = f.get("subtasks", []) if isinstance(f.get("subtasks", []), list) else []
        if subtasks:
            subtasks, omitted_subtasks = _bounded(subtasks, _MAX_LIST_ITEMS)
            lines += ["", "**子任务：**", ""]
            for st in subtasks:
                if not isinstance(st, dict):
                    continue
                sf = st.get("fields", {}) if isinstance(st.get("fields", {}), dict) else {}
                lines.append(
                    f"- {st.get('key', '')} {sf.get('summary', '')} "
                    f"({_object_text(sf.get('status'), 'name')})"
                )
            if omitted_subtasks:
                lines.append(f"- （另有 {omitted_subtasks} 个子任务未显示）")

        # ── attachments ──
        attachments = f.get("attachment", []) if isinstance(f.get("attachment", []), list) else []
        if attachments:
            attachments, omitted_attachments = _bounded(attachments, _MAX_LIST_ITEMS)
            lines += ["", "**附件：**", ""]
            for att in attachments:
                if not isinstance(att, dict):
                    continue
                try:
                    size_mb = int(att.get("size") or 0) / 1024 / 1024
                except (TypeError, ValueError):
                    size_mb = 0
                author = _object_text(att.get("author"), "displayName")
                att_id = att.get("id", "")
                lines.append(
                    f"- {att.get('filename', '')} ({size_mb:.1f} MB) "
                    f"by {author} - {att.get('created', '')} "
                    f"[ID: {att_id}]"
                )
            if omitted_attachments:
                lines.append(f"- （另有 {omitted_attachments} 个附件未显示，可用 ID 单独读取）")

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
            # An instance with hundreds of populated custom fields would
            # otherwise render a single response of several hundred kilobytes.
            omitted = len(other_rows) - _MAX_OTHER_FIELD_ROWS
            lines += ["", "**其他信息**", ""]
            lines += ["| 字段 | 值 |", "|---|---|"]
            for label, value in other_rows[:_MAX_OTHER_FIELD_ROWS]:
                if len(value) > _MAX_OTHER_FIELD_LENGTH:
                    value = value[:_MAX_OTHER_FIELD_LENGTH] + "..."
                lines.append(f"| {_table_cell(label)} | {_table_cell(value)} |")
            if omitted > 0:
                lines += ["", f"（另有 {omitted} 个自定义字段未显示）"]

        return _render_within_budget(lines, _MAX_ISSUE_CHARS)

    # ── workflow transitions ──────────────────────────────────────

    def _fetch_transitions(self, issue_key: str) -> list[dict]:
        data = self._request(
            "GET",
            f"rest/api/2/issue/{issue_key}/transitions",
            params={"expand": "transitions.fields"},
        ).json()
        raw_transitions = data.get("transitions") if isinstance(data, dict) else None
        if not isinstance(raw_transitions, list) or any(
            not isinstance(transition, dict) for transition in raw_transitions
        ):
            raise TypeError("Jira transitions response must contain a transitions array")
        return [_normalize_transition(transition) for transition in raw_transitions]

    def get_transitions(self, issue_key: str, **kwargs) -> str:
        """Get the workflow transitions currently available to this account."""
        err = self._validate_issue_key(issue_key)
        if err:
            return _json_error(
                err,
                "invalid_issue_key",
                issue_key=issue_key,
                transitions=[],
                transition_count=0,
            )
        try:
            transitions = self._fetch_transitions(issue_key)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            LOGGER.warning("Jira transitions lookup returned HTTP %d", status)
            return _json_error(
                _http_error_message(exc),
                _status_error_code(status),
                issue_key=issue_key,
                transitions=[],
                transition_count=0,
            )
        except httpx.RequestError as exc:
            LOGGER.warning("Jira transitions request failed: %s", exc)
            return _json_error(
                f"Request failed: {exc}",
                "upstream_error",
                issue_key=issue_key,
                transitions=[],
                transition_count=0,
            )
        except Exception as exc:  # noqa: BLE001 - normalize provider failures to MCP errors
            LOGGER.exception("Jira transitions lookup failed")
            if isinstance(exc, TypeError):
                code = "invalid_provider_response"
            else:
                code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(
                f"{type(exc).__name__}: {exc}",
                code,
                issue_key=issue_key,
                transitions=[],
                transition_count=0,
            )

        return json.dumps(
            {
                "issue_key": issue_key,
                "transitions": transitions,
                "transition_count": len(transitions),
            },
            ensure_ascii=False,
        )

    def transition_issue(
        self,
        issue_key: str,
        transition_id: str,
        fields: dict[str, object] | None = None,
        **kwargs,
    ) -> str:
        """Execute one currently available Jira workflow transition."""
        issue_error = self._validate_issue_key(issue_key)
        if issue_error:
            return _json_error(
                issue_error,
                "invalid_issue_key",
                issue_key=issue_key,
                transition_id=transition_id,
                transitioned=False,
            )
        if not isinstance(transition_id, str) or not transition_id.isdigit():
            return _json_error(
                f"Invalid transition ID: {transition_id!r}",
                "invalid_transition_id",
                issue_key=issue_key,
                transition_id=transition_id,
                transitioned=False,
            )
        if fields is not None and not isinstance(fields, dict):
            return _json_error(
                "fields must be an object when provided",
                "invalid_fields",
                issue_key=issue_key,
                transition_id=transition_id,
                transitioned=False,
            )
        raw_transition_fields = fields or {}
        if len(raw_transition_fields) > 50:
            return _json_error(
                "fields must contain at most 50 entries",
                "invalid_fields",
                issue_key=issue_key,
                transition_id=transition_id,
                transitioned=False,
            )
        transition_fields: dict[str, object] = {}
        for raw_name, value in raw_transition_fields.items():
            if not isinstance(raw_name, str):
                return _json_error(
                    "field names must be strings",
                    "invalid_fields",
                    issue_key=issue_key,
                    transition_id=transition_id,
                    transitioned=False,
                )
            name = raw_name.strip()
            if not name or len(name) > 255:
                return _json_error(
                    "field names must contain 1 to 255 characters",
                    "invalid_fields",
                    issue_key=issue_key,
                    transition_id=transition_id,
                    transitioned=False,
                )
            transition_fields[name] = value
        try:
            serialized_fields = json.dumps(transition_fields, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            return _json_error(
                f"fields must contain JSON-compatible values: {exc}",
                "invalid_fields",
                issue_key=issue_key,
                transition_id=transition_id,
                transitioned=False,
            )
        if len(serialized_fields.encode("utf-8")) > 1_000_000:
            return _json_error(
                "serialized fields must not exceed 1 MB",
                "invalid_fields",
                issue_key=issue_key,
                transition_id=transition_id,
                transitioned=False,
            )

        try:
            transitions = self._fetch_transitions(issue_key)
            selected = next(
                (transition for transition in transitions if transition["id"] == transition_id),
                None,
            )
            if selected is None:
                return _json_error(
                    "The requested transition is not currently available",
                    "transition_unavailable",
                    issue_key=issue_key,
                    transition_id=transition_id,
                    transitioned=False,
                    available_transitions=transitions,
                )
            available_field_ids = {
                field["id"]
                for field in selected["fields"]
                if isinstance(field, dict) and field.get("id")
            }
            unavailable_fields = [
                name for name in transition_fields if name not in available_field_ids
            ]
            if unavailable_fields:
                return _json_error(
                    "One or more fields are unavailable on the selected transition",
                    "unavailable_transition_fields",
                    issue_key=issue_key,
                    transition_id=transition_id,
                    transitioned=False,
                    unavailable_fields=unavailable_fields,
                    available_fields=sorted(available_field_ids),
                )
            payload: dict[str, object] = {"transition": {"id": transition_id}}
            if fields is not None:
                payload["fields"] = transition_fields
            self._request(
                "POST",
                f"rest/api/2/issue/{issue_key}/transitions",
                json=payload,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            LOGGER.warning("Jira issue transition returned HTTP %d", status)
            return _json_error(
                _http_error_message(exc),
                _status_error_code(status),
                issue_key=issue_key,
                transition_id=transition_id,
                transitioned=False,
            )
        except httpx.RequestError as exc:
            LOGGER.warning("Jira issue transition request failed: %s", exc)
            return _json_error(
                f"Request failed: {exc}",
                "upstream_error",
                issue_key=issue_key,
                transition_id=transition_id,
                transitioned=False,
            )
        except Exception as exc:  # noqa: BLE001 - normalize provider failures to MCP errors
            LOGGER.exception("Jira issue transition failed")
            if isinstance(exc, TypeError):
                code = "invalid_provider_response"
            else:
                code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(
                f"{type(exc).__name__}: {exc}",
                code,
                issue_key=issue_key,
                transition_id=transition_id,
                transitioned=False,
            )

        return json.dumps(
            {
                "issue_key": issue_key,
                "transition_id": transition_id,
                "transitioned": True,
                "transition": selected,
            },
            ensure_ascii=False,
        )

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
            data = self._request(
                "GET",
                f"rest/api/2/issue/{issue_key}/comment",
                params={"maxResults": max_results, "startAt": start_at},
            ).json()
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

        raw_comments = data.get("comments", [])
        total = data.get("total", 0)
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            return _json_error(
                "Jira comments response contains an invalid total",
                "invalid_provider_response",
                issue_key=issue_key,
                comments=[],
                comment_count=0,
            )
        if not isinstance(raw_comments, list) or any(not isinstance(comment, dict) for comment in raw_comments):
            return _json_error(
                "Jira comments response contains an invalid comments list",
                "invalid_provider_response",
                issue_key=issue_key,
                comments=[],
                comment_count=0,
            )

        comments = []
        for c in raw_comments:
            comments.append(_normalize_comment(c))

        return json.dumps(
            {
                "issue_key": issue_key,
                "total": total,
                "start_at": start_at,
                "max_results": max_results,
                "comments": comments,
                "comment_count": len(comments),
                "has_more": start_at + len(comments) < total,
                "next_start_at": start_at + len(comments)
                if start_at + len(comments) < total
                else None,
            },
            ensure_ascii=False,
        )

    # ── comment mutations ─────────────────────────────────────────

    @staticmethod
    def _validate_comment_body(body: str) -> str | None:
        if not isinstance(body, str) or not body.strip():
            return "Comment body must not be empty"
        if len(body) > _MAX_COMMENT_LENGTH:
            return f"Comment body must not exceed {_MAX_COMMENT_LENGTH} characters"
        return None

    def add_comment(self, issue_key: str, body: str, **kwargs) -> str:
        """Add a new comment to a Jira issue."""
        issue_error = self._validate_issue_key(issue_key)
        if issue_error:
            return _json_error(issue_error, "invalid_issue_key", issue_key=issue_key, added=False)
        body_error = self._validate_comment_body(body)
        if body_error:
            return _json_error(body_error, "invalid_comment_body", issue_key=issue_key, added=False)

        try:
            comment = self._request(
                "POST",
                f"rest/api/2/issue/{issue_key}/comment",
                json={"body": body},
            ).json()
            if not isinstance(comment, dict):
                return _json_error(
                    "Jira add-comment response must be an object",
                    "invalid_provider_response",
                    issue_key=issue_key,
                    added=False,
                )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            LOGGER.warning("Jira comment creation returned HTTP %d", status)
            return _json_error(_http_error_message(exc), _status_error_code(status), issue_key=issue_key, added=False)
        except httpx.RequestError as exc:
            LOGGER.warning("Jira comment creation request failed: %s", exc)
            return _json_error(f"Request failed: {exc}", "upstream_error", issue_key=issue_key, added=False)
        except Exception as exc:  # noqa: BLE001 - normalize provider failures to MCP errors
            LOGGER.exception("Jira comment creation failed")
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(f"{type(exc).__name__}: {exc}", code, issue_key=issue_key, added=False)

        return json.dumps(
            {"issue_key": issue_key, "added": True, "comment": _normalize_comment(comment, body)},
            ensure_ascii=False,
        )

    def update_comment(self, issue_key: str, comment_id: str, body: str, **kwargs) -> str:
        """Replace the body of an existing Jira comment."""
        issue_error = self._validate_issue_key(issue_key)
        if issue_error:
            return _json_error(issue_error, "invalid_issue_key", issue_key=issue_key, comment_id=comment_id, updated=False)
        comment_error = self._validate_comment_id(comment_id)
        if comment_error:
            return _json_error(comment_error, "invalid_comment_id", issue_key=issue_key, comment_id=comment_id, updated=False)
        body_error = self._validate_comment_body(body)
        if body_error:
            return _json_error(body_error, "invalid_comment_body", issue_key=issue_key, comment_id=comment_id, updated=False)

        try:
            comment = self._request(
                "PUT",
                f"rest/api/2/issue/{issue_key}/comment/{comment_id}",
                json={"body": body},
            ).json()
            if not isinstance(comment, dict):
                return _json_error(
                    "Jira update-comment response must be an object",
                    "invalid_provider_response",
                    issue_key=issue_key,
                    comment_id=comment_id,
                    updated=False,
                )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            LOGGER.warning("Jira comment update returned HTTP %d", status)
            return _json_error(_http_error_message(exc), _status_error_code(status), issue_key=issue_key, comment_id=comment_id, updated=False)
        except httpx.RequestError as exc:
            LOGGER.warning("Jira comment update request failed: %s", exc)
            return _json_error(f"Request failed: {exc}", "upstream_error", issue_key=issue_key, comment_id=comment_id, updated=False)
        except Exception as exc:  # noqa: BLE001 - normalize provider failures to MCP errors
            LOGGER.exception("Jira comment update failed")
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(f"{type(exc).__name__}: {exc}", code, issue_key=issue_key, comment_id=comment_id, updated=False)

        return json.dumps(
            {
                "issue_key": issue_key,
                "comment_id": comment_id,
                "updated": True,
                "comment": _normalize_comment(comment, body),
            },
            ensure_ascii=False,
        )

    def delete_comment(self, issue_key: str, comment_id: str, **kwargs) -> str:
        """Delete an existing Jira comment."""
        issue_error = self._validate_issue_key(issue_key)
        if issue_error:
            return _json_error(issue_error, "invalid_issue_key", issue_key=issue_key, comment_id=comment_id, deleted=False)
        comment_error = self._validate_comment_id(comment_id)
        if comment_error:
            return _json_error(comment_error, "invalid_comment_id", issue_key=issue_key, comment_id=comment_id, deleted=False)

        try:
            self._request("DELETE", f"rest/api/2/issue/{issue_key}/comment/{comment_id}")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            LOGGER.warning("Jira comment deletion returned HTTP %d", status)
            return _json_error(_http_error_message(exc), _status_error_code(status), issue_key=issue_key, comment_id=comment_id, deleted=False)
        except httpx.RequestError as exc:
            LOGGER.warning("Jira comment deletion request failed: %s", exc)
            return _json_error(f"Request failed: {exc}", "upstream_error", issue_key=issue_key, comment_id=comment_id, deleted=False)
        except Exception as exc:  # noqa: BLE001 - normalize provider failures to MCP errors
            LOGGER.exception("Jira comment deletion failed")
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(f"{type(exc).__name__}: {exc}", code, issue_key=issue_key, comment_id=comment_id, deleted=False)

        return json.dumps(
            {"issue_key": issue_key, "comment_id": comment_id, "deleted": True},
            ensure_ascii=False,
        )

    # ── update_issue ──────────────────────────────────────────────

    def update_issue(self, issue_key: str, fields: dict[str, object], **kwargs) -> str:
        """Replace editable fields on an existing Jira issue.

        Jira's edit metadata is checked first so a request containing a field
        that this account cannot edit is rejected without partially updating the
        issue.
        """
        err = self._validate_issue_key(issue_key)
        if err:
            return _json_error(err, "invalid_issue_key", issue_key=issue_key)
        if not isinstance(fields, dict) or not fields:
            return _json_error(
                "fields must be a non-empty object",
                "invalid_fields",
                issue_key=issue_key,
            )
        if len(fields) > 50:
            return _json_error(
                "fields must contain at most 50 entries",
                "invalid_fields",
                issue_key=issue_key,
            )

        normalized_fields: dict[str, object] = {}
        for raw_name, value in fields.items():
            if not isinstance(raw_name, str):
                return _json_error(
                    "field names must be strings",
                    "invalid_fields",
                    issue_key=issue_key,
                )
            name = raw_name.strip()
            if not name or len(name) > 255:
                return _json_error(
                    "field names must contain 1 to 255 characters",
                    "invalid_fields",
                    issue_key=issue_key,
                )
            normalized_fields[name] = value

        try:
            serialized = json.dumps(normalized_fields, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            return _json_error(
                f"fields must contain JSON-compatible values: {exc}",
                "invalid_fields",
                issue_key=issue_key,
            )
        if len(serialized.encode("utf-8")) > 1_000_000:
            return _json_error(
                "serialized fields must not exceed 1 MB",
                "invalid_fields",
                issue_key=issue_key,
            )

        try:
            editmeta = self._request("GET", f"rest/api/2/issue/{issue_key}/editmeta").json()
            metadata_fields = editmeta.get("fields") if isinstance(editmeta, dict) else None
            if not isinstance(metadata_fields, dict):
                return _json_error(
                    "Jira edit metadata response must contain a fields object",
                    "invalid_provider_response",
                    issue_key=issue_key,
                )

            editable_aliases: dict[str, str] = {}
            for field_id, metadata in metadata_fields.items():
                if not isinstance(metadata, dict):
                    continue
                operations = metadata.get("operations")
                if isinstance(operations, list) and "set" not in operations:
                    continue
                identifiers = (
                    field_id,
                    metadata.get("key"),
                    metadata.get("id"),
                )
                for identifier in identifiers:
                    if isinstance(identifier, str) and identifier.strip():
                        editable_aliases[identifier.strip()] = str(field_id)

            uneditable_fields = [
                name for name in normalized_fields if name not in editable_aliases
            ]
            if uneditable_fields:
                return _json_error(
                    "The Jira account cannot edit one or more requested fields",
                    "uneditable_fields",
                    issue_key=issue_key,
                    uneditable_fields=uneditable_fields,
                    editable_fields=sorted(set(editable_aliases.values()))[:200],
                )

            self._request(
                "PUT",
                f"rest/api/2/issue/{issue_key}",
                json={"fields": normalized_fields},
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            LOGGER.warning("Jira issue edit metadata/update returned HTTP %d", status)
            return _json_error(
                _http_error_message(exc),
                _status_error_code(status),
                issue_key=issue_key,
            )
        except httpx.RequestError as exc:
            LOGGER.warning("Jira issue update request failed: %s", exc)
            return _json_error(
                f"Request failed: {exc}",
                "upstream_error",
                issue_key=issue_key,
            )
        except Exception as exc:  # noqa: BLE001 - normalize provider failures to MCP errors
            LOGGER.exception("Jira issue update failed")
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(
                f"{type(exc).__name__}: {exc}",
                code,
                issue_key=issue_key,
            )

        return json.dumps(
            {
                "issue_key": issue_key,
                "updated": True,
                "updated_fields": list(normalized_fields),
            },
            ensure_ascii=False,
        )

    # ── attachment content ────────────────────────────────────────

    def _fetch_attachment_content(self, attachment_id: str) -> tuple[dict, bytes] | str:
        """Fetch bounded attachment bytes without writing to disk."""
        config = self._config()
        try:
            meta = self._request("GET", f"rest/api/2/attachment/{attachment_id}").json()
            if not isinstance(meta, dict):
                return _json_error(
                    "Jira returned invalid attachment metadata",
                    "invalid_provider_response",
                    id=attachment_id,
                )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            LOGGER.warning("Jira attachment metadata returned HTTP %d", status)
            return _json_error(_http_error_message(exc), _status_error_code(status), id=attachment_id)
        except httpx.RequestError as exc:
            LOGGER.warning("Jira attachment metadata request failed: %s", exc)
            return _json_error(f"Request failed: {exc}", "upstream_error", id=attachment_id)
        except Exception as exc:
            LOGGER.exception("Jira attachment metadata lookup failed")
            code = "configuration_error" if isinstance(exc, RuntimeError) else "provider_error"
            return _json_error(f"{type(exc).__name__}: {exc}", code, id=attachment_id)

        filename = str(meta.get("filename") or "")
        try:
            declared_size = max(int(meta.get("size", 0)), 0)
        except (TypeError, ValueError):
            declared_size = 0
        mime_type = str(meta.get("mimeType") or "application/octet-stream")
        content_url = meta.get("content", "")
        if not isinstance(content_url, str) or not content_url:
            return _json_error(
                f"Attachment {attachment_id} has no download URL",
                "invalid_provider_response",
                id=attachment_id,
                filename=filename,
            )
        if declared_size > config.max_attachment_size:
            return _json_error(
                f"Attachment size ({declared_size} bytes) exceeds limit ({config.max_attachment_size} bytes)",
                "attachment_too_large",
                id=attachment_id,
                filename=filename,
                size=declared_size,
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

        try:
            chunks: list[bytes] = []
            downloaded_size = 0
            with self._stream("GET", download_url) as download_resp:
                for chunk in download_resp.iter_bytes(chunk_size=8192):
                    downloaded_size += len(chunk)
                    if downloaded_size > config.max_attachment_size:
                        raise _AttachmentTooLarge(
                            f"Downloaded content exceeds limit ({config.max_attachment_size} bytes)"
                        )
                    chunks.append(chunk)
        except _AttachmentTooLarge as exc:
            return _json_error(str(exc), "attachment_too_large", id=attachment_id, filename=filename)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            LOGGER.warning("Jira attachment download returned HTTP %d", status)
            return _json_error(_http_error_message(exc), _status_error_code(status), id=attachment_id)
        except httpx.RequestError as exc:
            LOGGER.warning("Jira attachment download failed: %s", exc)
            return _json_error(f"Download failed: {exc}", "upstream_error", id=attachment_id)
        except Exception as exc:
            LOGGER.exception("Jira attachment download failed")
            return _json_error(f"Download failed: {exc}", "download_failed", id=attachment_id)

        return {
            "id": attachment_id,
            "filename": filename,
            "size": downloaded_size,
            "declared_size": declared_size,
            "mime_type": mime_type,
        }, b"".join(chunks)

    def get_attachment(self, attachment_id: str, **kwargs) -> str:
        """Read an attachment without writing a local file."""
        err = self._validate_attachment_id(attachment_id)
        if err:
            return _json_error(err, "invalid_attachment_id", id=attachment_id)
        fetched = self._fetch_attachment_content(attachment_id)
        if isinstance(fetched, str):
            return fetched
        metadata, content = fetched
        return json.dumps(
            {
                **metadata,
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
            ensure_ascii=False,
        )

    def download_attachment(self, attachment_id: str, save_to: str, **kwargs) -> str:
        """Download an attachment to an authorized path for internal exports."""
        err = self._validate_attachment_id(attachment_id)
        if err:
            return _json_error(err, "invalid_attachment_id", id=attachment_id)
        config = self._config()
        try:
            save_path = resolve_output_path(save_to, config)
        except OutputPathError as exc:
            return _json_error(str(exc), "invalid_output_path", id=attachment_id)

        fetched = self._fetch_attachment_content(attachment_id)
        if isinstance(fetched, str):
            return fetched
        metadata, content = fetched
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
                temporary.write(content)
            commit_output_file(temporary_path, save_path, config)
            temporary_path = None
        except OutputPathError as exc:
            return _json_error(str(exc), "invalid_output_path", id=attachment_id, filename=metadata["filename"])
        except Exception as exc:
            LOGGER.exception("Jira attachment file write failed")
            return _json_error(f"Download failed: {exc}", "download_failed", id=attachment_id)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        return json.dumps(
            {
                **metadata,
                "saved_to": str(save_path),
            },
            ensure_ascii=False,
        )
