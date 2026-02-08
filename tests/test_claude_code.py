"""Tests for the Claude Code CLI invocation module."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bender.claude_code import (
    ClaudeCodeError,
    ClaudeResponse,
    _parse_response,
    invoke_claude,
)


class TestClaudeResponse:
    """Tests for the ClaudeResponse dataclass."""

    def test_default_values(self) -> None:
        """ClaudeResponse sets is_error to False by default."""
        r = ClaudeResponse(result="hello", session_id="abc-123")
        assert r.result == "hello"
        assert r.session_id == "abc-123"
        assert r.is_error is False

    def test_error_response(self) -> None:
        """ClaudeResponse can represent an error."""
        r = ClaudeResponse(result="failed", session_id="abc-123", is_error=True)
        assert r.is_error is True


class TestParseResponse:
    """Tests for the _parse_response function."""

    def test_valid_json_response(self) -> None:
        """Parses valid JSON with all fields."""
        raw = json.dumps({
            "result": "Hello from Claude",
            "session_id": "session-xyz",
            "is_error": False,
        })
        response = _parse_response(raw, "fallback-id")
        assert response.result == "Hello from Claude"
        assert response.session_id == "session-xyz"
        assert response.is_error is False

    def test_json_with_missing_session_id_uses_fallback(self) -> None:
        """Uses fallback session_id when not in JSON response."""
        raw = json.dumps({"result": "Hello"})
        response = _parse_response(raw, "fallback-id")
        assert response.result == "Hello"
        assert response.session_id == "fallback-id"

    def test_json_with_missing_result_uses_raw(self) -> None:
        """Uses raw output when 'result' key is missing from JSON."""
        raw = json.dumps({"session_id": "s1"})
        response = _parse_response(raw, "fallback-id")
        assert response.result == raw.strip()
        assert response.session_id == "s1"

    def test_json_with_error_flag(self) -> None:
        """Parses is_error flag from JSON."""
        raw = json.dumps({"result": "error occurred", "is_error": True})
        response = _parse_response(raw, "fallback-id")
        assert response.is_error is True

    def test_invalid_json_returns_raw_text(self) -> None:
        """Falls back to raw text when JSON parsing fails."""
        raw = "This is not JSON output"
        response = _parse_response(raw, "fallback-id")
        assert response.result == "This is not JSON output"
        assert response.session_id == "fallback-id"

    def test_empty_string_returns_empty(self) -> None:
        """Handles empty string gracefully."""
        response = _parse_response("", "fallback-id")
        assert response.result == ""
        assert response.session_id == "fallback-id"


class TestInvokeClaude:
    """Tests for the invoke_claude function."""

    async def test_basic_invocation(self, tmp_path: Path) -> None:
        """Invokes Claude Code with correct base arguments."""
        json_output = json.dumps({"result": "response text", "session_id": "s1"})
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(
            return_value=(json_output.encode(), b"")
        )
        mock_process.returncode = 0

        with patch("bender.claude_code.asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            result = await invoke_claude("hello", tmp_path)

        mock_exec.assert_called_once()
        cmd_args = mock_exec.call_args[0]
        assert cmd_args[0] == "claude"
        assert "--print" in cmd_args
        assert "--output-format" in cmd_args
        assert "json" in cmd_args
        assert "--" in cmd_args
        assert "hello" in cmd_args
        assert result.result == "response text"

    async def test_invocation_with_session_id(self, tmp_path: Path) -> None:
        """Passes --session-id when provided."""
        json_output = json.dumps({"result": "ok"})
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(
            return_value=(json_output.encode(), b"")
        )
        mock_process.returncode = 0

        with patch("bender.claude_code.asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await invoke_claude("hello", tmp_path, session_id="my-session")

        cmd_args = mock_exec.call_args[0]
        assert "--session-id" in cmd_args
        assert "my-session" in cmd_args
        assert "--resume" not in cmd_args

    async def test_invocation_with_resume(self, tmp_path: Path) -> None:
        """Passes --resume <session_id> when resume=True."""
        json_output = json.dumps({"result": "resumed"})
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(
            return_value=(json_output.encode(), b"")
        )
        mock_process.returncode = 0

        with patch("bender.claude_code.asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await invoke_claude(
                "continue", tmp_path, session_id="my-session", resume=True
            )

        cmd_args = mock_exec.call_args[0]
        assert "--resume" in cmd_args
        assert "my-session" in cmd_args
        assert "--session-id" not in cmd_args

    async def test_resume_without_session_id_ignored(self, tmp_path: Path) -> None:
        """resume=True without session_id does not add --resume flag."""
        json_output = json.dumps({"result": "ok"})
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(
            return_value=(json_output.encode(), b"")
        )
        mock_process.returncode = 0

        with patch("bender.claude_code.asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await invoke_claude("hello", tmp_path, resume=True)

        cmd_args = mock_exec.call_args[0]
        assert "--resume" not in cmd_args
        assert "--session-id" not in cmd_args

    async def test_workspace_passed_as_cwd(self, tmp_path: Path) -> None:
        """Workspace is passed as cwd to subprocess."""
        json_output = json.dumps({"result": "ok"})
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(
            return_value=(json_output.encode(), b"")
        )
        mock_process.returncode = 0

        with patch("bender.claude_code.asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await invoke_claude("hello", tmp_path)

        assert mock_exec.call_args[1]["cwd"] == tmp_path

    async def test_nonzero_exit_code_raises(self, tmp_path: Path) -> None:
        """Raises ClaudeCodeError on non-zero exit code."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(
            return_value=(b"", b"Something went wrong")
        )
        mock_process.returncode = 1

        with patch("bender.claude_code.asyncio.create_subprocess_exec", return_value=mock_process):
            with pytest.raises(ClaudeCodeError, match="exited with code 1"):
                await invoke_claude("hello", tmp_path)

    async def test_nonzero_exit_code_empty_stderr(self, tmp_path: Path) -> None:
        """Raises ClaudeCodeError with 'Unknown error' when stderr is empty."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        mock_process.returncode = 1

        with patch("bender.claude_code.asyncio.create_subprocess_exec", return_value=mock_process):
            with pytest.raises(ClaudeCodeError, match="Unknown error"):
                await invoke_claude("hello", tmp_path)

    async def test_timeout_raises(self, tmp_path: Path) -> None:
        """Raises ClaudeCodeError when execution times out."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        with patch("bender.claude_code.asyncio.create_subprocess_exec", return_value=mock_process):
            with pytest.raises(ClaudeCodeError, match="timed out"):
                await invoke_claude("hello", tmp_path, timeout=1)

        mock_process.kill.assert_called_once()

    async def test_cli_not_found_raises(self, tmp_path: Path) -> None:
        """Raises ClaudeCodeError when claude CLI is not in PATH."""
        with patch(
            "bender.claude_code.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ):
            with pytest.raises(ClaudeCodeError, match="CLI not found"):
                await invoke_claude("hello", tmp_path)
