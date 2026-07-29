"""Jira REST API provider implementation."""

import json
import os
import re
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

_MAX_FIELD_LENGTH = 2000
_DEFAULT_MAX_RESULTS = 50
_MAX_SEARCH_RESULTS = 200
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB
_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")


class JiraProvider:
    """Jira Server REST API client using session authentication."""

    def __init__(self) -> None:
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    def _get_client(self) -> httpx.Client:
        if self._client is not None and self._client.is_closed:
            self._client = None
        if self._client is None:
            with self._lock:
                if self._client is not None and self._client.is_closed:
                    self._client = None
                if self._client is None:
                    jira_url = os.environ.get("JIRA_URL", "")
                    username = os.environ.get("JIRA_USERNAME", "")
                    password = os.environ.get("JIRA_PASSWORD", "")
                    try:
                        timeout = int(os.environ.get("JIRA_TIMEOUT", _DEFAULT_TIMEOUT))
                    except ValueError:
                        print("Warning: invalid JIRA_TIMEOUT, falling back to 30s", file=sys.stderr)
                        timeout = _DEFAULT_TIMEOUT
                    self._client = httpx.Client(
                        timeout=timeout,
                        headers={"Accept": "application/json"},
                        base_url=jira_url.rstrip("/"),
                    )
                    self._login(username, password)
        return self._client

    def _login(self, username: str, password: str) -> None:
        """Authenticate via Jira session API and store the session cookie."""
        resp = self._client.post(
            "/rest/auth/1/session",
            json={"username": username, "password": password},
        )
        resp.raise_for_status()

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            self._client.close()

    def _invalidate_client(self) -> None:
        """Close and discard the current client so the next call creates a fresh session."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
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
                "/rest/api/2/search",
                params={
                    "jql": jql,
                    "fields": fields,
                    "startAt": start_at,
                    "maxResults": max_results,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                self._invalidate_client()
            print(f"Error: Jira HTTP {e.response.status_code}: {e.response.text[:200]}", file=sys.stderr)
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}, ensure_ascii=False)
        except httpx.RequestError as e:
            print(f"Error: Jira request failed: {e}", file=sys.stderr)
            return json.dumps({"error": f"Request failed: {e}"}, ensure_ascii=False)
        except Exception as e:
            print(f"Error: Jira search_issues failed: {e}", file=sys.stderr)
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

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

    # ── get_issue ──────────────────────────────────────────────────

    def get_issue(self, issue_key: str, **kwargs) -> str:
        err = self._validate_issue_key(issue_key)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)

        fields = (
            "summary,description,status,assignee,reporter,issuetype,"
            "priority,labels,created,updated,subtasks,issuelinks,"
            "attachment,parent"
        )
        try:
            client = self._get_client()
            resp = client.get(f"/rest/api/2/issue/{issue_key}", params={"fields": fields})
            resp.raise_for_status()
            issue = resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                self._invalidate_client()
            print(f"Error: Jira HTTP {e.response.status_code}: {e.response.text[:200]}", file=sys.stderr)
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}, ensure_ascii=False)
        except httpx.RequestError as e:
            print(f"Error: Jira request failed: {e}", file=sys.stderr)
            return json.dumps({"error": f"Request failed: {e}"}, ensure_ascii=False)
        except Exception as e:
            print(f"Error: Jira get_issue failed: {e}", file=sys.stderr)
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

        f = issue.get("fields", {})

        # Issue links normalization
        issue_links = []
        for link in f.get("issuelinks", []):
            link_type = link.get("type", {})
            if "outwardIssue" in link:
                linked = link["outwardIssue"]
                issue_links.append({
                    "type": link_type.get("outward", link_type.get("name", "")),
                    "direction": "outward",
                    "linked_issue": {
                        "key": linked.get("key", ""),
                        "summary": (linked.get("fields") or {}).get("summary", ""),
                        "status": ((linked.get("fields") or {}).get("status") or {}).get("name", ""),
                    },
                })
            elif "inwardIssue" in link:
                linked = link["inwardIssue"]
                issue_links.append({
                    "type": link_type.get("inward", link_type.get("name", "")),
                    "direction": "inward",
                    "linked_issue": {
                        "key": linked.get("key", ""),
                        "summary": (linked.get("fields") or {}).get("summary", ""),
                        "status": ((linked.get("fields") or {}).get("status") or {}).get("name", ""),
                    },
                })

        # Subtasks
        subtasks = []
        for st in f.get("subtasks", []):
            sf = st.get("fields", {})
            subtasks.append({
                "key": st.get("key", ""),
                "summary": sf.get("summary", ""),
                "status": (sf.get("status") or {}).get("name", ""),
            })

        # Parent
        parent = None
        if f.get("parent"):
            pf = f["parent"]
            parent = {
                "key": pf.get("key", ""),
                "summary": (pf.get("fields") or {}).get("summary", ""),
            }

        # Attachments metadata
        attachments = []
        for att in f.get("attachment", []):
            attachments.append({
                "id": att.get("id", ""),
                "filename": att.get("filename", ""),
                "size": att.get("size", 0),
                "mime_type": att.get("mimeType", ""),
                "author": (att.get("author") or {}).get("displayName", ""),
                "created": att.get("created", ""),
            })

        description = f.get("description") or ""
        if len(description) > _MAX_FIELD_LENGTH:
            description = description[:_MAX_FIELD_LENGTH] + "..."

        return json.dumps(
            {
                "key": issue.get("key", ""),
                "summary": f.get("summary", ""),
                "description": description,
                "status": (f.get("status") or {}).get("name", ""),
                "assignee": (f.get("assignee") or {}).get("displayName", "Unassigned"),
                "reporter": (f.get("reporter") or {}).get("displayName", ""),
                "issue_type": (f.get("issuetype") or {}).get("name", ""),
                "priority": (f.get("priority") or {}).get("name", ""),
                "labels": f.get("labels", []),
                "created": f.get("created", ""),
                "updated": f.get("updated", ""),
                "parent": parent,
                "subtasks": subtasks,
                "issue_links": issue_links,
                "attachments": attachments,
            },
            ensure_ascii=False,
        )

    # ── get_comments ───────────────────────────────────────────────

    def get_comments(
        self, issue_key: str, max_results: int = 50, start_at: int = 0, **kwargs
    ) -> str:
        max_results = min(max(max_results, 1), 100)
        start_at = max(start_at, 0)

        try:
            client = self._get_client()
            resp = client.get(
                f"/rest/api/2/issue/{issue_key}/comment",
                params={"maxResults": max_results, "startAt": start_at},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                self._invalidate_client()
            print(f"Error: Jira HTTP {e.response.status_code}: {e.response.text[:200]}", file=sys.stderr)
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}, ensure_ascii=False)
        except httpx.RequestError as e:
            print(f"Error: Jira request failed: {e}", file=sys.stderr)
            return json.dumps({"error": f"Request failed: {e}"}, ensure_ascii=False)
        except Exception as e:
            print(f"Error: Jira get_comments failed: {e}", file=sys.stderr)
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

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
            return json.dumps({"error": err}, ensure_ascii=False)

        save_path = Path(save_to)
        max_size = int(
            os.environ.get("JIRA_MAX_ATTACHMENT_SIZE", _DEFAULT_MAX_ATTACHMENT_SIZE)
        )

        try:
            client = self._get_client()

            # Get attachment metadata
            meta_resp = client.get(f"/rest/api/2/attachment/{attachment_id}")
            meta_resp.raise_for_status()
            meta = meta_resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                self._invalidate_client()
            print(f"Error: Jira HTTP {e.response.status_code}: {e.response.text[:200]}", file=sys.stderr)
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}, ensure_ascii=False)
        except httpx.RequestError as e:
            print(f"Error: Jira request failed: {e}", file=sys.stderr)
            return json.dumps({"error": f"Request failed: {e}"}, ensure_ascii=False)
        except Exception as e:
            print(f"Error: Jira get_attachment metadata failed: {e}", file=sys.stderr)
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

        filename = meta.get("filename", "")
        size = meta.get("size", 0)
        mime_type = meta.get("mimeType", "")
        content_url = meta.get("content", "")

        if not content_url:
            return json.dumps(
                {"error": f"Attachment {attachment_id} has no download URL"},
                ensure_ascii=False,
            )

        if size > max_size:
            return json.dumps(
                {
                    "id": attachment_id,
                    "filename": filename,
                    "size": size,
                    "mime_type": mime_type,
                    "error": f"Attachment size ({size} bytes) exceeds limit ({max_size} bytes)",
                },
                ensure_ascii=False,
            )

        # Ensure parent directory exists
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return json.dumps(
                {"error": f"Cannot create directory {save_path.parent}: {e}"},
                ensure_ascii=False,
            )

        # Download content — stream directly to file
        try:
            parsed = urlparse(content_url)
            query_params = parse_qs(parsed.query) if parsed.query else None
            if query_params:
                query_params = {k: v[0] if len(v) == 1 else v for k, v in query_params.items()}
            with client.stream(
                "GET",
                f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                params=query_params,
            ) as download_resp:
                download_resp.raise_for_status()
                with open(save_path, "wb") as f:
                    for chunk in download_resp.iter_bytes(chunk_size=8192):
                        f.write(chunk)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                self._invalidate_client()
            print(f"Error: Jira attachment download HTTP {e.response.status_code}: {e.response.text[:200]}", file=sys.stderr)
            return json.dumps({"error": f"Download failed: HTTP {e.response.status_code}"}, ensure_ascii=False)
        except httpx.RequestError as e:
            print(f"Error: Jira attachment download failed: {e}", file=sys.stderr)
            return json.dumps({"error": f"Download failed: {e}"}, ensure_ascii=False)
        except Exception as e:
            print(f"Error: Jira get_attachment download failed: {e}", file=sys.stderr)
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

        return json.dumps(
            {
                "id": attachment_id,
                "filename": filename,
                "saved_to": str(save_path.resolve()),
                "size": save_path.stat().st_size,
                "mime_type": mime_type,
            },
            ensure_ascii=False,
        )
