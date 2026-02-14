"""Job tracker — SQLite-based job persistence and monitoring."""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


class JobStatus:
    """Job status constants."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobTracker:
    """SQLite-based job tracker for monitoring job status and metrics."""

    def __init__(self, workspace: Path) -> None:
        """Initialize the job tracker.

        Args:
            workspace: The Bender workspace directory for storing the database.
        """
        self._workspace = workspace
        self._db_path = workspace / ".bender" / "jobs.db"
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Ensure the database is initialized."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            # Create .bender directory if it doesn't exist
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

            # Create database and table
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY,
                        thread_ts TEXT NOT NULL,
                        channel TEXT NOT NULL,
                        message TEXT NOT NULL,
                        status TEXT NOT NULL,
                        session_id TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        started_at TIMESTAMP,
                        completed_at TIMESTAMP,
                        result TEXT,
                        error TEXT,
                        input_tokens INTEGER DEFAULT 0,
                        output_tokens INTEGER DEFAULT 0,
                        total_cost_usd REAL DEFAULT 0,
                        duration_seconds REAL DEFAULT 0,
                        progress TEXT DEFAULT '[]'
                    )
                """)
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS commits (
                        id TEXT PRIMARY KEY,
                        job_id TEXT,
                        hash TEXT NOT NULL,
                        message TEXT,
                        author TEXT,
                        committed_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (job_id) REFERENCES jobs(id)
                    )
                """)
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_commits_job_id ON commits(job_id)"
                )
                await db.commit()

            self._initialized = True
            logger.info("JobTracker initialized at %s", self._db_path)

    async def create_job(
        self,
        thread_ts: str,
        channel: str,
        message: str,
        session_id: str | None = None,
    ) -> str:
        """Create a new job record.

        Args:
            thread_ts: Slack thread timestamp.
            channel: Slack channel ID.
            message: The message/prompt for the job.
            session_id: Optional Claude Code session ID.

        Returns:
            The job ID.
        """
        await self._ensure_initialized()

        job_id = str(uuid.uuid4())

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO jobs (id, thread_ts, channel, message, status, session_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, thread_ts, channel, message, JobStatus.PENDING, session_id),
            )
            await db.commit()

        logger.info("Created job %s for thread %s", job_id, thread_ts)
        return job_id

    async def update_job(
        self,
        job_id: str,
        status: str,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        result: str | None = None,
        error: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_cost_usd: float | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """Update an existing job record.

        Args:
            job_id: The job ID to update.
            status: New status (pending, running, completed, failed).
            started_at: When the job started processing.
            completed_at: When the job completed.
            result: The job result/output.
            error: Error message if failed.
            input_tokens: Number of input tokens used.
            output_tokens: Number of output tokens used.
            total_cost_usd: Total cost in USD.
            duration_seconds: Job duration in seconds.
        """
        await self._ensure_initialized()

        # Build dynamic update query
        updates = ["status = ?"]
        params: list[Any] = [status]

        if started_at is not None:
            updates.append("started_at = ?")
            params.append(started_at.isoformat())

        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at.isoformat())

        if result is not None:
            updates.append("result = ?")
            params.append(result)

        if error is not None:
            updates.append("error = ?")
            params.append(error)

        if input_tokens is not None:
            updates.append("input_tokens = ?")
            params.append(input_tokens)

        if output_tokens is not None:
            updates.append("output_tokens = ?")
            params.append(output_tokens)

        if total_cost_usd is not None:
            updates.append("total_cost_usd = ?")
            params.append(total_cost_usd)

        if duration_seconds is not None:
            updates.append("duration_seconds = ?")
            params.append(duration_seconds)

        params.append(job_id)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await db.commit()

        logger.debug("Updated job %s to status %s", job_id, status)

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Get a job by ID.

        Args:
            job_id: The job ID.

        Returns:
            Job data as a dictionary, or None if not found.
        """
        await self._ensure_initialized()

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_all_jobs(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get all jobs, optionally filtered by status.

        Args:
            status: Optional status filter.
            limit: Maximum number of jobs to return.
            offset: Number of jobs to skip (for pagination).

        Returns:
            List of job dictionaries.
        """
        await self._ensure_initialized()

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            if status:
                async with db.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (status, limit, offset),
                ) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with db.execute(
                    """
                    SELECT * FROM jobs
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ) as cursor:
                    rows = await cursor.fetchall()

            return [dict(row) for row in rows]

    async def get_job_count(self, status: str | None = None) -> int:
        """Get the total count of jobs.

        Args:
            status: Optional status filter.

        Returns:
            Total number of jobs.
        """
        await self._ensure_initialized()

        async with aiosqlite.connect(self._db_path) as db:
            if status:
                async with db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status = ?",
                    (status,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
            else:
                async with db.execute(
                    "SELECT COUNT(*) FROM jobs",
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0

    async def add_progress_event(
        self,
        job_id: str,
        event_type: str,
        message: str,
        tool_name: str | None = None,
        is_thinking: bool = False,
    ) -> None:
        """Add a progress event to a job.

        Args:
            job_id: The job ID.
            event_type: Type of event (thinking, tool_start, tool_end, etc.)
            message: Human-readable message.
            tool_name: Optional tool name if it's a tool event.
            is_thinking: Whether the model is thinking.
        """
        await self._ensure_initialized()

        event = {
            "type": event_type,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            "tool_name": tool_name,
            "is_thinking": is_thinking,
        }

        # Get current progress and append new event
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT progress FROM jobs WHERE id = ?",
                (job_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    progress_list = json.loads(row[0]) if row[0] else []
                    progress_list.append(event)
                    await db.execute(
                        "UPDATE jobs SET progress = ? WHERE id = ?",
                        (json.dumps(progress_list), job_id),
                    )
                    await db.commit()

    async def get_progress(self, job_id: str) -> list[dict]:
        """Get progress events for a job.

        Args:
            job_id: The job ID.

        Returns:
            List of progress events.
        """
        await self._ensure_initialized()

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT progress FROM jobs WHERE id = ?",
                (job_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return json.loads(row[0]) if row[0] else []
                return []

    async def get_monthly_stats(self, months: int = 12) -> list[dict]:
        """Get monthly statistics for jobs.

        Args:
            months: Number of months to look back.

        Returns:
            List of monthly stats with requests and costs.
        """
        await self._ensure_initialized()

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    strftime('%Y-%m', created_at) as month,
                    COUNT(*) as total_requests,
                    COALESCE(SUM(total_cost_usd), 0) as total_cost,
                    COALESCE(SUM(input_tokens), 0) as input_tokens,
                    COALESCE(SUM(output_tokens), 0) as output_tokens,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
                FROM jobs
                WHERE created_at >= date('now', '-' || ? || ' months')
                GROUP BY strftime('%Y-%m', created_at)
                ORDER BY month DESC
                """,
                (months,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def record_commit(
        self,
        job_id: str | None,
        commit_hash: str,
        message: str,
        author: str,
        committed_at: datetime,
    ) -> None:
        """Record a commit made during a job.

        Args:
            job_id: The job ID (optional).
            commit_hash: The git commit hash.
            message: The commit message.
            author: The commit author.
            committed_at: When the commit was made.
        """
        await self._ensure_initialized()

        commit_id = str(uuid.uuid4())

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO commits (id, job_id, hash, message, author, committed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (commit_id, job_id, commit_hash, message, author, committed_at.isoformat()),
            )
            await db.commit()

        logger.info("Recorded commit %s for job %s", commit_hash[:8], job_id)

    async def get_commits(self, limit: int = 50) -> list[dict]:
        """Get recent commits.

        Args:
            limit: Maximum number of commits to return.

        Returns:
            List of commit dictionaries.
        """
        await self._ensure_initialized()

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM commits
                ORDER BY committed_at DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_commits_by_workspace(self, workspace: Path, limit: int = 50) -> list[dict]:
        """Get recent commits from a workspace git repository.

        Args:
            workspace: Path to the workspace.
            limit: Maximum number of commits to return.

        Returns:
            List of commit dictionaries.
        """
        import subprocess

        try:
            result = subprocess.run(
                ["git", "log", f"-{limit}", "--pretty=format:%H|%an|%ae|%at|%s"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                logger.warning("Git log failed for workspace: %s", workspace)
                return []

            commits = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|", 4)
                if len(parts) >= 5:
                    commits.append({
                        "hash": parts[0],
                        "short_hash": parts[0][:8],
                        "author": parts[1],
                        "email": parts[2],
                        "timestamp": int(parts[3]),
                        "message": parts[4],
                    })

            return commits
        except Exception as e:
            logger.warning("Failed to get git commits: %s", e)
            return []

    async def scan_new_commits(
        self,
        workspace: Path,
        job_id: str,
        since_timestamp: datetime,
    ) -> list[dict]:
        """Scan for new commits made after a job started.

        Args:
            workspace: Path to the workspace.
            job_id: The job ID.
            since_timestamp: Only look for commits after this time.

        Returns:
            List of new commit dictionaries.
        """
        import subprocess

        try:
            # Get commits since the job started
            since_str = since_timestamp.strftime("%Y-%m-%d %H:%M:%S")
            result = subprocess.run(
                ["git", "log", "--since", since_str, "--pretty=format:%H|%an|%ae|%at|%s"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return []

            commits = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("|", 4)
                if len(parts) >= 5:
                    commit_data = {
                        "hash": parts[0],
                        "short_hash": parts[0][:8],
                        "author": parts[1],
                        "email": parts[2],
                        "timestamp": int(parts[3]),
                        "message": parts[4],
                    }
                    # Record in database
                    await self.record_commit(
                        job_id=job_id,
                        commit_hash=parts[0],
                        message=parts[4],
                        author=parts[1],
                        committed_at=datetime.fromtimestamp(int(parts[3])),
                    )
                    commits.append(commit_data)

            if commits:
                logger.info("Found %d new commits for job %s", len(commits), job_id)

            return commits
        except Exception as e:
            logger.warning("Failed to scan new commits: %s", e)
            return []
