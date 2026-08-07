"""Jira provider registry.

.. note::
    The web search provider registry is in ``web/server/providers/__init__.py``.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class JiraProvider(Protocol):
    """Protocol for Jira API backend implementations."""

    def search_issues(
        self, jql: str, max_results: int = 50, start_at: int = 0, **kwargs
    ) -> str:
        """Execute a JQL search and return formatted result string."""
        ...

    def search_jql_fields(
        self, query: str = "", max_results: int = 50, start_at: int = 0, refresh: bool = False, **kwargs
    ) -> str:
        """Discover JQL field metadata from the current Jira instance."""
        ...

    def get_jql_value_suggestions(
        self, field: str, query: str = "", max_results: int = 50, refresh: bool = False, **kwargs
    ) -> str:
        """Get Jira-provided candidate values for one JQL field."""
        ...

    def get_issue(self, issue_key: str, **kwargs) -> str:
        """Get full issue details and return formatted result string."""
        ...

    def get_issue_json(self, issue_key: str, **kwargs) -> dict:
        """Get full issue details and return structured JSON dictionary."""
        ...

    def get_transitions(self, issue_key: str, **kwargs) -> str:
        """Get currently available workflow transitions for a Jira issue."""
        ...

    def transition_issue(
        self,
        issue_key: str,
        transition_id: str,
        fields: dict[str, object] | None = None,
        **kwargs,
    ) -> str:
        """Execute one Jira workflow transition."""
        ...

    def get_comments(
        self, issue_key: str, max_results: int = 50, start_at: int = 0, **kwargs
    ) -> str:
        """Get comments for an issue and return formatted result string."""
        ...

    def add_comment(self, issue_key: str, body: str, **kwargs) -> str:
        """Add a comment to a Jira issue and return a structured result string."""
        ...

    def update_comment(self, issue_key: str, comment_id: str, body: str, **kwargs) -> str:
        """Update one Jira comment and return a structured result string."""
        ...

    def delete_comment(self, issue_key: str, comment_id: str, **kwargs) -> str:
        """Delete one Jira comment and return a structured result string."""
        ...

    def update_issue(self, issue_key: str, fields: dict[str, object], **kwargs) -> str:
        """Update Jira issue fields and return a structured result string."""
        ...

    def get_attachment(self, attachment_id: str, **kwargs) -> str:
        """Read attachment content without writing a local file."""
        ...

    def download_attachment(self, attachment_id: str, save_to: str, **kwargs) -> str:
        """Download attachment content to a file path for internal exports."""
        ...


_providers: dict[str, JiraProvider] = {}


def register(name: str, provider: JiraProvider) -> None:
    """Register a Jira provider."""
    _providers[name] = provider


def get_provider(name: str) -> JiraProvider:
    """Get a registered provider by name."""
    if name not in _providers:
        raise ValueError(
            f"Unknown provider: {name!r}. "
            f"Available: {list(_providers.keys())}"
        )
    return _providers[name]


def list_providers() -> list[str]:
    """List all registered provider names."""
    return list(_providers.keys())


def is_registered(name: str) -> bool:
    """Check if a provider is registered by name."""
    return name in _providers
