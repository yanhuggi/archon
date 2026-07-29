"""Shared HTTP client utilities with SSL context and connection pooling."""

import atexit
import ssl
import threading

import httpx

_ssl_ctx = ssl.create_default_context()
_http_client: httpx.Client | None = None
_lock = threading.Lock()


def get_shared_http_client(timeout: int) -> httpx.Client:
    """Get or create a shared HTTP client with proper SSL configuration.

    Uses double-checked locking for thread safety and registers an
    atexit handler to close the client on process exit.
    """
    global _http_client  # noqa: PLW0603

    if _http_client is None or _http_client.is_closed:
        with _lock:
            if _http_client is None or _http_client.is_closed:
                _http_client = httpx.Client(
                    timeout=timeout,
                    verify=_ssl_ctx,
                    follow_redirects=True,
                    limits=httpx.Limits(max_keepalive_connections=5),
                )
                atexit.register(_close_client)

    return _http_client


def _close_client() -> None:
    """Close the shared HTTP client if it exists."""
    global _http_client  # noqa: PLW0603
    if _http_client is not None and not _http_client.is_closed:
        _http_client.close()
