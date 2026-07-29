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

    def _get_field_map(self) -> dict[str, str]:
        """Fetch custom field ID → name mapping from Jira (always fresh)."""
        try:
            client = self._get_client()
            resp = client.get("/rest/api/2/field")
            resp.raise_for_status()
            return {
                f["id"]: f["name"]
                for f in resp.json()
                if f.get("custom", False)
            }
        except Exception as e:
            print(f"Warning: failed to fetch field map: {e}", file=sys.stderr)
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
            return f"Error: {err}"

        field_map = self._get_field_map()
        custom_fields = ",".join(field_map.keys())
        fields_param = (
            "summary,description,status,assignee,reporter,issuetype,"
            "priority,labels,created,updated,subtasks,issuelinks,"
            "attachment,parent,components,versions,fixVersions,duedate,resolution"
        )
        if custom_fields:
            fields_param += "," + custom_fields

        try:
            client = self._get_client()
            resp = client.get(f"/rest/api/2/issue/{issue_key}", params={"fields": fields_param})
            resp.raise_for_status()
            issue = resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                self._invalidate_client()
            print(f"Error: Jira HTTP {e.response.status_code}: {e.response.text[:200]}", file=sys.stderr)
            return f"Error: HTTP {e.response.status_code}: {e.response.text[:500]}"
        except httpx.RequestError as e:
            print(f"Error: Jira request failed: {e}", file=sys.stderr)
            return f"Error: Request failed: {e}"
        except Exception as e:
            print(f"Error: Jira get_issue failed: {e}", file=sys.stderr)
            return f"Error: {type(e).__name__}: {e}"

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
                return val.get("displayName") or val.get("value") or str(val)
            if isinstance(val, list):
                items = [
                    item.get("value") or item.get("name") if isinstance(item, dict) else str(item)
                    for item in val
                ]
                return ", ".join(items)
            text = str(val).strip()
            return text if text and text != "None" else None

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
                lines.append(
                    f"- {att.get('filename', '')} ({size_mb:.1f} MB) "
                    f"by {author} - {att.get('created', '')}"
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
