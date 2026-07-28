"""analyze_image tool definition."""

import json

from mcp.server.fastmcp import FastMCP

from server.providers import get_provider


def register(mcp: FastMCP, default_provider: str = "mimo") -> None:
    """Register the analyze_image tool on the given MCP server.

    Args:
        mcp: The FastMCP instance.
        default_provider: Default image provider to use when not specified.
    """

    @mcp.tool(
        description=(
            "Call this when the user provides an image (via URL, file, drag-and-drop) "
            "and asks about its content — especially when the backend model lacks built-in "
            "vision capabilities. Analyzes and returns a detailed description of the image. "
            "Supports image URLs (http/https), base64 data URIs, "
            "and local file paths (@prefix). Provide a 'prompt' to ask specific questions "
            "about the image (e.g. 'What objects are in this photo?'). "
            "Do NOT call this tool for images that have already been described "
            "in the conversation, or when the model already has vision capabilities."
        )
    )
    def analyze_image(
        image_source: str,
        prompt: str = "请详细描述这张图片的内容",
        provider: str = default_provider,
    ) -> str:
        """Analyze an image and return a textual description of its content.

        Args:
            image_source: The image to analyze. Can be an HTTP/HTTPS URL,
                a base64 data URI (data:image/...;base64,...), or a local file
                path optionally prefixed with @ (e.g. @screenshot.png).
            prompt: Question or instruction about the image content.
            provider: Vision backend to use. Currently only "mimo" is supported.

        Returns:
            JSON string with image_url, prompt, understanding, model, and
            optional reasoning_content.
        """
        try:
            p = get_provider(provider)
        except ValueError as e:
            return json.dumps({
                "image_url": image_source,
                "prompt": prompt,
                "understanding": "",
                "error": str(e),
            }, ensure_ascii=False)

        try:
            return p.understand(image_source, prompt=prompt)
        except Exception as e:
            print(f"Error: analyze_image failed: {e}", file=sys.stderr)
            return json.dumps({
                "image_url": image_source,
                "prompt": prompt,
                "understanding": "",
                "error": f"{type(e).__name__}: {e}",
            }, ensure_ascii=False)
