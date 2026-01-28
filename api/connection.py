"""
Database Connection Management
==============================

SQLite connection utilities, session management, and engine caching.

Concurrency Protection:
- WAL mode for better concurrent read/write access
- Busy timeout (30s) to handle lock contention
- Connection-level retries for transient errors
"""

import logging
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from api.migrations import run_all_migrations
from api.models import Base

# Module logger
logger = logging.getLogger(__name__)

# SQLite configuration constants
SQLITE_BUSY_TIMEOUT_MS = 30000  # 30 seconds
SQLITE_MAX_RETRIES = 3
SQLITE_RETRY_DELAY_MS = 100  # Start with 100ms, exponential backoff

# Engine cache to avoid creating new engines for each request
# Key: project directory path (as posix string), Value: (engine, SessionLocal)
# Thread-safe: protected by _engine_cache_lock
_engine_cache: dict[str, tuple] = {}
_engine_cache_lock = threading.Lock()


def _is_network_path(path: Path) -> bool:
    """
    Detects whether a given path is located on a network filesystem.
    
    Detection is best-effort and may be conservative; if platform or system
    information cannot be inspected, the function will return False.
    
    Returns:
        True if the path appears to be on a network filesystem, False otherwise.
    """
    path_str = str(path.resolve())

    if sys.platform == "win32":
        # Windows UNC paths: \\server\share or \\?\UNC\server\share
        if path_str.startswith("\\\\"):
            return True
        # Mapped network drives - check if the drive is a network drive
        try:
            import ctypes
            drive = path_str[:2]  # e.g., "Z:"
            if len(drive) == 2 and drive[1] == ":":
                # DRIVE_REMOTE = 4
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
                if drive_type == 4:  # DRIVE_REMOTE
                    return True
        except (AttributeError, OSError):
            pass
    else:
        # Unix: Check mount type via /proc/mounts or mount command
        try:
            with open("/proc/mounts", "r") as f:
                mounts = f.read()
                # Check each mount point to find which one contains our path
                for line in mounts.splitlines():
                    parts = line.split()
                    if len(parts) >= 3:
                        mount_point = parts[1]
                        fs_type = parts[2]
                        # Check if path is under this mount point and if it's a network FS
                        if path_str.startswith(mount_point):
                            if fs_type in ("nfs", "nfs4", "cifs", "smbfs", "fuse.sshfs"):
                                return True
        except (FileNotFoundError, PermissionError):
            pass

    return False


def get_database_path(project_dir: Path) -> Path:
    """
    Get the filesystem path for the project's SQLite database file.
    
    Returns:
        database_path (Path): Path to the 'features.db' file inside the given project directory.
    """
    return project_dir / "features.db"


def get_database_url(project_dir: Path) -> str:
    """
    Builds the SQLAlchemy SQLite database URL for the given project directory.
    
    The path portion uses POSIX-style forward slashes for cross-platform compatibility.
    
    Returns:
        database_url (str): SQLite URL pointing to the project's features.db (e.g. "sqlite:////path/to/features.db").
    """
    db_path = get_database_path(project_dir)
    return f"sqlite:///{db_path.as_posix()}"


def get_robust_connection(db_path: Path) -> sqlite3.Connection:
    """
    Open and configure a sqlite3.Connection optimized for concurrent access.
    
    Configures the connection with a 30-second busy timeout, enables WAL journal mode when the database file is on a local filesystem, and sets synchronous mode to NORMAL.
    
    Parameters:
        db_path (Path): Path to the SQLite database file.
    
    Returns:
        sqlite3.Connection: Configured SQLite connection.
    
    Raises:
        sqlite3.Error: If the database connection or PRAGMA configuration fails.
    """
    conn = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)

    # Set busy timeout (in milliseconds for sqlite3)
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")

    # Enable WAL mode (only for local filesystems)
    if not _is_network_path(db_path):
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.Error:
            # WAL mode might fail on some systems, fall back to default
            pass

    # Synchronous NORMAL provides good balance of safety and performance
    conn.execute("PRAGMA synchronous = NORMAL")

    return conn


@contextmanager
def robust_db_connection(db_path: Path):
    """
    Context manager that yields a configured sqlite3.Connection and ensures it is closed on exit.
    
    Parameters:
        db_path (Path): Path to the SQLite database file.
    
    Yields:
        sqlite3.Connection: A configured connection to the database; closed when the context exits.
    """
    conn = None
    try:
        conn = get_robust_connection(db_path)
        yield conn
    finally:
        if conn:
            conn.close()


def execute_with_retry(
    db_path: Path,
    query: str,
    params: tuple = (),
    fetch: str = "none",
    max_retries: int = SQLITE_MAX_RETRIES
) -> Any:
    """
    Execute a SQL statement against the given SQLite file and retry on transient lock/busy errors.
    
    Parameters:
        db_path (Path): Path to the SQLite database file.
        query (str): SQL statement to execute.
        params (tuple): Parameters to bind to the SQL statement.
        fetch (str): Result mode: "none" commits and returns the number of affected rows, "one" returns a single row (or None), "all" returns all rows as a list.
        max_retries (int): Maximum number of retry attempts for transient errors.
    
    Returns:
        int | tuple | list | None: For `fetch == "none"`, the number of rows affected; for `fetch == "one"`, a single row tuple or `None`; for `fetch == "all"`, a list of row tuples.
    
    Raises:
        sqlite3.DatabaseError: On database corruption or other database-level errors.
        sqlite3.OperationalError: If the statement fails after all retries (including persistent lock/busy conditions).
    """
    last_error = None
    delay = SQLITE_RETRY_DELAY_MS / 1000  # Convert to seconds

    for attempt in range(max_retries + 1):
        try:
            with robust_db_connection(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)

                if fetch == "one":
                    result = cursor.fetchone()
                elif fetch == "all":
                    result = cursor.fetchall()
                else:
                    conn.commit()
                    result = cursor.rowcount

                return result

        except sqlite3.OperationalError as e:
            error_msg = str(e).lower()
            # Retry on lock/busy errors
            if "locked" in error_msg or "busy" in error_msg:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        f"Database busy/locked (attempt {attempt + 1}/{max_retries + 1}), "
                        f"retrying in {delay:.2f}s: {e}"
                    )
                    time.sleep(delay)
                    delay *= 2  # Exponential backoff
                    continue
            raise
        except sqlite3.DatabaseError as e:
            # Log corruption errors clearly
            error_msg = str(e).lower()
            if "malformed" in error_msg or "corrupt" in error_msg:
                logger.error(f"DATABASE CORRUPTION DETECTED: {e}")
            raise

    # If we get here, all retries failed
    raise last_error or sqlite3.OperationalError("Query failed after all retries")


def check_database_health(db_path: Path) -> dict:
    """
    Assess the integrity and journal mode of a SQLite database file.
    
    Parameters:
        db_path (Path): Path to the SQLite database file to check.
    
    Returns:
        dict: A dictionary containing:
            - healthy (bool): `True` if the database passes PRAGMA integrity_check, `False` otherwise.
            - journal_mode (str, optional): The current journal mode (e.g., "WAL", "DELETE") when available.
            - integrity (str, optional): The raw result of PRAGMA integrity_check when available (e.g., "ok").
            - error (str, optional): Error message when the file is missing or an integrity/IO error occurred.
    """
    if not db_path.exists():
        return {"healthy": False, "error": "Database file does not exist"}

    try:
        with robust_db_connection(db_path) as conn:
            cursor = conn.cursor()

            # Check integrity
            cursor.execute("PRAGMA integrity_check")
            integrity = cursor.fetchone()[0]

            # Get journal mode
            cursor.execute("PRAGMA journal_mode")
            journal_mode = cursor.fetchone()[0]

            if integrity.lower() == "ok":
                return {
                    "healthy": True,
                    "journal_mode": journal_mode,
                    "integrity": integrity
                }
            else:
                return {
                    "healthy": False,
                    "journal_mode": journal_mode,
                    "error": f"Integrity check failed: {integrity}"
                }

    except sqlite3.Error as e:
        return {"healthy": False, "error": str(e)}


def create_database(project_dir: Path) -> tuple:
    """
    Create database and return engine + session maker.

    Uses a cache to avoid creating new engines for each request, which prevents
    file descriptor leaks and improves performance by reusing database connections.

    Thread Safety:
    - Uses double-checked locking pattern to minimize lock contention
    - First check is lock-free for fast path (cache hit)
    - Lock is only acquired when creating new engines

    Args:
        project_dir: Directory containing the project

    Returns:
        Tuple of (engine, SessionLocal)
    """
    cache_key = project_dir.resolve().as_posix()

    # Fast path: check cache without lock (double-checked locking pattern)
    if cache_key in _engine_cache:
        return _engine_cache[cache_key]

    # Slow path: acquire lock and check again
    with _engine_cache_lock:
        # Double-check inside lock to prevent race condition
        if cache_key in _engine_cache:
            return _engine_cache[cache_key]

        db_url = get_database_url(project_dir)
        engine = create_engine(db_url, connect_args={
            "check_same_thread": False,
            "timeout": 30  # Wait up to 30s for locks
        })
        Base.metadata.create_all(bind=engine)

        # Choose journal mode based on filesystem type
        # WAL mode doesn't work reliably on network filesystems and can cause corruption
        is_network = _is_network_path(project_dir)
        journal_mode = "DELETE" if is_network else "WAL"

        with engine.connect() as conn:
            conn.execute(text(f"PRAGMA journal_mode={journal_mode}"))
            conn.execute(text("PRAGMA busy_timeout=30000"))
            conn.commit()

        # Run all migrations
        run_all_migrations(engine)

        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        # Cache the engine and session maker
        _engine_cache[cache_key] = (engine, SessionLocal)
        logger.debug(f"Created new database engine for {cache_key}")

        return engine, SessionLocal


def checkpoint_wal(project_dir: Path) -> bool:
    """
    Force a WAL checkpoint for the project's SQLite database to flush and truncate the WAL into the main database.
    
    Parameters:
        project_dir (Path): Directory containing the project's SQLite database file (features.db).
    
    Returns:
        `true` if the checkpoint succeeded or the database file does not exist, `false` otherwise.
    """
    db_path = get_database_path(project_dir)
    if not db_path.exists():
        return True  # No database to checkpoint

    try:
        with robust_db_connection(db_path) as conn:
            cursor = conn.cursor()
            # Use TRUNCATE mode for cleanest state on exit
            cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            result = cursor.fetchone()
            # Result: (busy, log_pages, checkpointed_pages)
            if result and result[0] == 0:  # Not busy
                logger.debug(
                    f"WAL checkpoint successful for {db_path}: "
                    f"log_pages={result[1]}, checkpointed={result[2]}"
                )
                return True
            else:
                logger.warning(f"WAL checkpoint partial for {db_path}: {result}")
                return True  # Partial checkpoint is still okay
    except Exception as e:
        logger.error(f"WAL checkpoint failed for {db_path}: {e}")
        return False


def invalidate_engine_cache(project_dir: Path) -> None:
    """
    Invalidate and dispose the cached SQLAlchemy Engine and SessionLocal for the given project directory.
    
    Parameters:
        project_dir (Path): Path to the project directory whose cached engine should be removed.
    """
    cache_key = project_dir.resolve().as_posix()
    with _engine_cache_lock:
        if cache_key in _engine_cache:
            engine, _ = _engine_cache[cache_key]
            try:
                engine.dispose()
            except Exception as e:
                logger.warning(f"Error disposing engine for {cache_key}: {e}")
            del _engine_cache[cache_key]
            logger.debug(f"Invalidated engine cache for {cache_key}")


# Global session maker - will be set when server starts
_session_maker: Optional[sessionmaker] = None


def set_session_maker(session_maker: sessionmaker) -> None:
    """
    Configure the module-wide SQLAlchemy session factory used by get_db and get_db_session.
    
    Parameters:
        session_maker (sessionmaker): A SQLAlchemy sessionmaker instance to use as the global session factory.
    """
    global _session_maker
    _session_maker = session_maker


def get_db() -> Session:
    """
    Provide a SQLAlchemy Session for FastAPI dependency injection.
    
    Yields a Session for database operations and ensures the session is closed afterwards. On exception, rolls back the transaction before re-raising.
    
    Returns:
        Session: A SQLAlchemy Session instance for use in request handling.
    """
    if _session_maker is None:
        raise RuntimeError("Database not initialized. Call set_session_maker first.")

    db = _session_maker()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def get_db_session(project_dir: Path):
    """
    Context manager for database sessions with automatic cleanup.

    Ensures the session is properly closed on all code paths, including exceptions.
    Rolls back uncommitted changes on error to prevent PendingRollbackError.

    Usage:
        with get_db_session(project_dir) as session:
            feature = session.query(Feature).first()
            feature.passes = True
            session.commit()

    Args:
        project_dir: Path to the project directory

    Yields:
        SQLAlchemy Session object

    Raises:
        Any exception from the session operations (after rollback)
    """
    _, SessionLocal = create_database(project_dir)
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()