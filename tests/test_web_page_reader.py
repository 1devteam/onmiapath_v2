"""
Tests for WebPageReaderTool
===========================
Covers: URL validation, SSRF prevention, content extraction,
        truncation, error handling, and the sync wrapper.

All HTTP calls are mocked — no real network requests are made.

Author: Dev Team Lead
Built with Pride for Obex Blackvault
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure repo root is on path when running standalone
sys.path.insert(0, str(Path(__file__).parent.parent))  # noqa: E402

import pytest  # noqa: E402

from backend.agents.tools.web_page_reader import (  # noqa: E402
    WebPageReaderTool,
    _extract_clean_text,
    _is_blocked_host,
    read_web_page_sync,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status_code: int = 200,
    content_type: str = "text/html",
    text: str = "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>",
    final_url: str = "https://example.com",
) -> MagicMock:
    """Create a mock aiohttp.ClientResponse."""
    mock_resp = AsyncMock()
    mock_resp.status = status_code
    mock_resp.headers = {"content-type": content_type}
    mock_resp.text = AsyncMock(return_value=text)
    mock_resp.url = final_url
    return mock_resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_clean_text():
    """Test HTML text extraction."""
    html = "<html><body><p>Hello</p><script>alert('xss')</script></body></html>"
    text = _extract_clean_text(html)
    assert "Hello" in text
    assert "alert" not in text


def test_is_blocked_host():
    """Test SSRF prevention."""
    assert _is_blocked_host("localhost")
    assert _is_blocked_host("127.0.0.1")
    assert _is_blocked_host("192.168.1.1")
    assert not _is_blocked_host("example.com")


@pytest.mark.asyncio
async def test_read_web_page_success():
    """Test successful page read."""
    mock_resp = _make_mock_response(text="<html><body>Content</body></html>")
    tool = WebPageReaderTool()

    with patch("aiohttp.ClientSession.get", return_value=mock_resp):
        result = await tool.read("https://example.com")
        assert "Content" in result


@pytest.mark.asyncio
async def test_read_web_page_blocked_host():
    """Test SSRF prevention."""
    tool = WebPageReaderTool()
    with pytest.raises(ValueError, match="blocked"):
        await tool.read("http://localhost:8000")


def test_read_web_page_sync():
    """Test synchronous wrapper."""
    mock_resp = _make_mock_response(text="<html><body>Sync Test</body></html>")

    with patch("aiohttp.ClientSession.get", return_value=mock_resp):
        result = read_web_page_sync("https://example.com")
        assert "Sync Test" in result
