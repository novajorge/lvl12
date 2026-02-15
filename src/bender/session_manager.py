"""Session manager — maps Slack threads to Claude Code sessions."""

import logging
import uuid
from asyncio import Lock
from pathlib import Path

logger = logging.getLogger(__name__)


class SessionManager:
    """Thread-safe mapping between Slack thread timestamps and Claude Code session IDs.

    Each Slack thread maps to exactly one Claude Code session,
    enabling multi-turn conversations with context preserved.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}
        self._lock = Lock()

    async def list_sessions(self) -> list[dict]:
        """List all active sessions.

        Returns:
            List of session info with thread_ts and session_id.
        """
        async with self._lock:
            return [
                {"thread_ts": ts, "session_id": sid}
                for ts, sid in self._sessions.items()
            ]

    async def remove_session(self, thread_ts: str) -> bool:
        """Remove a session from tracking.

        Args:
            thread_ts: The Slack thread timestamp.

        Returns:
            True if session was removed, False if not found.
        """
        async with self._lock:
            if thread_ts in self._sessions:
                del self._sessions[thread_ts]
                return True
            return False

    async def abort_session(self, thread_ts: str) -> bool:
        """Abort a session by killing the Claude session directory.

        Args:
            thread_ts: The Slack thread timestamp.

        Returns:
            True if session was aborted, False if not found.
        """
        session_id = None
        async with self._lock:
            session_id = self._sessions.get(thread_ts)

        if session_id:
            try:
                session_dir = Path.home() / ".claude" / "projects" / session_id
                if session_dir.exists():
                    import shutil
                    shutil.rmtree(session_dir)
                    logger.info("Aborted session %s for thread %s", session_id, thread_ts)
                    # Remove from tracking
                    await self.remove_session(thread_ts)
                    return True
            except Exception as e:
                logger.warning("Failed to abort session %s: %s", session_id, e)

        return False

    async def create_session(self, thread_ts: str) -> str:
        """Create a new session for a Slack thread.

        Args:
            thread_ts: The Slack thread timestamp identifier.

        Returns:
            The newly generated session ID.
        """
        session_id = str(uuid.uuid4())
        async with self._lock:
            self._sessions[thread_ts] = session_id
        logger.info("Created session %s for thread %s", session_id, thread_ts)
        return session_id

    async def get_session(self, thread_ts: str) -> str | None:
        """Get the session ID for a Slack thread, if one exists.

        Args:
            thread_ts: The Slack thread timestamp identifier.

        Returns:
            The session ID, or None if no session exists for this thread.
        """
        async with self._lock:
            return self._sessions.get(thread_ts)

    async def has_session(self, thread_ts: str) -> bool:
        """Check whether a Slack thread has an existing session.

        Args:
            thread_ts: The Slack thread timestamp identifier.

        Returns:
            True if the thread has an associated session.
        """
        async with self._lock:
            return thread_ts in self._sessions

    async def set_session(self, thread_ts: str, session_id: str) -> None:
        """Explicitly set the session ID for a thread (e.g., from API-created sessions).

        Args:
            thread_ts: The Slack thread timestamp identifier.
            session_id: The Claude Code session ID to associate.
        """
        async with self._lock:
            self._sessions[thread_ts] = session_id
        logger.info("Set session %s for thread %s", session_id, thread_ts)
