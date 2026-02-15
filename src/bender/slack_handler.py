"""Slack event handlers — @mentions and thread replies via slack-bolt."""

import logging
import re
import asyncio
from datetime import datetime

from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from bender.claude_code import ClaudeCodeError, invoke_claude, StreamingProgress
from bender.config import Settings
from bender.job_tracker import JobTracker, JobStatus
from bender.session_manager import SessionManager
from bender.slack_utils import SLACK_MSG_LIMIT, LONG_RESPONSE_THRESHOLD, md_to_mrkdwn, split_text, create_temp_file, process_urls_in_text

logger = logging.getLogger(__name__)


def register_handlers(
    app: AsyncApp,
    settings: Settings,
    sessions: SessionManager,
    job_tracker: JobTracker | None = None,
) -> None:
    """Register Slack event handlers on the bolt app."""

    @app.event("reaction_added")
    async def handle_reaction_added(event: dict) -> None:
        """Ignore reaction_added events — Bender doesn't need to track reactions."""
        pass

    @app.event("reaction_removed")
    async def handle_reaction_removed(event: dict) -> None:
        """Ignore reaction_removed events — Bender doesn't need to track reactions."""
        pass

    @app.event("app_mention")
    async def handle_mention(event: dict, say, client: AsyncWebClient) -> None:
        """Handle new @Bender mentions — create session and invoke Claude Code."""
        text = _strip_mention(event.get("text", ""))
        thread_ts = event.get("ts", "")
        channel = event.get("channel", "")

        if not text.strip():
            await say(text="How can I help?", thread_ts=thread_ts)
            return

        # Process URLs in text to provide context to Claude
        text = await process_urls_in_text(text)

        logger.info("New mention in channel=%s thread=%s", channel, thread_ts)

        # Check if session already exists for this thread
        session_id = await sessions.get_session(thread_ts)
        if not session_id:
            session_id = await sessions.create_session(thread_ts)

        # Create job tracking record
        job_id = None
        existing_job = None
        if job_tracker:
            existing_job = await job_tracker.get_job_by_thread(thread_ts)
            if existing_job:
                # Reuse existing job for this thread (continuing conversation)
                job_id = existing_job["id"]
                logger.info("Resuming existing job %s for thread %s", job_id, thread_ts)
                await job_tracker.update_job(
                    job_id,
                    status=JobStatus.RUNNING,
                    started_at=datetime.utcnow(),
                )
            else:
                job_id = await job_tracker.create_job(
                    thread_ts=thread_ts,
                    channel=channel,
                    message=text,
                    session_id=session_id,
                )
                await job_tracker.update_job(
                    job_id,
                    status=JobStatus.RUNNING,
                    started_at=datetime.utcnow(),
                )

        # Post initial "thinking" message that we'll update with progress
        initial_msg = await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="🤔 Processing your request..."
        )
        progress_ts = initial_msg["ts"]

        # Create progress callback for streaming updates
        async def update_progress(progress: StreamingProgress) -> None:
            await _update_progress_message(client, channel, progress_ts, progress)

            # Also record progress to job tracker
            if job_tracker and job_id:
                if progress.is_thinking:
                    await job_tracker.add_progress_event(
                        job_id, "thinking", "Thinking..."
                    )
                elif progress.tool_name:
                    if progress.tool_status == "running":
                        await job_tracker.add_progress_event(
                            job_id, "tool_start",
                            f"Running tool: {progress.tool_name}",
                            tool_name=progress.tool_name
                        )
                    else:
                        await job_tracker.add_progress_event(
                            job_id, "tool_end",
                            f"Completed tool: {progress.tool_name}",
                            tool_name=progress.tool_name
                        )

        # Create progress callback for periodic updates
        progress_count = 0

        async def send_progress_update():
            nonlocal progress_count
            progress_count += 1
            elapsed_seconds = progress_count * 30
            if elapsed_seconds >= 60:
                minutes = elapsed_seconds // 60
                seconds = elapsed_seconds % 60
                if seconds == 0:
                    status = f"⏳ Working... ({minutes}m)"
                else:
                    status = f"⏳ Working... ({minutes}m {seconds}s)"
            else:
                status = f"⏳ Working... ({elapsed_seconds}s)"
            try:
                await client.chat_update(
                    channel=channel,
                    ts=progress_ts,
                    text=status
                )
            except Exception as e:
                logger.debug("Failed to update progress: %s", e)

            # Record time progress in job tracker
            if job_tracker and job_id:
                await job_tracker.add_progress_event(
                    job_id, "progress", status
                )

        # Send initial progress update immediately
        await send_progress_update()

        try:
            # Use non-streaming version (more reliable)
            response = await invoke_claude(
                    prompt=text,
                    workspace=settings.bender_workspace,
                    session_id=session_id,
                    model=settings.anthropic_model,
                    timeout=settings.claude_timeout,
                )

            logger.info("Claude Code response received (length=%d)", len(response.result))

            # Delete progress message
            try:
                await client.chat_delete(channel=channel, ts=progress_ts)
            except Exception:
                pass  # Ignore if can't delete

            if not response.result or not response.result.strip():
                logger.warning("Claude Code returned empty response")
                await say(text="I received your message but got an empty response. Please try again.", thread_ts=thread_ts)
                # Update job as completed (empty result)
                if job_tracker and job_id:
                    await job_tracker.update_job(
                        job_id,
                        status=JobStatus.COMPLETED,
                        completed_at=datetime.utcnow(),
                        result="",
                    )
                return

            await _post_response(client, channel, say, response.result, thread_ts)

            # Update job as completed
            if job_tracker and job_id:
                # If existing job, append to result; otherwise set result
                if existing_job:
                    await job_tracker.append_to_result(job_id, response.result[:5000])
                    # Also update token stats for this turn
                    await job_tracker.update_job(
                        job_id,
                        status=JobStatus.COMPLETED,
                        completed_at=datetime.utcnow(),
                        input_tokens=getattr(response, 'input_tokens', 0) or 0,
                        output_tokens=getattr(response, 'output_tokens', 0) or 0,
                        total_cost_usd=getattr(response, 'total_cost', 0) or 0,
                    )
                else:
                    await job_tracker.update_job(
                        job_id,
                        status=JobStatus.COMPLETED,
                        completed_at=datetime.utcnow(),
                        result=response.result[:5000],
                        input_tokens=getattr(response, 'input_tokens', 0) or 0,
                        output_tokens=getattr(response, 'output_tokens', 0) or 0,
                        total_cost_usd=getattr(response, 'total_cost', 0) or 0,
                    )
                # Scan for new git commits
                try:
                    await job_tracker.scan_new_commits(
                        settings.bender_workspace,
                        job_id,
                        datetime.utcnow(),
                    )
                except Exception as e:
                    logger.debug("Failed to scan commits: %s", e)
        except ClaudeCodeError as exc:
            logger.error("Claude Code invocation failed: %s", exc)
            # Update progress message with error
            try:
                await client.chat_update(
                    channel=channel,
                    ts=progress_ts,
                    text=f"❌ Error: {exc}"
                )
            except Exception:
                await say(text=f"Sorry, something went wrong: {exc}", thread_ts=thread_ts)
            # Update job as failed
            if job_tracker and job_id:
                await job_tracker.update_job(
                    job_id,
                    status=JobStatus.FAILED,
                    completed_at=datetime.utcnow(),
                    error=str(exc),
                )
        except Exception as exc:
            logger.error("Unexpected error: %s", exc)
            try:
                await client.chat_update(
                    channel=channel,
                    ts=progress_ts,
                    text=f"❌ Unexpected error: {exc}"
                )
            except Exception:
                await say(text=f"Sorry, an unexpected error occurred: {exc}", thread_ts=thread_ts)
            # Update job as failed
            if job_tracker and job_id:
                await job_tracker.update_job(
                    job_id,
                    status=JobStatus.FAILED,
                    completed_at=datetime.utcnow(),
                    error=str(exc),
                )

    @app.event("message")
    async def handle_message(event: dict, say, client: AsyncWebClient) -> None:
        """Handle thread replies — resume existing session if one exists."""
        # Ignore bot messages to avoid loops
        if event.get("bot_id") or event.get("subtype"):
            return

        thread_ts = event.get("thread_ts")
        if not thread_ts:
            # Not a thread reply, ignore
            return

        session_id = await sessions.get_session(thread_ts)
        if not session_id:
            # Thread not tracked by Bender, ignore
            return

        text = _strip_mention(event.get("text", ""))
        if not text.strip():
            return

        # Process URLs in text to provide context to Claude
        text = await process_urls_in_text(text)

        channel = event.get("channel", "")
        logger.info("Thread reply in channel=%s thread=%s", channel, thread_ts)

        # Check if job already exists for this thread
        job_id = None
        existing_job = None
        if job_tracker:
            existing_job = await job_tracker.get_job_by_thread(thread_ts)

        if existing_job:
            # Reuse existing job for this thread (continuing conversation)
            job_id = existing_job["id"]
            logger.info("Resuming existing job %s for thread %s", job_id, thread_ts)
            if job_tracker:
                await job_tracker.update_job(
                    job_id,
                    status=JobStatus.RUNNING,
                    started_at=datetime.utcnow(),
                )
        else:
            # Create new job for this thread
            if job_tracker:
                job_id = await job_tracker.create_job(
                    thread_ts=thread_ts,
                    channel=channel,
                    message=text,
                    session_id=session_id,
                )
                await job_tracker.update_job(
                    job_id,
                    status=JobStatus.RUNNING,
                    started_at=datetime.utcnow(),
                )

        # Post initial "thinking" message that we'll update with progress
        initial_msg = await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="🤔 Processing your request..."
        )
        progress_ts = initial_msg["ts"]

        # Create progress callback for streaming updates
        async def update_progress(progress: StreamingProgress) -> None:
            await _update_progress_message(client, channel, progress_ts, progress)

            # Also record progress to job tracker
            if job_tracker and job_id:
                if progress.is_thinking:
                    await job_tracker.add_progress_event(
                        job_id, "thinking", "Thinking..."
                    )
                elif progress.tool_name:
                    if progress.tool_status == "running":
                        await job_tracker.add_progress_event(
                            job_id, "tool_start",
                            f"Running tool: {progress.tool_name}",
                            tool_name=progress.tool_name
                        )
                    else:
                        await job_tracker.add_progress_event(
                            job_id, "tool_end",
                            f"Completed tool: {progress.tool_name}",
                            tool_name=progress.tool_name
                        )

        # Create progress callback for periodic updates
        progress_count = 0

        async def send_progress_update():
            nonlocal progress_count
            progress_count += 1
            elapsed_seconds = progress_count * 30
            if elapsed_seconds >= 60:
                minutes = elapsed_seconds // 60
                seconds = elapsed_seconds % 60
                if seconds == 0:
                    status = f"⏳ Working... ({minutes}m)"
                else:
                    status = f"⏳ Working... ({minutes}m {seconds}s)"
            else:
                status = f"⏳ Working... ({elapsed_seconds}s)"
            try:
                await client.chat_update(
                    channel=channel,
                    ts=progress_ts,
                    text=status
                )
            except Exception as e:
                logger.debug("Failed to update progress: %s", e)

            # Record time progress in job tracker
            if job_tracker and job_id:
                await job_tracker.add_progress_event(
                    job_id, "progress", status
                )

        # Send initial progress update immediately
        await send_progress_update()

        try:
            # Use non-streaming version (more reliable)
            response = await invoke_claude(
                prompt=text,
                workspace=settings.bender_workspace,
                session_id=session_id,
                resume=True,
                model=settings.anthropic_model,
                timeout=settings.claude_timeout,
            )
            logger.info("Claude Code response received (length=%d)", len(response.result))

            # Delete progress message
            try:
                await client.chat_delete(channel=channel, ts=progress_ts)
            except Exception:
                pass  # Ignore if can't delete

            if not response.result or not response.result.strip():
                logger.warning("Claude Code returned empty response")
                await say(text="I received your message but got an empty response. Please try again.", thread_ts=thread_ts)
                # Update job as completed (empty result)
                if job_tracker and job_id:
                    await job_tracker.update_job(
                        job_id,
                        status=JobStatus.COMPLETED,
                        completed_at=datetime.utcnow(),
                        result="",
                    )
                return

            await _post_response(client, channel, say, response.result, thread_ts)

            # Update job as completed
            if job_tracker and job_id:
                # If existing job, append to result; otherwise set result
                if existing_job:
                    await job_tracker.append_to_result(job_id, response.result[:5000])
                    # Also update token stats for this turn
                    await job_tracker.update_job(
                        job_id,
                        status=JobStatus.COMPLETED,
                        completed_at=datetime.utcnow(),
                        input_tokens=getattr(response, 'input_tokens', 0) or 0,
                        output_tokens=getattr(response, 'output_tokens', 0) or 0,
                        total_cost_usd=getattr(response, 'total_cost', 0) or 0,
                    )
                else:
                    await job_tracker.update_job(
                        job_id,
                        status=JobStatus.COMPLETED,
                        completed_at=datetime.utcnow(),
                        result=response.result[:5000],
                        input_tokens=getattr(response, 'input_tokens', 0) or 0,
                        output_tokens=getattr(response, 'output_tokens', 0) or 0,
                        total_cost_usd=getattr(response, 'total_cost', 0) or 0,
                    )
                # Scan for new git commits
                try:
                    await job_tracker.scan_new_commits(
                        settings.bender_workspace,
                        job_id,
                        datetime.utcnow(),
                    )
                except Exception as e:
                    logger.debug("Failed to scan commits: %s", e)
        except ClaudeCodeError as exc:
            logger.error("Claude Code invocation failed: %s", exc)
            # Update progress message with error
            try:
                await client.chat_update(
                    channel=channel,
                    ts=progress_ts,
                    text=f"❌ Error: {exc}"
                )
            except Exception:
                await say(text=f"Sorry, something went wrong: {exc}", thread_ts=thread_ts)
            # Update job as failed
            if job_tracker and job_id:
                await job_tracker.update_job(
                    job_id,
                    status=JobStatus.FAILED,
                    completed_at=datetime.utcnow(),
                    error=str(exc),
                )
        except Exception as exc:
            logger.error("Unexpected error: %s", exc)
            try:
                await client.chat_update(
                    channel=channel,
                    ts=progress_ts,
                    text=f"❌ Unexpected error: {exc}"
                )
            except Exception:
                await say(text=f"Sorry, an unexpected error occurred: {exc}", thread_ts=thread_ts)
            # Update job as failed
            if job_tracker and job_id:
                await job_tracker.update_job(
                    job_id,
                    status=JobStatus.FAILED,
                    completed_at=datetime.utcnow(),
                    error=str(exc),
                )

    @app.command("/abort")
    async def handle_abort_command(ack, respond, command) -> None:
        """Handle /abort command to abort a session."""
        await ack()

        # Extract thread_ts from command (should be in thread)
        thread_ts = command.get("thread_ts")
        if not thread_ts:
            await respond("This command must be used in a thread.")
            return

        # Try to abort the session
        session_id = await sessions.get_session(thread_ts)
        if not session_id:
            await respond("No active session found for this thread.")
            return

        success = await sessions.abort_session(thread_ts)
        if success:
            await respond(f"Session aborted successfully.")
        else:
            await respond("Failed to abort session.")


def _strip_mention(text: str) -> str:
    """Remove Slack mention tags (<@U...>, <@B...>, <@W...>) from the message text."""
    return re.sub(r"<@[UBW][A-Z0-9]+>", "", text).strip()


async def _update_progress_message(
    client: AsyncWebClient,
    channel: str,
    ts: str,
    progress: StreamingProgress
) -> None:
    """Update the progress message in Slack with current status."""
    # Build status message
    if progress.is_thinking:
        status = "🧠 Thinking..."
    elif progress.tool_name:
        tool_emoji = "🔧"
        if "read" in progress.tool_name.lower():
            tool_emoji = "📖"
        elif "write" in progress.tool_name.lower() or "edit" in progress.tool_name.lower():
            tool_emoji = "✏️"
        elif "bash" in progress.tool_name.lower() or "command" in progress.tool_name.lower():
            tool_emoji = "💻"
        elif "search" in progress.tool_name.lower() or "grep" in progress.tool_name.lower():
            tool_emoji = "🔍"

        if progress.tool_status == "running":
            status = f"{tool_emoji} Running: `{progress.tool_name}`..."
        else:
            status = f"✅ Completed: `{progress.tool_name}`"
    else:
        status = "⏳ Working..."

    try:
        await client.chat_update(
            channel=channel,
            ts=ts,
            text=status
        )
    except Exception as e:
        logger.debug("Failed to update progress message: %s", e)


async def _post_response(client, channel, say, text: str, thread_ts: str) -> None:
    """Post a response in the thread, splitting if it exceeds Slack's limit or uploading as file."""
    text = md_to_mrkdwn(text)

    # For very long responses, upload as a file instead
    if len(text) > LONG_RESPONSE_THRESHOLD:
        logger.info("Response too long (%d chars), uploading as file", len(text))
        try:
            # Create a temporary file with the response
            file_path = create_temp_file(text, "claude-response")

            # Upload file to Slack
            await client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                file=str(file_path),
                initial_comment="Response too long, here it is as a file:"
            )
            logger.info("File uploaded successfully")
            return
        except Exception as e:
            logger.error("Failed to upload file: %s", e)
            # Fall back to splitting the message
            text = text[:LONG_RESPONSE_THRESHOLD] + "\n\n[Response truncated. Full response uploaded as file failed.]"

    # Safety limit: if text is extremely long, truncate it
    MAX_TOTAL_LENGTH = 50000
    if len(text) > MAX_TOTAL_LENGTH:
        logger.warning("Response too long (%d chars), truncating", len(text))
        text = text[:MAX_TOTAL_LENGTH] + "\n\n[Response truncated due to length]"

    if len(text) <= SLACK_MSG_LIMIT:
        await say(text=text, thread_ts=thread_ts)
        return

    chunks = split_text(text, SLACK_MSG_LIMIT)
    for chunk in chunks:
        await say(text=chunk, thread_ts=thread_ts)
