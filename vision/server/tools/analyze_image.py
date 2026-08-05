"""The public ``analyze_image`` MCP tool."""

from __future__ import annotations

import json
import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from server.config import VisionConfig
from server.instructions import ANALYZE_IMAGE_DESCRIPTION
from server.providers import get_provider
from server.providers.mimo import DEFAULT_PROMPT, display_image_source


LOGGER = logging.getLogger(__name__)
MAX_PROMPT_LENGTH = 4000


def _model_name(config: VisionConfig | None) -> str:
    """Return the model name to report in tool-layer envelopes.

    Prefer the config the server was built with, so a fixed-config server
    reports the same model in every response. Fall back to the environment
    only when no config was supplied.
    """

    if config is not None:
        return config.model
    try:
        return VisionConfig.from_env().model
    except Exception:  # pragma: no cover - config parsing is already defensive
        return ""


def _error_response(
    image_source: object,
    prompt: object,
    code: str,
    message: str,
    config: VisionConfig | None = None,
) -> str:
    # Keep the same key set as the provider-layer envelope so callers can parse
    # any failure identically, regardless of which layer rejected the call.
    return json.dumps(
        {
            "image_url": display_image_source(image_source if isinstance(image_source, str) else ""),
            "prompt": prompt if isinstance(prompt, str) else "",
            "understanding": "",
            "model": _model_name(config),
            "error": message,
            "error_code": code,
        },
        ensure_ascii=False,
    )


def _ensure_response(
    raw: object,
    image_source: str,
    prompt: str,
    config: VisionConfig | None = None,
) -> str:
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return _error_response(
                image_source,
                prompt,
                "invalid_provider_response",
                "Provider returned invalid JSON",
                config,
            )
    elif isinstance(raw, dict):
        data = raw
    else:
        return _error_response(
            image_source,
            prompt,
            "invalid_provider_response",
            "Provider returned an unsupported response",
            config,
        )
    if not isinstance(data, dict):
        return _error_response(
            image_source,
            prompt,
            "invalid_provider_response",
            "Provider response must be an object",
            config,
        )
    data.setdefault("image_url", display_image_source(image_source))
    data.setdefault("prompt", prompt)
    data.setdefault("understanding", "")
    data.setdefault("model", _model_name(config))
    return json.dumps(data, ensure_ascii=False)


def register(
    mcp: MCPServer,
    default_provider: str = "mimo",
    config: VisionConfig | None = None,
) -> None:
    """Register the single-provider image analysis tool.

    ``config`` should be the same configuration the provider was built with, so
    that tool-layer envelopes report the model the server actually uses.
    """

    @mcp.tool(
        name="analyze_image",
        title="Analyze Image",
        description=ANALYZE_IMAGE_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=False,
    )
    def analyze_image(
        image_source: Annotated[
            str,
            Field(min_length=1, description="Image URL, base64 data URI, or authorized JPEG/PNG path."),
        ],
        prompt: Annotated[
            str,
            Field(min_length=1, max_length=MAX_PROMPT_LENGTH, description="Focused visual question."),
        ] = DEFAULT_PROMPT,
    ) -> str:
        """Analyze one image and return a stable JSON result envelope."""

        if not isinstance(image_source, str) or not image_source.strip():
            return _error_response(
                image_source,
                prompt,
                "invalid_image_source",
                "image_source must not be empty",
                config,
            )
        if not isinstance(prompt, str) or not prompt.strip():
            return _error_response(
                image_source,
                prompt,
                "invalid_prompt",
                "prompt must not be empty",
                config,
            )
        normalized_prompt = prompt.strip()
        if len(normalized_prompt) > MAX_PROMPT_LENGTH:
            return _error_response(
                image_source,
                normalized_prompt,
                "invalid_prompt",
                f"prompt must be at most {MAX_PROMPT_LENGTH} characters",
                config,
            )

        try:
            provider = get_provider(default_provider)
        except ValueError as exc:
            return _error_response(
                image_source,
                normalized_prompt,
                "provider_unavailable",
                str(exc),
                config,
            )

        try:
            raw = provider.understand(image_source.strip(), prompt=normalized_prompt)
        except Exception as exc:  # pragma: no cover - defensive provider boundary
            LOGGER.exception("analyze_image provider failed")
            return _error_response(
                image_source,
                normalized_prompt,
                "provider_error",
                f"Image provider failed: {type(exc).__name__}: {exc}",
                config,
            )
        return _ensure_response(raw, image_source, normalized_prompt, config)
