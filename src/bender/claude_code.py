"""Claude Code CLI invocation — subprocess wrapper for headless mode."""

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator, Callable, Awaitable

logger = logging.getLogger(__name__)

# Default timeout for Claude Code invocations (10 minutes)
DEFAULT_TIMEOUT_SECONDS = 600

# Streaming update interval (seconds) - how often to call the progress callback
STREAMING_UPDATE_INTERVAL = 15


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


@dataclass
class StreamingProgress:
    """Progress information during streaming."""

    current_text: str = ""
    tool_name: str | None = None
    tool_status: str | None = None  # "running", "completed", "error"
    is_thinking: bool = False
    total_cost: float = 0.0


class ClaudeCodeError(Exception):
    """Raised when Claude Code CLI invocation fails."""


async def invoke_claude(
    prompt: str,
    workspace: Path,
    session_id: str | None = None,
    resume: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    model: str | None = None,
    progress_callback: Callable[[], Awaitable[None]] | None = None,
    progress_interval: float = 30.0,
) -> ClaudeResponse:
    """Invoke Claude Code CLI in headless mode via subprocess.

    Args:
        prompt: The message/prompt to send to Claude Code.
        workspace: Working directory where Claude Code runs.
        session_id: Session ID for new or resumed sessions.
        resume: Whether to resume an existing session.
        timeout: Maximum execution time in seconds.
        model: Optional model name (for Ollama or custom Claude models).
        progress_callback: Optional async callback called periodically to indicate progress.
        progress_interval: Seconds between progress callbacks.

    Returns:
        ClaudeResponse with the parsed result.

    Raises:
        ClaudeCodeError: If the CLI invocation fails.
    """
    # Find claude executable
    claude_executable = _find_claude_executable()

    cmd = [claude_executable, "--print", "--verbose", "--output-format", "json"]

    # Add model flag if specified (for Ollama or custom models)
    if model:
        cmd.extend(["--model", model])

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

        # Start progress callback task if provided
        progress_task = None
        if progress_callback:
            async def progress_loop():
                while True:
                    await asyncio.sleep(progress_interval)
                    if progress_callback:
                        await progress_callback()

            progress_task = asyncio.create_task(progress_loop())

        try:
            # If timeout is 0, use None for no timeout
            actual_timeout = timeout if timeout > 0 else None
            if actual_timeout:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=actual_timeout,
                )
            else:
                # No timeout - wait indefinitely
                stdout, stderr = await process.communicate()
        finally:
            # Cancel progress task if running
            if progress_task and not progress_task.done():
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

    except FileNotFoundError as exc:
        raise ClaudeCodeError(
            f"Failed to execute Claude Code CLI at {claude_executable}: {exc}"
        )
    except asyncio.TimeoutError:
        if process is not None:
            process.kill()
            await process.wait()
        # Clean up the session directory if it exists
        if session_id:
            try:
                session_dir = Path.home() / ".claude" / "projects" / session_id
                if session_dir.exists():
                    import shutil
                    shutil.rmtree(session_dir)
                    logger.info("Cleaned up timed out session: %s", session_id)
            except Exception as cleanup_err:
                logger.warning("Failed to clean up session %s: %s", session_id, cleanup_err)
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
        # First try to parse as a single JSON object
        data = json.loads(raw_output)

        # Handle case where response is a list of events
        if isinstance(data, list):
            logger.debug("Claude Code response is a list with %d events", len(data))
            # Find the "result" event which contains the final response
            final_result = ""
            returned_session_id = session_id
            is_error = False

            for event in data:
                if isinstance(event, dict):
                    event_type = event.get("type") or event.get("subtype")
                    # Look for the result event
                    if event_type == "result":
                        final_result = event.get("result", "")
                        returned_session_id = event.get("session_id", returned_session_id)
                        is_error = event.get("is_error", False)
                    # Also check for success subtype
                    elif event.get("subtype") == "success":
                        final_result = event.get("result", final_result)

            if final_result:
                logger.debug("Found result in event list (length=%d)", len(final_result))
                return ClaudeResponse(
                    result=final_result,
                    session_id=returned_session_id,
                    is_error=is_error,
                )

            # If no result found in events, extract text from assistant messages
            for event in data:
                if isinstance(event, dict):
                    if event.get("type") == "assistant":
                        msg = event.get("message", {})
                        content = msg.get("content", [])
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                if text:
                                    logger.debug("Found text in assistant message (length=%d)", len(text))
                                    return ClaudeResponse(
                                        result=text,
                                        session_id=returned_session_id,
                                        is_error=False,
                                    )

            # Fallback: return raw text if no result found
            logger.warning("No result found in event list, using raw text")
            return ClaudeResponse(result=raw_output.strip(), session_id=session_id)

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


async def invoke_claude_streaming(
    prompt: str,
    workspace: Path,
    session_id: str | None = None,
    resume: bool = False,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    progress_callback: Callable[[StreamingProgress], Awaitable[None]] | None = None,
    update_interval: float = STREAMING_UPDATE_INTERVAL,
) -> ClaudeResponse:
    """Invoke Claude Code CLI with streaming output.

    This version uses --output-format stream-json to get real-time updates
    and calls the progress_callback periodically with status updates.

    Args:
        prompt: The message/prompt to send to Claude Code.
        workspace: Working directory where Claude Code runs.
        session_id: Session ID for new or resumed sessions.
        resume: Whether to resume an existing session.
        model: Optional model name (for Ollama or custom Claude models).
        timeout: Maximum execution time in seconds.
        progress_callback: Async function called with progress updates.
        update_interval: Seconds between progress callback invocations.

    Returns:
        ClaudeResponse with the final result.

    Raises:
        ClaudeCodeError: If the CLI invocation fails.
    """
    claude_executable = _find_claude_executable()

    cmd = [claude_executable, "--verbose", "--output-format", "stream-json"]

    if model:
        cmd.extend(["--model", model])

    if resume and session_id:
        cmd.extend(["--resume", session_id])
    elif session_id:
        cmd.extend(["--session-id", session_id])

    cmd.extend(["--", prompt])

    logger.info(
        "Invoking Claude Code streaming (session=%s, resume=%s, workspace=%s)",
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
    except FileNotFoundError as exc:
        raise ClaudeCodeError(
            f"Failed to execute Claude Code CLI at {claude_executable}: {exc}"
        )

    # Track progress state
    progress = StreamingProgress()
    final_result = ""
    returned_session_id = session_id or ""
    last_update_time = asyncio.get_event_loop().time()

    async def maybe_send_update(force: bool = False) -> None:
        """Send progress update if enough time has passed."""
        nonlocal last_update_time
        if progress_callback is None:
            return
        current_time = asyncio.get_event_loop().time()
        if force or (current_time - last_update_time) >= update_interval:
            await progress_callback(progress)
            last_update_time = current_time

    try:
        # Read stdout line by line for streaming updates
        line_count = 0

        # Set up timeout if needed
        actual_timeout = timeout if timeout > 0 else None
        read_task = None

        async def read_stream():
            nonlocal line_count
            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                line_str = line.decode().strip()
                line_count += 1

                if not line_str:
                    continue

                # Log first few lines for debugging
                if line_count <= 5:
                    logger.debug("Stream line %d: %s", line_count, line_str[:200])

                try:
                    event = json.loads(line_str)
                    event_type = event.get("type", "")

                    # Handle different event types from stream-json
                    if event_type == "assistant":
                        message = event.get("message", {})
                        content = message.get("content", [])
                        for block in content:
                            if block.get("type") == "text":
                                progress.current_text = block.get("text", "")
                                final_result = progress.current_text
                        progress.is_thinking = False
                        progress.tool_name = None
                        await maybe_send_update()

                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            progress.current_text += text
                            final_result = progress.current_text

                    elif event_type == "tool_use":
                        tool_name = event.get("tool", {}).get("name", "unknown")
                        progress.tool_name = tool_name
                        progress.tool_status = "running"
                        progress.is_thinking = False
                        logger.debug("Tool started: %s", tool_name)
                        await maybe_send_update(force=True)

                    elif event_type == "tool_result":
                        progress.tool_status = "completed"
                        await maybe_send_update(force=True)

                    elif event_type == "thinking":
                        progress.is_thinking = True
                        progress.tool_name = None
                        await maybe_send_update()

                    elif event_type == "result":
                        final_result = event.get("result", final_result)
                        returned_session_id = event.get("session_id", returned_session_id)
                        progress.total_cost = event.get("total_cost_usd", 0.0)

                    elif event_type == "error":
                        error_msg = event.get("error", {}).get("message", "Unknown error")
                        raise ClaudeCodeError(f"Claude Code error: {error_msg}")

                except json.JSONDecodeError:
                    logger.debug("Non-JSON streaming line: %s", line_str[:100])
                except Exception as parse_err:
                    logger.error("Error parsing streaming line: %s | Line: %s", parse_err, line_str[:200])
                    raise

        # Start reading with optional timeout
        if actual_timeout:
            read_task = asyncio.create_task(read_stream())
            try:
                await asyncio.wait_for(read_task, timeout=actual_timeout)
            except asyncio.TimeoutError:
                read_task.cancel()
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                # Clean up session directory
                if session_id:
                    try:
                        session_dir = Path.home() / ".claude" / "projects" / session_id
                        if session_dir.exists():
                            import shutil
                            shutil.rmtree(session_dir)
                            logger.info("Cleaned up timed out session: %s", session_id)
                    except Exception as cleanup_err:
                        logger.warning("Failed to clean up session %s: %s", session_id, cleanup_err)
                raise ClaudeCodeError(f"Claude Code timed out after {timeout}s")
        else:
            await read_stream()

        # Wait for process to complete
        await process.wait()

        # Check exit code
        if process.returncode != 0:
            stderr = await process.stderr.read()
            error_msg = stderr.decode().strip() if stderr else "Unknown error"
            logger.error("Claude Code streaming failed (exit=%d): %s", process.returncode, error_msg)
            raise ClaudeCodeError(f"Claude Code exited with code {process.returncode}: {error_msg}")

        # Send final update
        progress.tool_name = None
        progress.tool_status = None
        progress.is_thinking = False
        await maybe_send_update(force=True)

        logger.info("Claude Code streaming completed (result length=%d)", len(final_result))

        return ClaudeResponse(
            result=final_result,
            session_id=returned_session_id,
            is_error=False,
        )

    except Exception as exc:
        # Kill process on any error
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        if isinstance(exc, ClaudeCodeError):
            raise
        raise ClaudeCodeError(f"Streaming invocation failed: {exc}")
