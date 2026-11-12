"""Jira's native CloneIssueDetails action, using the authenticated web session.

Jira Server does not expose Clone through its public REST v2 API. Keep the
version-dependent HTML handling here; never emulate Clone with issue creation.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

import httpx

from server.providers.jira import (
    _ISSUE_KEY_RE,
    JiraProvider,
    _http_error_message,
    _json_error,
    _status_error_code,
)

_MAX_PAGE_CHARS = 2_000_000
_MAX_PROGRESS_POLLS = 30
_PROGRESS_INTERVAL = 0.5
_OPTIONS = {
    "cloneAttachments": "attachment",
    "cloneSubTasks": "subtasks",
    "cloneLinks": "issuelinks",
}
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_ERROR_CLASSES = {"error", "errorMessage", "error-message", "aui-message-error"}


class ClonePage(HTMLParser):
    """Read only the native clone form, error text, and progress refresh target."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] | None = None
        self.options: set[str] = set()
        self.refresh: str | None = None
        self.errors: list[str] = []
        self._in_form = False
        self._elements: list[tuple[str, bool]] = []
        self._error_chars = 0
        if len(text) > _MAX_PAGE_CHARS:
            raise _CloneFailure(
                "Jira clone page exceeds the size limit", "invalid_provider_response"
            )
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "form":
            action = urlparse(data.get("action") or "").path.rsplit("/", 1)[-1]
            self._in_form = (
                action == "CloneIssueDetails.jspa"
                and (data.get("method") or "get").lower() == "post"
            )
            if self._in_form:
                if self.fields is not None:
                    raise _CloneFailure(
                        "Jira returned multiple clone forms",
                        "invalid_provider_response",
                    )
                self.fields = {}
        if tag == "input" and self._in_form and self.fields is not None:
            name = data.get("name") or ""
            if "disabled" not in data:
                if name in {"id", "atl_token", "summary", "Create"}:
                    if name in self.fields:
                        raise _CloneFailure(
                            "Jira clone form contains duplicate controls",
                            "invalid_provider_response",
                        )
                    self.fields[name] = data.get("value") or ""
                elif name in _OPTIONS and data.get("type", "").lower() == "checkbox":
                    self.options.add(name)
        if tag == "meta" and (data.get("http-equiv") or "").lower() == "refresh":
            self.refresh = _refresh_target(data.get("content") or "")
        if tag not in _VOID_TAGS:
            is_error = bool(set((data.get("class") or "").split()) & _ERROR_CLASSES)
            self._elements.append(
                (tag, is_error or bool(self._elements and self._elements[-1][1]))
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_form = False
        for index in range(len(self._elements) - 1, -1, -1):
            if self._elements[index][0] == tag:
                del self._elements[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._elements and self._elements[-1][1] and self._error_chars < 2000:
            text = " ".join(data.split())[: 2000 - self._error_chars]
            if text:
                self.errors.append(text)
                self._error_chars += len(text)


class _CloneFailure(ValueError):
    def __init__(self, message: str, code: str, **context: object) -> None:
        super().__init__(message)
        self.code = code
        self.context = context


def _refresh_target(value: str) -> str | None:
    match = re.fullmatch(
        r"\s*\d+(?:\.\d+)?\s*;\s*url\s*=\s*(.+?)\s*", value, re.IGNORECASE
    )
    return match.group(1).strip("\"'") if match else None


def _native_request(
    provider: JiraProvider, method: str, url: str, **kwargs
) -> httpx.Response:
    """Inspect redirects before following; never replay a native clone POST."""

    try:
        return provider._request(
            method,
            url,
            follow_redirects=False,
            _retry_after_401=method.upper() != "POST",
            **kwargs,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {301, 302, 303, 307, 308}:
            return exc.response
        raise


def _safe_target(
    provider: JiraProvider, target: str, current: str
) -> tuple[str, str | None]:
    """Accept only a browse result or the native progress action on this Jira."""

    base = provider._config().url.rstrip("/") + "/"
    resolved = urljoin(current, target)
    root, parsed = urlparse(base), urlparse(resolved)
    path = unquote(parsed.path)
    if (
        (parsed.scheme, parsed.hostname, parsed.port)
        != (root.scheme, root.hostname, root.port)
        or parsed.username is not None
        or parsed.password is not None
        or not path.startswith(root.path)
        or any(part in {".", ".."} for part in path.split("/"))
        or "\\" in path
        or any(ord(char) < 32 for char in resolved)
    ):
        raise _CloneFailure(
            "Jira clone returned an unsafe redirect", "invalid_clone_redirect"
        )
    relative = path[len(root.path) :]
    if relative.startswith("browse/"):
        key = relative.removeprefix("browse/")
        if _ISSUE_KEY_RE.fullmatch(key):
            return resolved, key.upper()
    if relative == "secure/CloneIssueDetails!progress.jspa":
        return resolved, None
    raise _CloneFailure(
        "Jira clone did not redirect to a new issue or native progress page",
        "invalid_clone_redirect",
    )


def _submit_clone(
    provider: JiraProvider,
    source_key: str,
    summary: str | None,
    options: dict[str, bool],
    state: dict,
) -> dict:
    source_response = provider._request(
        "GET",
        f"rest/api/2/issue/{source_key}",
        params={"fields": "summary,attachment,subtasks,issuelinks"},
    )
    try:
        source = source_response.json()
    except ValueError as exc:
        raise _CloneFailure(
            "Jira source issue response is not valid JSON", "invalid_provider_response"
        ) from exc
    if (
        not isinstance(source, dict)
        or not str(source.get("id") or "").isdigit()
        or not isinstance(source.get("fields"), dict)
    ):
        raise _CloneFailure(
            "Jira source issue response is invalid", "invalid_provider_response"
        )
    source_id = str(source["id"])
    canonical_key = str(source.get("key") or source_key).upper()
    if not _ISSUE_KEY_RE.fullmatch(canonical_key):
        raise _CloneFailure(
            "Jira source issue key is invalid", "invalid_provider_response"
        )
    base = provider._config().url.rstrip("/") + "/"
    dialog = _native_request(
        provider,
        "GET",
        "secure/CloneIssueDetails!default.jspa",
        params={"id": source_id},
        headers={"Accept": "text/html"},
    )
    if dialog.is_redirect:
        raise _CloneFailure(
            "Jira native clone form is unavailable or requires web authentication",
            "clone_unavailable",
        )
    page = ClonePage(dialog.text)
    fields = page.fields
    if (
        fields is None
        or fields.get("id") != source_id
        or not fields.get("atl_token")
        or not fields.get("summary")
    ):
        raise _CloneFailure(
            "Jira native clone form or CSRF token is unavailable", "clone_unavailable"
        )
    unavailable = [
        option
        for option, enabled in options.items()
        if enabled
        and option not in page.options
        and source["fields"].get(_OPTIONS[option]) != []
    ]
    if unavailable:
        raise _CloneFailure(
            "Requested copy options are unavailable in Jira's native clone form",
            "clone_options_unavailable",
            unavailable_options=unavailable,
        )
    payload = {
        **fields,
        **{
            name: str(enabled).lower()
            for name, enabled in options.items()
            if name in page.options
        },
    }
    if summary is not None:
        payload["summary"] = summary
    current = urljoin(base, "secure/CloneIssueDetails.jspa")
    state["submitted"] = True
    try:
        response = _native_request(
            provider, "POST", current, data=payload, headers={"Accept": "text/html"}
        )
    except httpx.HTTPStatusError as exc:
        if 400 <= exc.response.status_code < 500:
            state["submitted"] = False
        raise
    deadline = time.monotonic() + min(provider._config().timeout, 30.0)
    for attempt in range(_MAX_PROGRESS_POLLS + 1):
        if response.is_redirect:
            target = response.headers.get("location")
        else:
            page = ClonePage(response.text)
            if (
                attempt == 0
                and page.fields is not None
                and page.fields.get("id") == source_id
                and page.errors
            ):
                # Some versions can render errors after creating the parent
                # but failing to copy attachments or subtasks. An HTML error
                # alone cannot prove that the entire operation was rejected.
                raise _CloneFailure(" ".join(page.errors), "clone_reported_error")
            target = (
                _refresh_target(response.headers.get("refresh", "")) or page.refresh
            )
            if not target and state.get("progress_url"):
                target = current
        if not target:
            raise _CloneFailure(
                "Jira did not confirm the cloned issue", "invalid_provider_response"
            )
        current, key = _safe_target(provider, target, current)
        if key is not None:
            if key in {source_key, canonical_key}:
                raise _CloneFailure(
                    "Jira redirected to the source issue instead of a clone",
                    "invalid_provider_response",
                )
            return {
                "source_issue_key": source_key,
                "issue_key": key,
                "url": urljoin(base, f"browse/{key}"),
                "summary": payload["summary"],
                "created": True,
                "cloned": True,
                "clone_options": options,
            }
        state["progress_url"] = current
        remaining = deadline - time.monotonic()
        if attempt == _MAX_PROGRESS_POLLS or remaining <= 0:
            raise _CloneFailure(
                "Jira's native clone task is still in progress; open progress_url to check it",
                "clone_in_progress",
            )
        if attempt:
            time.sleep(min(_PROGRESS_INTERVAL, remaining))
        response = _native_request(
            provider,
            "GET",
            current,
            headers={"Accept": "text/html"},
            timeout=max(
                0.1, min(provider._config().timeout, deadline - time.monotonic())
            ),
        )
    raise AssertionError("Unreachable clone progress state")


def clone_issue(
    provider: JiraProvider,
    source_issue_key: str,
    summary: str | None = None,
    clone_attachments: bool = False,
    clone_subtasks: bool = False,
    clone_links: bool = False,
    idempotency_key: str | None = None,
) -> str:
    """Submit the native Clone action once and coalesce duplicate submissions."""

    source = (
        source_issue_key.strip().upper() if isinstance(source_issue_key, str) else ""
    )
    context = {"source_issue_key": source, "created": False, "cloned": False}
    error = provider._validate_issue_key(source)
    if error:
        return _json_error(error, "invalid_issue_key", **context)
    if summary is not None:
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary.strip()) > 255
            or any(ord(char) < 32 for char in summary)
        ):
            return _json_error(
                "summary must contain 1 to 255 printable characters",
                "invalid_summary",
                **context,
            )
        summary = summary.strip()
    options = {
        "cloneAttachments": clone_attachments,
        "cloneSubTasks": clone_subtasks,
        "cloneLinks": clone_links,
    }
    if any(type(value) is not bool for value in options.values()):
        return _json_error(
            "Clone options must be booleans", "invalid_clone_options", **context
        )
    if idempotency_key is not None:
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key.strip()) > 200
            or any(ord(char) < 32 for char in idempotency_key)
        ):
            return _json_error(
                "idempotency_key must contain 1 to 200 printable characters",
                "invalid_idempotency_key",
                **context,
            )
        idempotency_key = idempotency_key.strip()
    fingerprint = provider._create_fingerprint(
        source, "clone", {"summary": summary, **options}
    )
    assert fingerprint is not None  # Validated clone inputs are JSON serializable.
    token_value = idempotency_key if idempotency_key is not None else fingerprint
    token = (
        "clone:"
        + ("key:" if idempotency_key is not None else "fingerprint:")
        + hashlib.sha256(token_value.encode("utf-8")).hexdigest()
    )
    mode, cached = provider._begin_create_dedupe(token, fingerprint)
    if mode == "conflict":
        return _json_error(
            "The idempotency key is already associated with different clone options",
            "idempotency_key_reused",
            **context,
        )
    if mode == "cached":
        return provider._replayed_create_result(str(cached))
    if mode == "wait" and isinstance(cached, threading.Event):
        waited = json.loads(
            provider._wait_for_create_dedupe(token, cached, fingerprint)
        )
        if waited.get("error_code") in {
            "dedupe_wait_timeout",
            "dedupe_result_unavailable",
        }:
            waited.update(
                source_issue_key=source,
                created=None,
                cloned=None,
                outcome_unknown=True,
                retry_blocked=True,
            )
        return json.dumps(waited, ensure_ascii=False)
    if mode != "owner":
        return _json_error(
            "The clone request could not be coordinated safely",
            "dedupe_result_unavailable",
            **context,
        )

    state: dict = {"submitted": False}
    result = context.copy()
    try:
        result = _submit_clone(provider, source, summary, options, state)
    except _CloneFailure as exc:
        result.update(error=str(exc), error_code=exc.code, **exc.context)
    except httpx.HTTPStatusError as exc:
        result.update(
            error=_http_error_message(exc),
            error_code=_status_error_code(exc.response.status_code),
        )
    except httpx.RequestError:
        result.update(error="Jira clone request failed", error_code="upstream_error")
    except Exception as exc:  # noqa: BLE001 - always release duplicate waiters
        result.update(
            error=f"Jira clone failed: {type(exc).__name__}",
            error_code="configuration_error"
            if isinstance(exc, RuntimeError)
            else "provider_error",
        )
    finally:
        if state["submitted"] and not result.get("cloned"):
            result.update(
                created=None,
                cloned=None,
                outcome_unknown=True,
                retry_blocked=True,
                upstream_error=str(result.get("error") or "")[:500],
                upstream_error_code=result.get("error_code"),
                error_code="clone_outcome_unknown",
                error="Jira may have accepted the clone request. Check progress_url or search Jira before any manual retry; reuse the same idempotency key.",
            )
            if state.get("progress_url"):
                result["progress_url"] = state["progress_url"]
        raw = json.dumps(result, ensure_ascii=False)
        provider._finish_create_dedupe(
            token,
            fingerprint,
            idempotency_key is not None or bool(result.get("outcome_unknown")),
            raw,
        )
    return raw
