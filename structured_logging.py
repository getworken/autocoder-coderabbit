"""
Structured Logging Module
=========================

Enhanced logging with structured JSON format, filtering, and export capabilities.

Features:
- JSON-formatted logs with consistent schema
- Filter by agent, feature, level
- Full-text search
- Timeline view for agent activity
- Export logs for offline analysis

Log Format:
{
    "timestamp": "2025-01-21T10:30:00.000Z",
    "level": "info|warn|error",
    "agent_id": "coding-42",
    "feature_id": 42,
    "tool_name": "feature_mark_passing",
    "duration_ms": 150,
    "message": "Feature marked as passing"
}
"""

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

# Type aliases
LogLevel = Literal["debug", "info", "warn", "error"]


@dataclass
class StructuredLogEntry:
    """A structured log entry with all metadata."""

    timestamp: str
    level: LogLevel
    message: str
    agent_id: Optional[str] = None
    feature_id: Optional[int] = None
    tool_name: Optional[str] = None
    duration_ms: Optional[int] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """
        Build a dictionary representation of the log entry, omitting fields that are unset or empty.
        
        Returns:
            dict: Mapping with keys 'timestamp', 'level', and 'message', plus any of 'agent_id', 'feature_id', 'tool_name', 'duration_ms', and 'extra' when those fields are present.
        """
        result = {
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
        }
        if self.agent_id:
            result["agent_id"] = self.agent_id
        if self.feature_id is not None:
            result["feature_id"] = self.feature_id
        if self.tool_name:
            result["tool_name"] = self.tool_name
        if self.duration_ms is not None:
            result["duration_ms"] = self.duration_ms
        if self.extra:
            result["extra"] = self.extra
        return result

    def to_json(self) -> str:
        """
        Return a JSON string representing the structured log entry.
        
        Returns:
            json_str (str): JSON-encoded object containing the entry's fields (timestamp, level, message, and any present metadata such as `agent_id`, `feature_id`, `tool_name`, `duration_ms`, and `extra`).
        """
        return json.dumps(self.to_dict())


class StructuredLogHandler(logging.Handler):
    """
    Custom logging handler that stores structured logs in SQLite.

    Thread-safe for concurrent agent logging.
    """

    def __init__(
        self,
        db_path: Path,
        agent_id: Optional[str] = None,
        max_entries: int = 10000,
    ):
        """
        Initialize the StructuredLogHandler and ensure the SQLite backing store is ready.
        
        Parameters:
            db_path (Path): Filesystem path to the SQLite database used to persist logs. The handler will create parent directories and initialize the database schema if needed.
            agent_id (Optional[str]): Optional default agent identifier to attach to emitted log entries when a record does not supply one.
            max_entries (int): Maximum number of log rows to retain in the database; older entries will be evicted when this limit is exceeded.
        """
        super().__init__()
        self.db_path = db_path
        self.agent_id = agent_id
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._init_database()

    def _init_database(self) -> None:
        """
        Initialize the on-disk SQLite logs database and ensure required schema and indexes exist.
        
        This method acquires the handler's internal lock, opens (or creates) the SQLite file at self.db_path, enables WAL journaling for concurrent readers/writers, creates the `logs` table with columns for structured log fields, and adds indexes on timestamp, level, agent_id, and feature_id. Commits changes and closes the connection.
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Enable WAL mode for better concurrency with parallel agents
            # WAL allows readers and writers to work concurrently without blocking
            cursor.execute("PRAGMA journal_mode=WAL")

            # Create logs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    agent_id TEXT,
                    feature_id INTEGER,
                    tool_name TEXT,
                    duration_ms INTEGER,
                    extra TEXT
                )
            """)

            # Create indexes for common queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_logs_timestamp
                ON logs(timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_logs_level
                ON logs(level)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_logs_agent_id
                ON logs(agent_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_logs_feature_id
                ON logs(feature_id)
            """)

            conn.commit()
            conn.close()

    def emit(self, record: logging.LogRecord) -> None:
        """Store a log record in the database."""
        try:
            # Extract structured data from record
            entry = StructuredLogEntry(
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                level=record.levelname.lower(),
                message=self.format(record),
                agent_id=getattr(record, "agent_id", self.agent_id),
                feature_id=getattr(record, "feature_id", None),
                tool_name=getattr(record, "tool_name", None),
                duration_ms=getattr(record, "duration_ms", None),
                extra=getattr(record, "extra", {}),
            )

            with self._lock:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()

                cursor.execute(
                    """
                    INSERT INTO logs
                    (timestamp, level, message, agent_id, feature_id, tool_name, duration_ms, extra)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.timestamp,
                        entry.level,
                        entry.message,
                        entry.agent_id,
                        entry.feature_id,
                        entry.tool_name,
                        entry.duration_ms,
                        json.dumps(entry.extra) if entry.extra else None,
                    ),
                )

                # Cleanup old entries if over limit
                cursor.execute("SELECT COUNT(*) FROM logs")
                count = cursor.fetchone()[0]
                if count > self.max_entries:
                    delete_count = count - self.max_entries
                    cursor.execute(
                        """
                        DELETE FROM logs WHERE id IN (
                            SELECT id FROM logs ORDER BY timestamp ASC LIMIT ?
                        )
                        """,
                        (delete_count,),
                    )

                conn.commit()
                conn.close()

        except Exception:
            self.handleError(record)


class StructuredLogger:
    """
    Enhanced logger with structured logging capabilities.

    Usage:
        logger = StructuredLogger(project_dir, agent_id="coding-1")
        logger.info("Starting feature", feature_id=42)
        logger.error("Test failed", feature_id=42, tool_name="playwright")
    """

    def __init__(
        self,
        project_dir: Path,
        agent_id: Optional[str] = None,
        console_output: bool = True,
    ):
        """
        Initialize the StructuredLogger for a project by preparing the project's SQLite logs database and attaching logging handlers.
        
        Parameters:
            project_dir (Path): Root directory of the project; a database will be created at `<project_dir>/.autocoder/logs.db`.
            agent_id (Optional[str]): Optional agent identifier to tag emitted logs and associate the DB handler with a specific agent.
            console_output (bool): If True, attach a human-readable console StreamHandler in addition to the database-backed StructuredLogHandler.
        
        Side effects:
            Ensures the `.autocoder` directory exists, creates/opens the SQLite log database, and configures an internal logger with a StructuredLogHandler and optional console handler.
        """
        self.project_dir = Path(project_dir)
        self.agent_id = agent_id
        self.db_path = self.project_dir / ".autocoder" / "logs.db"

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Setup logger with unique name per instance to avoid handler accumulation
        # across tests and multiple invocations. Include project path hash for uniqueness.
        import hashlib
        path_hash = hashlib.md5(str(self.project_dir).encode()).hexdigest()[:8]
        logger_name = f"autocoder.{agent_id or 'main'}.{path_hash}.{id(self)}"
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.DEBUG)

        # Clear existing handlers (for safety, though names should be unique)
        self.logger.handlers.clear()

        # Add structured handler
        self.handler = StructuredLogHandler(self.db_path, agent_id)
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(self.handler)

        # Add console handler if requested
        if console_output:
            console = logging.StreamHandler()
            console.setLevel(logging.INFO)
            console.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            )
            self.logger.addHandler(console)

    def _log(
        self,
        level: str,
        message: str,
        feature_id: Optional[int] = None,
        tool_name: Optional[str] = None,
        duration_ms: Optional[int] = None,
        **extra,
    ) -> None:
        """
        Log a message with structured metadata attached.
        
        Parameters:
            level (str): Log level name (e.g., "debug", "info", "warn", "error") used to select the logger method.
            message (str): Human-readable log message.
            feature_id (Optional[int]): Optional numeric identifier for a feature or operation.
            tool_name (Optional[str]): Optional name of a tool or subsystem associated with the log.
            duration_ms (Optional[int]): Optional duration in milliseconds related to the logged event.
            **extra: Additional key/value pairs to include in the structured `extra` payload.
        """
        record_extra = {
            "agent_id": self.agent_id,
            "feature_id": feature_id,
            "tool_name": tool_name,
            "duration_ms": duration_ms,
            "extra": extra,
        }

        # Use LogRecord extras
        getattr(self.logger, level)(
            message,
            extra=record_extra,
        )

    def debug(self, message: str, **kwargs) -> None:
        """
        Log a message at the debug level with optional structured metadata.
        
        Parameters:
            message (str): Human-readable log message.
            **kwargs: Optional structured fields to attach to the log entry. Recognized keys include
                `agent_id` (str), `feature_id` (int), `tool_name` (str), `duration_ms` (int), and any
                additional keys which will be stored under the entry's `extra` payload.
        """
        self._log("debug", message, **kwargs)

    def info(self, message: str, **kwargs) -> None:
        """
        Log an informational structured message for this logger.
        
        Parameters:
            message (str): Human-readable message to record.
            **kwargs: Optional structured metadata to attach to the log. Recognized keys:
                - feature_id (int): Identifier for the feature or step.
                - tool_name (str): Name of the tool or subsystem.
                - duration_ms (int): Duration in milliseconds associated with the event.
                - Any other keys will be included in the log's `extra` payload.
        """
        self._log("info", message, **kwargs)

    def warn(self, message: str, **kwargs) -> None:
        """
        Log a message at the warning level.
        
        Parameters:
        	message (str): The message to log.
        	**kwargs: Optional structured fields forwarded to the logger (e.g., agent_id, feature_id, tool_name, duration_ms, extra).
        """
        self._log("warning", message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        """
        Log a warning-level message.
        
        Parameters:
            message (str): Human-readable log message.
            **kwargs: Optional structured fields to include with the log entry (e.g. `agent_id`, `feature_id`, `tool_name`, `duration_ms`, `extra`).
        """
        self._log("warning", message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        """
        Log a message at the error level with optional structured metadata.
        
        Parameters:
        	message (str): Human-readable log message.
        	feature_id (Optional[int], in kwargs): Identifier for the feature emitting the log.
        	tool_name (Optional[str], in kwargs): Name of the tool related to this log.
        	duration_ms (Optional[int], in kwargs): Duration in milliseconds associated with the event.
        	extra (dict, in kwargs): Arbitrary additional payload to include with the log.
        """
        self._log("error", message, **kwargs)


class LogQuery:
    """
    Query interface for structured logs.

    Supports filtering, searching, and aggregation.
    """

    def __init__(self, db_path: Path):
        """
        Initialize the LogQuery bound to a specific SQLite logs database.
        
        Parameters:
            db_path (Path): Filesystem path to the SQLite logs database used for queries.
        """
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        """
        Open a new SQLite connection to the configured logs database with rows returned as sqlite3.Row.
        
        Returns:
            sqlite3.Connection: A new SQLite connection to self.db_path with `row_factory` set to `sqlite3.Row`.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def query(
        self,
        level: Optional[LogLevel] = None,
        agent_id: Optional[str] = None,
        feature_id: Optional[int] = None,
        tool_name: Optional[str] = None,
        search: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """
        Query structured logs using optional filters and pagination.
        
        Filters may restrict results by level, agent_id, feature_id, tool_name, a substring search of the message, and an inclusive time range. Results are ordered by timestamp descending.
        
        Parameters:
            level (Optional[LogLevel]): Only include logs with this level.
            agent_id (Optional[str]): Only include logs for this agent ID.
            feature_id (Optional[int]): Only include logs for this feature ID.
            tool_name (Optional[str]): Only include logs for this tool name.
            search (Optional[str]): Substring to match in the `message` field.
            since (Optional[datetime]): Include logs whose timestamp is >= this datetime.
            until (Optional[datetime]): Include logs whose timestamp is <= this datetime.
            limit (int): Maximum number of entries to return.
            offset (int): Number of entries to skip (for pagination).
        
        Returns:
            list[dict]: Matching log rows as dictionaries with keys corresponding to the logs table (`id`, `timestamp`, `level`, `message`, `agent_id`, `feature_id`, `tool_name`, `duration_ms`, `extra`).
        """
        conn = self._connect()
        cursor = conn.cursor()

        conditions = []
        params = []

        if level:
            conditions.append("level = ?")
            params.append(level)

        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)

        if feature_id is not None:
            conditions.append("feature_id = ?")
            params.append(feature_id)

        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)

        if search:
            conditions.append("message LIKE ?")
            params.append(f"%{search}%")

        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())

        if until:
            conditions.append("timestamp <= ?")
            params.append(until.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT * FROM logs
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def count(
        self,
        level: Optional[LogLevel] = None,
        agent_id: Optional[str] = None,
        feature_id: Optional[int] = None,
        since: Optional[datetime] = None,
    ) -> int:
        """
        Compute the number of log entries that match the provided filters.
        
        Parameters:
            level (Optional[LogLevel]): If provided, only count entries with this log level.
            agent_id (Optional[str]): If provided, only count entries for this agent_id.
            feature_id (Optional[int]): If provided, only count entries with this feature_id.
            since (Optional[datetime]): If provided, only count entries with timestamp greater than or equal to this value.
        
        Returns:
            int: Number of matching log entries.
        """
        conn = self._connect()
        cursor = conn.cursor()

        conditions = []
        params = []

        if level:
            conditions.append("level = ?")
            params.append(level)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if feature_id is not None:
            conditions.append("feature_id = ?")
            params.append(feature_id)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        cursor.execute(f"SELECT COUNT(*) FROM logs WHERE {where_clause}", params)
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def get_timeline(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        bucket_minutes: int = 5,
    ) -> list[dict]:
        """
        Builds a time-bucketed activity timeline of logs.
        
        Each returned bucket represents a time interval (aligned to `bucket_minutes`) and aggregates per-agent log counts and error counts.
        
        Parameters:
            since (Optional[datetime]): Start of the time range; defaults to 24 hours before now if omitted.
            until (Optional[datetime]): End of the time range; defaults to now if omitted.
            bucket_minutes (int): Length of each time bucket in minutes; buckets are aligned to minute boundaries (e.g., for 5 minutes: 00:00–00:04, 00:05–00:09).
        
        Returns:
            list[dict]: A list of buckets ordered by time. Each bucket is a dict with:
                - "timestamp": bucket start as an ISO-like string (YYYY-MM-DD HH:MM:00),
                - "agents": mapping of agent_id (or "main" when null) to count of logs in that bucket,
                - "total": total number of logs across all agents in the bucket,
                - "errors": number of logs with level "error" in the bucket.
        """
        conn = self._connect()
        cursor = conn.cursor()

        # Default to last 24 hours
        if not since:
            since = datetime.utcnow() - timedelta(hours=24)
        if not until:
            until = datetime.utcnow()

        cursor.execute(
            """
            SELECT
                strftime('%Y-%m-%d %H:', timestamp) ||
                printf('%02d', (CAST(strftime('%M', timestamp) AS INTEGER) / ?) * ?) || ':00' as bucket,
                agent_id,
                COUNT(*) as count,
                SUM(CASE WHEN level = 'error' THEN 1 ELSE 0 END) as errors
            FROM logs
            WHERE timestamp >= ? AND timestamp <= ?
            GROUP BY bucket, agent_id
            ORDER BY bucket
            """,
            (bucket_minutes, bucket_minutes, since.isoformat(), until.isoformat()),
        )

        rows = cursor.fetchall()
        conn.close()

        # Group by bucket
        buckets = {}
        for row in rows:
            bucket = row["bucket"]
            if bucket not in buckets:
                buckets[bucket] = {"timestamp": bucket, "agents": {}, "total": 0, "errors": 0}
            agent = row["agent_id"] or "main"
            buckets[bucket]["agents"][agent] = row["count"]
            buckets[bucket]["total"] += row["count"]
            buckets[bucket]["errors"] += row["errors"]

        return list(buckets.values())

    def get_agent_stats(self, since: Optional[datetime] = None) -> list[dict]:
        """
        Compute per-agent log statistics optionally restricted to logs at or after `since`.
        
        Parameters:
            since (Optional[datetime]): If provided, only logs with timestamp >= `since` are included.
        
        Returns:
            list[dict]: A list of per-agent statistics dictionaries sorted by total descending. Each dictionary contains:
                - agent_id (Optional[str]): The agent identifier (may be NULL in the DB).
                - total (int): Total number of logs for the agent.
                - info_count (int): Number of logs with level "info".
                - warn_count (int): Number of logs with level "warn" or "warning".
                - error_count (int): Number of logs with level "error".
                - first_log (str): ISO-formatted timestamp of the agent's earliest matching log.
                - last_log (str): ISO-formatted timestamp of the agent's latest matching log.
        """
        conn = self._connect()
        cursor = conn.cursor()

        params = []
        where_clause = "1=1"
        if since:
            where_clause = "timestamp >= ?"
            params.append(since.isoformat())

        cursor.execute(
            f"""
            SELECT
                agent_id,
                COUNT(*) as total,
                SUM(CASE WHEN level = 'info' THEN 1 ELSE 0 END) as info_count,
                SUM(CASE WHEN level = 'warn' OR level = 'warning' THEN 1 ELSE 0 END) as warn_count,
                SUM(CASE WHEN level = 'error' THEN 1 ELSE 0 END) as error_count,
                MIN(timestamp) as first_log,
                MAX(timestamp) as last_log
            FROM logs
            WHERE {where_clause}
            GROUP BY agent_id
            ORDER BY total DESC
            """,
            params,
        )

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def export_logs(
        self,
        output_path: Path,
        format: Literal["json", "jsonl", "csv"] = "jsonl",
        **filters,
    ) -> int:
        """
        Export logs matching the provided filters to a file in the specified format.
        
        Parameters:
            output_path (Path): Destination file path for the exported logs.
            format (str): Output format; one of "json", "jsonl", or "csv".
            **filters: Keyword filters to select logs to export (e.g., level, agent_id, feature_id, tool_name, since, until, search).
        
        Returns:
            int: Number of log entries written to the file.
        """
        # Get all matching logs
        logs = self.query(limit=1000000, **filters)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if format == "json":
            with open(output_path, "w") as f:
                json.dump(logs, f, indent=2)

        elif format == "jsonl":
            with open(output_path, "w") as f:
                for log in logs:
                    f.write(json.dumps(log) + "\n")

        elif format == "csv":
            import csv

            if logs:
                with open(output_path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=logs[0].keys())
                    writer.writeheader()
                    writer.writerows(logs)

        return len(logs)


def get_logger(
    project_dir: Path,
    agent_id: Optional[str] = None,
    console_output: bool = True,
) -> StructuredLogger:
    """
    Get or create a StructuredLogger for the given project directory.
    
    Parameters:
        project_dir (Path): Path to the project directory where the logger's database will be stored.
        agent_id (Optional[str]): Optional identifier for the agent to attach to emitted logs.
        console_output (bool): If True, also attach a human-readable console handler.
    
    Returns:
        StructuredLogger: A logger instance configured for the specified project.
    """
    return StructuredLogger(project_dir, agent_id, console_output)


def get_log_query(project_dir: Path) -> LogQuery:
    """
    Return a LogQuery bound to the project's logs SQLite database.
    
    Parameters:
        project_dir (Path): Path to the project root; the logs database is located at `<project_dir>/.autocoder/logs.db`.
    
    Returns:
        LogQuery: A query interface for the project's logs database.
    """
    db_path = Path(project_dir) / ".autocoder" / "logs.db"
    return LogQuery(db_path)