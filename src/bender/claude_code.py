"""Claude Code CLI invocation — subprocess wrapper for headless mode."""

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Default timeout for Claude Code invocations (5 minutes)
DEFAULT_TIMEOUT_SECONDS = 300


def _find_claude_executable() -> str:
    """Find the Claude Code executable, checking common locations if not in PATH.

    Returns:
        Path to the claude executable.

    Raises:
        ClaudeCodeError: If claude executable cannot be found.
    """
    # First try shutil.which (checks PATH)
    claude_path = shutil.which("claude")
    if claude_path:
        logger.debug("Found claude in PATH: %s", claude_path)
        return claude_path

    # On Windows, try common npm global installation paths
    if os.name == "nt":
        common_paths = [
            os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd"),
            os.path.join(os.environ.get("ProgramFiles", ""), "nodejs", "claude.cmd"),
        ]
        for path in common_paths:
            if os.path.isfile(path):
                logger.debug("Found claude at: %s", path)
                return path

    # On Unix, try common npm global installation paths
    else:
        home = os.path.expanduser("~")
        common_paths = [
            "/usr/local/bin/claude",
            os.path.join(home, ".npm-global", "bin", "claude"),
            os.path.join(home, ".local", "bin", "claude"),
        ]
        for path in common_paths:
            if os.path.isfile(path):
                logger.debug("Found claude at: %s", path)
                return path

    raise ClaudeCodeError(
        "Claude Code CLI not found. Ensure 'claude' is installed and in PATH. "
        "Install with: npm install -g @anthropic-ai/claude-code"
    )


@dataclass
class ClaudeResponse:
    """Parsed response from Claude Code CLI."""

    result: str
    session_id: str
    is_error: bool = False


class ClaudeCodeError(Exception):
    """Raised when Claude Code CLI invocation fails."""


async def invoke_claude(
    prompt: str,
    workspace: Path,
    session_id: str | None = None,
    resume: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> ClaudeResponse:
    """Invoke Claude Code CLI in headless mode via subprocess.

    Args:
        prompt: The message/prompt to send to Claude Code.
        workspace: Working directory where Claude Code runs.
        session_id: Session ID for new or resumed sessions.
        resume: Whether to resume an existing session.
        timeout: Maximum execution time in seconds.

    Returns:
        ClaudeResponse with the parsed result.

    Raises:
        ClaudeCodeError: If the CLI invocation fails.
    """
    # Find claude executable
    claude_executable = _find_claude_executable()

    cmd = [claude_executable, "--print", "--output-format", "json"]

    if resume and session_id:
        cmd.extend(["--resume", session_id])
    elif session_id:
        cmd.extend(["--session-id", session_id])

    cmd.extend(["--", prompt])

    logger.info(
        "Invoking Claude Code (session=%s, resume=%s, workspace=%s)",
        session_id,
        resume,
        workspace,
    )

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ClaudeCodeError(
            f"Failed to execute Claude Code CLI at {claude_executable}: {exc}"
        )
    except asyncio.TimeoutError:
        if process is not None:
            process.kill()
            await process.wait()
        raise ClaudeCodeError(f"Claude Code timed out after {timeout}s")

    if process.returncode != 0:
        error_msg = stderr.decode().strip() if stderr else "Unknown error"
        logger.error("Claude Code failed (exit=%d): %s", process.returncode, error_msg)
        raise ClaudeCodeError(f"Claude Code exited with code {process.returncode}: {error_msg}")

    stdout_str = stdout.decode()
    stderr_str = stderr.decode() if stderr else ""

    logger.debug("Claude Code stdout length: %d", len(stdout_str))
    if stderr_str:
        logger.warning("Claude Code stderr: %s", stderr_str[:500])

    return _parse_response(stdout_str, session_id or "")


def _parse_response(raw_output: str, session_id: str) -> ClaudeResponse:
    """Parse JSON output from Claude Code CLI."""
    logger.debug("Parsing response (length=%d)", len(raw_output))

    try:
        data = json.loads(raw_output)
        logger.debug("Parsed JSON keys: %s", list(data.keys()))
    except json.JSONDecodeError as exc:
        # If output is not valid JSON, treat the raw text as the result
        logger.warning("Claude Code output is not valid JSON: %s. Using raw text (first 200 chars): %s",
                      exc, raw_output[:200])
        return ClaudeResponse(result=raw_output.strip(), session_id=session_id)

    # Claude Code --print --output-format json returns a structured response
    result = data.get("result", raw_output.strip())
    returned_session_id = data.get("session_id", session_id)
    is_error = data.get("is_error", False)

    logger.debug("Parsed result length: %d, session_id: %s, is_error: %s",
                 len(result) if result else 0, returned_session_id, is_error)

    return ClaudeResponse(
        result=result,
        session_id=returned_session_id,
        is_error=is_error,
    )
