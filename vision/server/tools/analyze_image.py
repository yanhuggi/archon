"""analyze_image tool definition."""

import json
import sys

from mcp.server import MCPServer

from server.providers import get_provider


def register(mcp: MCPServer, default_provider: str = "mimo") -> None:
    """Register the analyze_image tool on the given MCP server.

    Args:
        mcp: The MCPServer instance.
        default_provider: Default image provider to use when not specified.
    """

    @mcp.tool(
        description=(
            "Analyze and describe images: photos, screenshots, diagrams, charts, "
            "UI mockups, scanned documents, or any visual content. Supports image "
            "URLs, base64 data URIs, and local file paths (@prefix). "
            "MUST use this tool when the user: provides an image URL or file path "
            "and asks about its content; asks 'what's in this image' or similar; "
            "shares a screenshot for debugging or review; wants OCR text extraction "
            "from an image; needs a diagram, chart, or UI mockup described. "
            "Use the 'prompt' parameter to ask specific questions about the image "
            "(e.g. 'What objects are in this photo?', 'Read the error message')."
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
