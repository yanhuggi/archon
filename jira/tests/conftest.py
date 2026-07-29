"""Shared fixtures for archon-jira tests."""

from collections.abc import Generator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def clear_env() -> Generator[None, None, None]:
    """Clear provider env vars before each test.

    This fixture runs automatically for every test,
    ensuring tests start from a clean environment.
    """
    from server.providers import _providers

    _providers.clear()
    with patch.dict("os.environ", clear=True):
        yield
    _providers.clear()


@pytest.fixture
def jira_search_response() -> dict:
    """Simulated Jira search API success response."""
    return {
        "startAt": 0,
        "maxResults": 50,
        "total": 2,
        "issues": [
            {
                "key": "PROJ-1",
                "fields": {
                    "summary": "Fix login bug",
                    "status": {"name": "In Progress"},
                    "assignee": {"displayName": "John Doe"},
                    "issuetype": {"name": "Bug"},
                    "priority": {"name": "High"},
                    "labels": ["urgent"],
                    "created": "2025-01-15T10:30:00.000+0800",
                    "updated": "2025-01-20T14:00:00.000+0800",
                },
            },
            {
                "key": "PROJ-2",
                "fields": {
                    "summary": "Add dark mode",
                    "status": {"name": "Open"},
                    "assignee": None,
                    "issuetype": {"name": "Story"},
                    "priority": {"name": "Medium"},
                    "labels": [],
                    "created": "2025-01-16T09:00:00.000+0800",
                    "updated": "2025-01-16T09:00:00.000+0800",
                },
            },
        ],
    }


@pytest.fixture
def jira_issue_response() -> dict:
    """Simulated Jira issue detail API success response."""
    return {
        "key": "PROJ-1",
        "fields": {
            "summary": "Fix login bug",
            "description": "Users cannot log in when...",
            "status": {"name": "In Progress"},
            "assignee": {"displayName": "John Doe"},
            "reporter": {"displayName": "Jane Smith"},
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "labels": ["urgent"],
            "created": "2025-01-15T10:30:00.000+0800",
            "updated": "2025-01-20T14:00:00.000+0800",
            "parent": {"key": "PROJ-100", "fields": {"summary": "Login overhaul"}},
            "subtasks": [
                {
                    "key": "PROJ-2",
                    "fields": {
                        "summary": "Backend fix",
                        "status": {"name": "Done"},
                    },
                },
                {
                    "key": "PROJ-3",
                    "fields": {
                        "summary": "Frontend fix",
                        "status": {"name": "Open"},
                    },
                },
            ],
            "issuelinks": [
                {
                    "type": {
                        "name": "Blocks",
                        "outward": "Blocks",
                        "inward": "is blocked by",
                    },
                    "outwardIssue": {
                        "key": "PROJ-10",
                        "fields": {
                            "summary": "Deployment",
                            "status": {"name": "Open"},
                        },
                    },
                },
                {
                    "type": {
                        "name": "Blocks",
                        "outward": "Blocks",
                        "inward": "is blocked by",
                    },
                    "inwardIssue": {
                        "key": "PROJ-5",
                        "fields": {
                            "summary": "DB migration",
                            "status": {"name": "Done"},
                        },
                    },
                },
            ],
            "attachment": [
                {
                    "id": "10001",
                    "filename": "screenshot.png",
                    "size": 204800,
                    "mimeType": "image/png",
                    "author": {"displayName": "John Doe"},
                    "created": "2025-01-16T12:00:00.000+0800",
                },
                {
                    "id": "10002",
                    "filename": "error.log",
                    "size": 4096,
                    "mimeType": "text/plain",
                    "author": {"displayName": "Jane Smith"},
                    "created": "2025-01-17T15:00:00.000+0800",
                },
            ],
        },
    }


@pytest.fixture
def jira_comments_response() -> dict:
    """Simulated Jira comments API success response."""
    return {
        "total": 2,
        "startAt": 0,
        "maxResults": 50,
        "comments": [
            {
                "author": {"displayName": "John Doe"},
                "body": "Working on the fix now.",
                "created": "2025-01-16T10:00:00.000+0800",
                "updated": "2025-01-16T10:00:00.000+0800",
            },
            {
                "author": {"displayName": "Jane Smith"},
                "body": "Please prioritize this.",
                "created": "2025-01-17T09:00:00.000+0800",
                "updated": "2025-01-17T09:00:00.000+0800",
            },
        ],
    }
