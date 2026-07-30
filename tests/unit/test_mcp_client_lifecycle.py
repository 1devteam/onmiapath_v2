"""Lifecycle regression tests for the MCP subprocess client."""

from unittest.mock import MagicMock

import pytest

from backend.integrations.mcp.mcp_client import MCPClient


class FakeProcess:
    """Minimal asyncio subprocess stand-in for shutdown behavior."""

    def __init__(
        self,
        *,
        returncode: int | None,
        terminate_error: BaseException | None = None,
        wait_error: BaseException | None = None,
    ) -> None:
        self.returncode = returncode
        self.terminate_error = terminate_error
        self.wait_error = wait_error
        self.terminate = MagicMock(side_effect=terminate_error)
        self.wait_calls = 0

    async def wait(self) -> int:
        """Record the wait and emulate process completion."""
        self.wait_calls += 1
        if self.wait_error is not None:
            raise self.wait_error
        self.returncode = 0
        return self.returncode


@pytest.mark.asyncio
async def test_stop_server_does_not_signal_exited_process() -> None:
    """An already-exited child is reaped without sending another signal."""
    client = MCPClient()
    process = FakeProcess(returncode=1)
    client._processes["exited"] = process

    await client.stop_server("exited")

    process.terminate.assert_not_called()
    assert process.wait_calls == 1
    assert "exited" not in client._processes


@pytest.mark.asyncio
async def test_stop_server_handles_exit_race() -> None:
    """Shutdown remains successful if the child exits before termination."""
    client = MCPClient()
    process = FakeProcess(
        returncode=None,
        terminate_error=ProcessLookupError(),
        wait_error=ProcessLookupError(),
    )
    client._processes["racing"] = process

    await client.stop_server("racing")

    process.terminate.assert_called_once_with()
    assert process.wait_calls == 1
    assert "racing" not in client._processes


@pytest.mark.asyncio
async def test_stop_server_is_idempotent() -> None:
    """Repeated shutdown calls are safe."""
    client = MCPClient()

    await client.stop_server("missing")
    await client.stop_server("missing")
