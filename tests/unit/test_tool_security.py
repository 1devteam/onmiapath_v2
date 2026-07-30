"""Security regression tests for agent file and calculator tools."""

from pathlib import Path

import pytest

from backend.agents.tools.tool_registry import CalculatorTool, FileReaderTool, FileWriterTool


@pytest.mark.asyncio
async def test_file_tools_allow_paths_inside_configured_root(tmp_path: Path) -> None:
    """Read and write a file that resolves inside the configured workspace."""
    writer = FileWriterTool([str(tmp_path)])
    reader = FileReaderTool([str(tmp_path)])
    target = tmp_path / "nested" / "result.txt"

    write_result = await writer.execute(str(target), "verified")
    read_result = await reader.execute(str(target))

    assert write_result["success"] is True
    assert read_result["success"] is True
    assert read_result["content"] == "verified"


@pytest.mark.asyncio
async def test_file_tools_reject_prefix_confusion(tmp_path: Path) -> None:
    """A sibling whose name starts with the allowed path is not inside that path."""
    allowed = tmp_path / "allowed"
    sibling = tmp_path / "allowed-escape" / "secret.txt"
    allowed.mkdir()
    sibling.parent.mkdir()
    sibling.write_text("secret")

    result = await FileReaderTool([str(allowed)]).execute(str(sibling))

    assert result["success"] is False
    assert "Access denied" in result["error"]


@pytest.mark.asyncio
async def test_file_tools_reject_symlink_escape(tmp_path: Path) -> None:
    """Resolving a symlink may not escape the configured workspace."""
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (allowed / "link").symlink_to(outside, target_is_directory=True)

    result = await FileReaderTool([str(allowed)]).execute(str(allowed / "link" / "secret.txt"))

    assert result["success"] is False
    assert "Access denied" in result["error"]


@pytest.mark.asyncio
async def test_calculator_supports_documented_arithmetic() -> None:
    """Evaluate arithmetic, constants, and allowlisted functions."""
    result = await CalculatorTool().execute("round(sqrt(16) + sin(pi / 2), 2)")

    assert result == {
        "success": True,
        "expression": "round(sqrt(16) + sin(pi / 2), 2)",
        "result": 5.0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "(1).__class__.__mro__",
        "open('/etc/passwd').read()",
        "2 ** 1000000",
    ],
)
async def test_calculator_rejects_code_execution_and_resource_abuse(expression: str) -> None:
    """Reject Python object access, arbitrary calls, and extreme exponents."""
    result = await CalculatorTool().execute(expression)

    assert result["success"] is False
