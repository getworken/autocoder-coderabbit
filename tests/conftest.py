"""
Pytest Configuration and Fixtures
=================================

Central pytest configuration and shared fixtures for all tests.
Includes async fixtures for testing FastAPI endpoints and async functions.
"""

import sys
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# =============================================================================
# Basic Fixtures
# =============================================================================


@pytest.fixture
def project_root() -> Path:
    """
    Get the project's root directory.
    
    Returns:
        Path: Filesystem path pointing to the project's root directory.
    """
    return PROJECT_ROOT


@pytest.fixture
def temp_project_dir(tmp_path: Path) -> Path:
    """
    Create a temporary project directory named "test_project" containing an empty "prompts" subdirectory.
    
    Returns:
        Path: Path to the created project directory.
    """
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()

    # Create prompts directory
    prompts_dir = project_dir / "prompts"
    prompts_dir.mkdir()

    return project_dir


# =============================================================================
# Database Fixtures
# =============================================================================


@pytest.fixture
def temp_db(tmp_path: Path) -> Generator[Path, None, None]:
    """
    Create a temporary database for testing.
    
    Yields a Path to a temporary project directory named "test_db_project" that contains an initialized database and a "prompts" subdirectory. When the fixture is torn down, the database engine cache for that project directory is invalidated to release file handles.
    
    Returns:
        project_dir (Path): Path to the temporary project directory with an initialized database.
    """
    from api.database import create_database, invalidate_engine_cache

    project_dir = tmp_path / "test_db_project"
    project_dir.mkdir()

    # Create prompts directory (required by some code)
    (project_dir / "prompts").mkdir()

    # Initialize database
    create_database(project_dir)

    yield project_dir

    # Dispose cached engine to prevent file locks on Windows
    invalidate_engine_cache(project_dir)


@pytest.fixture
def db_session(temp_db: Path):
    """
    Provide an active SQLAlchemy session for tests that is rolled back and closed after the test.
    
    Yields:
        session (Session): A database session connected to the temporary test database. The session will be rolled back and closed automatically when the fixture teardown runs.
    """
    from api.database import create_database

    _, SessionLocal = create_database(temp_db)
    session = SessionLocal()

    try:
        yield session
    finally:
        session.rollback()
        session.close()


# =============================================================================
# Async Fixtures
# =============================================================================


@pytest.fixture
async def async_temp_db(tmp_path: Path) -> AsyncGenerator[Path, None]:
    """
    Create a temporary project directory with an initialized database for async tests.
    
    Yields the Path to the temporary project directory containing an initialized database. On teardown, invalidates the database engine cache for that project to avoid file locks (Windows).
    """
    from api.database import create_database, invalidate_engine_cache

    project_dir = tmp_path / "async_test_project"
    project_dir.mkdir()
    (project_dir / "prompts").mkdir()

    # Initialize database (sync operation, but fixture is async)
    create_database(project_dir)

    yield project_dir

    # Dispose cached engine to prevent file locks on Windows
    invalidate_engine_cache(project_dir)


# =============================================================================
# FastAPI Test Client Fixtures
# =============================================================================


@pytest.fixture
def test_app():
    """
    Provide the FastAPI application instance for tests.
    
    Returns:
        app (FastAPI): The application instance imported from server.main.
    """
    from server.main import app

    return app


@pytest.fixture
async def async_client(test_app) -> AsyncGenerator:
    """
    Provide an HTTP client configured to send requests to the given FastAPI app.
    
    Parameters:
        test_app (FastAPI): The FastAPI application instance to mount into the client's ASGI transport.
    
    Returns:
        AsyncClient: An `httpx.AsyncClient` configured with an `ASGITransport` for `test_app` and `base_url="http://test"`.
    """
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test"
    ) as client:
        yield client


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_env(monkeypatch):
    """
    Provide a pytest fixture that returns a helper to set environment variables via pytest's monkeypatch.
    
    The returned function sets the environment variable named `key` to `value` for the duration of the test.
    
    Returns:
        callable: A function `(key: str, value: str)` that sets an environment variable.
    """
    def _set_env(key: str, value: str):
        """
        Set an environment variable for the current test.
        
        The variable is applied for the duration of the test and will be restored when the pytest monkeypatch fixture is undone.
        
        Parameters:
            key (str): Environment variable name.
            value (str): Value to assign to the environment variable.
        """
        monkeypatch.setenv(key, value)

    return _set_env


@pytest.fixture
def mock_project_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """
    Creates a temporary mock project directory containing prompts, configuration, and an initialized database.
    
    The directory contains:
    - prompts/ with a sample app_spec.txt
    - .autocoder/ for config
    - an initialized features.db via create_database
    
    Returns:
        project_dir (Path): Path to the created mock project directory
    """
    from api.database import create_database, invalidate_engine_cache

    project_dir = tmp_path / "mock_project"
    project_dir.mkdir()

    # Create directory structure
    prompts_dir = project_dir / "prompts"
    prompts_dir.mkdir()

    autocoder_dir = project_dir / ".autocoder"
    autocoder_dir.mkdir()

    # Create sample app_spec
    (prompts_dir / "app_spec.txt").write_text(
        "<app_name>Test App</app_name>\n<description>Test description</description>"
    )

    # Initialize database
    create_database(project_dir)

    yield project_dir

    # Dispose cached engine to prevent file locks on Windows
    invalidate_engine_cache(project_dir)


# =============================================================================
# Feature Fixtures
# =============================================================================


@pytest.fixture
def sample_feature_data() -> dict:
    """
    Provide a sample feature payload used by tests.
    
    Returns:
        dict: A feature dictionary with keys:
            - `priority` (int): Priority value (e.g., 1).
            - `category` (str): Feature category (e.g., "test").
            - `name` (str): Feature name.
            - `description` (str): Feature description.
            - `steps` (list[str]): Ordered list of step descriptions.
    """
    return {
        "priority": 1,
        "category": "test",
        "name": "Test Feature",
        "description": "A test feature for unit tests",
        "steps": ["Step 1", "Step 2", "Step 3"],
    }


@pytest.fixture
def populated_db(temp_db: Path, sample_feature_data: dict) -> Generator[Path, None, None]:
    """
    Populate the temporary project's database with five sample Feature records and yield the project directory path.
    
    Parameters:
        temp_db (Path): Path to the temporary project directory where the database will be created and populated.
        sample_feature_data (dict): Fixture-provided sample feature fields (not required by this function's population logic).
    
    Returns:
        Path: The same `temp_db` path after the database has been populated.
    """
    from api.database import Feature, create_database, invalidate_engine_cache

    _, SessionLocal = create_database(temp_db)
    session = SessionLocal()

    try:
        # Add sample features
        for i in range(5):
            feature = Feature(
                priority=i + 1,
                category=f"category_{i % 2}",
                name=f"Feature {i + 1}",
                description=f"Description for feature {i + 1}",
                steps=[f"Step {j}" for j in range(3)],
                passes=i < 2,  # First 2 features are passing
                in_progress=i == 2,  # Third feature is in progress
            )
            session.add(feature)

        session.commit()
    finally:
        session.close()

    yield temp_db

    # Dispose cached engine to prevent file locks on Windows
    invalidate_engine_cache(temp_db)