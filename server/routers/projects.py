"""
Projects Router
===============

API endpoints for project management.
Uses project registry for path lookups instead of fixed generations/ directory.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..schemas import (
    DatabaseHealth,
    KnowledgeFile,
    KnowledgeFileContent,
    KnowledgeFileList,
    KnowledgeFileUpload,
    ProjectCreate,
    ProjectDetail,
    ProjectPrompts,
    ProjectPromptsUpdate,
    ProjectStats,
    ProjectSummary,
)
from ..utils.validation import validate_project_name

# Lazy imports to avoid circular dependencies
_imports_initialized = False
_check_spec_exists = None
_scaffold_project_prompts = None
_get_project_prompts_dir = None
_count_passing_tests = None


def _init_imports():
    """Lazy import of project-level modules."""
    global _imports_initialized, _check_spec_exists
    global _scaffold_project_prompts, _get_project_prompts_dir
    global _count_passing_tests

    if _imports_initialized:
        return

    import sys
    root = Path(__file__).parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from progress import count_passing_tests
    from prompts import get_project_prompts_dir, scaffold_project_prompts
    from start import check_spec_exists

    _check_spec_exists = check_spec_exists
    _scaffold_project_prompts = scaffold_project_prompts
    _get_project_prompts_dir = get_project_prompts_dir
    _count_passing_tests = count_passing_tests
    _imports_initialized = True


def _get_registry_functions():
    """Get registry functions with lazy import."""
    import sys
    root = Path(__file__).parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from registry import (
        get_project_path,
        list_registered_projects,
        register_project,
        unregister_project,
        validate_project_path,
    )
    return register_project, unregister_project, get_project_path, list_registered_projects, validate_project_path


router = APIRouter(prefix="/api/projects", tags=["projects"])


def get_project_stats(project_dir: Path) -> ProjectStats:
    """
    Compute statistics for a project.
    
    Parameters:
        project_dir (Path): Path to the project's root directory.
    
    Returns:
        ProjectStats: Aggregated test statistics containing `passing`, `in_progress`, `total`, and `percentage` (rounded to one decimal place).
    """
    _init_imports()
    passing, in_progress, total = _count_passing_tests(project_dir)
    percentage = (passing / total * 100) if total > 0 else 0.0
    return ProjectStats(
        passing=passing,
        in_progress=in_progress,
        total=total,
        percentage=round(percentage, 1)
    )


@router.get("", response_model=list[ProjectSummary])
async def list_projects():
    """List all registered projects."""
    _init_imports()
    _, _, _, list_registered_projects, validate_project_path = _get_registry_functions()

    projects = list_registered_projects()
    result = []

    for name, info in projects.items():
        project_dir = Path(info["path"])

        # Skip if path no longer exists
        is_valid, _ = validate_project_path(project_dir)
        if not is_valid:
            continue

        has_spec = _check_spec_exists(project_dir)
        stats = get_project_stats(project_dir)

        result.append(ProjectSummary(
            name=name,
            path=info["path"],
            has_spec=has_spec,
            stats=stats,
        ))

    return result


@router.post("", response_model=ProjectSummary)
async def create_project(project: ProjectCreate):
    """
    Create a new project directory and register it in the project registry.
    
    Validate the requested project name and path, ensure the path is not already
    registered or blocked, create the directory if needed, scaffold project prompts,
    and register the project.
    
    Parameters:
        project (ProjectCreate): Payload containing the desired project `name` and `path`.
    
    Returns:
        ProjectSummary: Summary of the newly created project with `has_spec` set to `False`
        and zeroed statistics.
    
    Raises:
        HTTPException: 
            - 409 if the project name or path is already registered.
            - 403 if the path is in a blocked/system location.
            - 400 if the path exists but is not a directory.
            - 500 if directory creation or registry registration fails.
    """
    _init_imports()
    register_project, _, get_project_path, list_registered_projects, _ = _get_registry_functions()

    name = validate_project_name(project.name)
    project_path = Path(project.path).resolve()

    # Check if project name already registered
    existing = get_project_path(name)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Project '{name}' already exists at {existing}"
        )

    # Check if path already registered under a different name
    all_projects = list_registered_projects()
    for existing_name, info in all_projects.items():
        existing_path = Path(info["path"]).resolve()
        # Case-insensitive comparison on Windows
        if sys.platform == "win32":
            paths_match = str(existing_path).lower() == str(project_path).lower()
        else:
            paths_match = existing_path == project_path

        if paths_match:
            raise HTTPException(
                status_code=409,
                detail=f"Path '{project_path}' is already registered as project '{existing_name}'"
            )

    # Security: Check if path is in a blocked location
    from .filesystem import is_path_blocked
    if is_path_blocked(project_path):
        raise HTTPException(
            status_code=403,
            detail="Cannot create project in system or sensitive directory"
        )

    # Validate the path is usable
    if project_path.exists():
        if not project_path.is_dir():
            raise HTTPException(
                status_code=400,
                detail="Path exists but is not a directory"
            )
    else:
        # Create the directory
        try:
            project_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create directory: {e}"
            )

    # Scaffold prompts
    _scaffold_project_prompts(project_path)

    # Register in registry
    try:
        register_project(name, project_path)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to register project: {e}"
        )

    return ProjectSummary(
        name=name,
        path=project_path.as_posix(),
        has_spec=False,  # Just created, no spec yet
        stats=ProjectStats(passing=0, total=0, percentage=0.0),
    )


@router.post("/import", response_model=ProjectSummary)
async def import_project(project: ProjectCreate):
    """
    Register an existing on-disk Autocoder project in the current registry.
    
    Validates the provided project name and path, ensures the path exists, is a directory, is an Autocoder project (contains a `.autocoder` folder), is not blocked, and that neither the name nor the path are already registered before registering the project and returning its summary.
    
    Parameters:
        project (ProjectCreate): Object containing `name` (desired project name) and `path` (filesystem path to the existing project).
    
    Returns:
        ProjectSummary: Summary of the registered project containing `name`, `path`, `has_spec`, and `stats`.
    
    Raises:
        HTTPException: 
            - 409 if the project name is already registered or the path is already registered under another name.
            - 404 if the provided path does not exist.
            - 400 if the path exists but is not a directory, or if it lacks a `.autocoder` folder.
            - 403 if the path is a blocked/system-sensitive directory.
            - 500 if registration fails due to an internal error.
    """
    _init_imports()
    register_project, _, get_project_path, list_registered_projects, _ = _get_registry_functions()

    name = validate_project_name(project.name)
    project_path = Path(project.path).resolve()

    # Check if project name already registered
    existing = get_project_path(name)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Project '{name}' already exists at {existing}. Use a different name or delete the existing project first."
        )

    # Check if path already registered under a different name
    all_projects = list_registered_projects()
    for existing_name, info in all_projects.items():
        existing_path = Path(info["path"]).resolve()
        if sys.platform == "win32":
            paths_match = str(existing_path).lower() == str(project_path).lower()
        else:
            paths_match = existing_path == project_path

        if paths_match:
            raise HTTPException(
                status_code=409,
                detail=f"Path '{project_path}' is already registered as project '{existing_name}'"
            )

    # Validate the path exists and is a directory
    if not project_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Project path does not exist: {project_path}"
        )

    if not project_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail="Path exists but is not a directory"
        )

    # Check for .autocoder folder to confirm it's a valid autocoder project
    autocoder_dir = project_path / ".autocoder"
    if not autocoder_dir.exists():
        raise HTTPException(
            status_code=400,
            detail="Path does not appear to be an autocoder project (missing .autocoder folder). Use 'Create Project' instead."
        )

    # Security check
    from .filesystem import is_path_blocked
    if is_path_blocked(project_path):
        raise HTTPException(
            status_code=403,
            detail="Cannot import project from system or sensitive directory"
        )

    # Register in registry
    try:
        register_project(name, project_path)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to register project: {e}"
        )

    # Get project stats
    has_spec = _check_spec_exists(project_path)
    stats = get_project_stats(project_path)

    return ProjectSummary(
        name=name,
        path=project_path.as_posix(),
        has_spec=has_spec,
        stats=stats,
    )


@router.get("/{name}", response_model=ProjectDetail)
async def get_project(name: str):
    """
    Retrieve detailed metadata and status for a registered project.
    
    Parameters:
        name (str): Registered project name to look up.
    
    Returns:
        ProjectDetail: Object containing the project's name, filesystem path, whether an app spec exists (`has_spec`), computed statistics (`stats`), and the prompts directory path (`prompts_dir`).
    
    Raises:
        HTTPException: 404 if the project name is not registered or the project directory no longer exists on disk.
    """
    _init_imports()
    _, _, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found in registry")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project directory no longer exists: {project_dir}")

    has_spec = _check_spec_exists(project_dir)
    stats = get_project_stats(project_dir)
    prompts_dir = _get_project_prompts_dir(project_dir)

    return ProjectDetail(
        name=name,
        path=project_dir.as_posix(),
        has_spec=has_spec,
        stats=stats,
        prompts_dir=str(prompts_dir),
    )


@router.delete("/{name}")
async def delete_project(name: str, delete_files: bool = False):
    """
    Remove a project from the registry and optionally delete its on-disk files.
    
    Parameters:
        name (str): Name of the project to remove.
        delete_files (bool): If True, remove the project's directory and all contents from disk.
    
    Returns:
        dict: A status payload with keys `success` (`True`) and `message` describing the outcome.
    
    Raises:
        HTTPException(404): If the project name is not registered.
        HTTPException(409): If the project has a running agent (presence of `.agent.lock`).
        HTTPException(500): If deleting the project files fails.
    """
    _init_imports()
    _, unregister_project, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    # Check if agent is running
    lock_file = project_dir / ".agent.lock"
    if lock_file.exists():
        raise HTTPException(
            status_code=409,
            detail="Cannot delete project while agent is running. Stop the agent first."
        )

    # Optionally delete files
    if delete_files and project_dir.exists():
        try:
            shutil.rmtree(project_dir)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete project files: {e}")

    # Unregister from registry
    unregister_project(name)

    return {
        "success": True,
        "message": f"Project '{name}' deleted" + (" (files removed)" if delete_files else " (files preserved)")
    }


@router.post("/{name}/reset")
async def reset_project(name: str, full_reset: bool = False):
    """
    Reset a registered project by removing runtime data and, optionally, its prompts.
    
    Removes persistent runtime files (databases and agent/assistant settings). If `full_reset` is True,
    also removes the project's prompts directory. This operation preserves the project's registry entry.
    
    Parameters:
        name (str): The registered project name to reset.
        full_reset (bool): If True, also delete the project's `prompts/` directory.
    
    Returns:
        dict: A summary of the reset with keys:
            - `success` (bool): Whether the reset completed.
            - `message` (str): Human-readable result message.
            - `deleted_files` (List[str]): Names of files/directories removed.
            - `full_reset` (bool): Mirrors the input `full_reset` flag.
    """
    _init_imports()
    _, _, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")

    # Check if agent is running
    lock_file = project_dir / ".agent.lock"
    if lock_file.exists():
        raise HTTPException(
            status_code=409,
            detail="Cannot reset project while agent is running. Stop the agent first."
        )

    # Files to delete
    files_to_delete = [
        "features.db",
        "assistant.db",
        ".claude_settings.json",
        ".claude_assistant_settings.json",
    ]

    deleted_files = []
    errors = []

    for filename in files_to_delete:
        filepath = project_dir / filename
        if filepath.exists():
            try:
                filepath.unlink()
                deleted_files.append(filename)
            except Exception as e:
                errors.append(f"{filename}: {e}")

    # If full reset, also delete prompts directory
    if full_reset:
        prompts_dir = project_dir / "prompts"
        if prompts_dir.exists():
            try:
                shutil.rmtree(prompts_dir)
                deleted_files.append("prompts/")
            except Exception as e:
                errors.append(f"prompts/: {e}")

    if errors:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete some files: {'; '.join(errors)}"
        )

    reset_type = "fully reset" if full_reset else "reset"
    return {
        "success": True,
        "message": f"Project '{name}' has been {reset_type}",
        "deleted_files": deleted_files,
        "full_reset": full_reset,
    }


@router.post("/{name}/open-in-ide")
async def open_project_in_ide(name: str, ide: str):
    """
    Open the named project directory in the specified IDE.
    
    Parameters:
        name (str): Registered project name to open.
        ide (str): IDE identifier to launch; one of "vscode", "cursor", or "antigravity".
    
    Returns:
        dict: A status payload with keys `status` and `message` describing the action.
    
    Raises:
        HTTPException: If the project is not registered or its directory is missing, if `ide` is invalid,
                       if the IDE executable cannot be found on PATH, or if launching the IDE fails.
    """
    _init_imports()
    _, _, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project directory not found: {project_dir}")

    # Validate IDE parameter
    ide_commands = {
        'vscode': 'code',
        'cursor': 'cursor',
        'antigravity': 'antigravity',
    }

    if ide not in ide_commands:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid IDE. Must be one of: {list(ide_commands.keys())}"
        )

    cmd = ide_commands[ide]
    project_path = str(project_dir)

    # Find the IDE executable in PATH
    cmd_path = shutil.which(cmd)
    if not cmd_path:
        raise HTTPException(
            status_code=400,
            detail=f"IDE executable '{cmd}' not found in PATH. Please ensure {ide} is installed and available in your system PATH."
        )

    try:
        if sys.platform == "win32":
            subprocess.Popen([cmd_path, project_path])
        else:
            # Unix-like systems
            subprocess.Popen([cmd, project_path], start_new_session=True)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to open IDE: {e}"
        )

    return {"status": "success", "message": f"Opening {project_path} in {ide}"}


@router.get("/{name}/prompts", response_model=ProjectPrompts)
async def get_project_prompts(name: str):
    """
    Return the text contents of a project's prompt files.
    
    Parameters:
        name (str): Project name; will be validated and looked up in the project registry.
    
    Returns:
        ProjectPrompts: Object containing:
            - app_spec (str): Contents of `app_spec.txt` or empty string if missing or unreadable.
            - initializer_prompt (str): Contents of `initializer_prompt.md` or empty string if missing or unreadable.
            - coding_prompt (str): Contents of `coding_prompt.md` or empty string if missing or unreadable.
    
    Raises:
        HTTPException: 404 if the project is not registered or its directory does not exist.
    """
    _init_imports()
    _, _, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")

    prompts_dir = _get_project_prompts_dir(project_dir)

    def read_file(filename: str) -> str:
        filepath = prompts_dir / filename
        if filepath.exists():
            try:
                return filepath.read_text(encoding="utf-8")
            except Exception:
                return ""
        return ""

    return ProjectPrompts(
        app_spec=read_file("app_spec.txt"),
        initializer_prompt=read_file("initializer_prompt.md"),
        coding_prompt=read_file("coding_prompt.md"),
    )


@router.put("/{name}/prompts")
async def update_project_prompts(name: str, prompts: ProjectPromptsUpdate):
    """Update project prompt files."""
    _init_imports()
    _, _, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")

    prompts_dir = _get_project_prompts_dir(project_dir)
    prompts_dir.mkdir(parents=True, exist_ok=True)

    def write_file(filename: str, content: str | None):
        if content is not None:
            filepath = prompts_dir / filename
            filepath.write_text(content, encoding="utf-8")

    write_file("app_spec.txt", prompts.app_spec)
    write_file("initializer_prompt.md", prompts.initializer_prompt)
    write_file("coding_prompt.md", prompts.coding_prompt)

    return {"success": True, "message": "Prompts updated"}


@router.get("/{name}/stats", response_model=ProjectStats)
async def get_project_stats_endpoint(name: str):
    """
    Return summary statistics for the named project.
    
    Parameters:
        name (str): Project name to look up (will be validated).
    
    Returns:
        ProjectStats: Aggregated project statistics including passing, in_progress, total, and percent.
    
    Raises:
        HTTPException: 404 if the project is not registered or its directory does not exist.
    """
    _init_imports()
    _, _, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")

    return get_project_stats(project_dir)


@router.get("/{name}/db-health", response_model=DatabaseHealth)
async def get_database_health(name: str):
    """
    Check the SQLite database health for a named project.
    
    Returns:
        DatabaseHealth: Health report containing integrity status, journal mode, and any detected errors.
    
    Raises:
        HTTPException: 404 if the project is not registered or the project directory does not exist.
    """
    _, _, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")

    # Import health check function
    root = Path(__file__).parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from api.database import check_database_health, get_database_path

    db_path = get_database_path(project_dir)
    result = check_database_health(db_path)

    return DatabaseHealth(**result)


# =============================================================================
# Knowledge Files Endpoints
# =============================================================================

def get_knowledge_dir(project_dir: Path) -> Path:
    """
    Get the project's knowledge directory path.
    
    Parameters:
        project_dir (Path): Root directory of the project.
    
    Returns:
        Path: Path pointing to the 'knowledge' subdirectory inside the project directory.
    """
    return project_dir / "knowledge"


@router.get("/{name}/knowledge", response_model=KnowledgeFileList)
async def list_knowledge_files(name: str):
    """
    List markdown knowledge files stored for the specified project.
    
    Parameters:
        name (str): Project name to resolve and enumerate knowledge files for.
    
    Returns:
        KnowledgeFileList: An object containing `files` (list of knowledge file records with `name`, `size` in bytes, and `modified` datetime) and `count` (number of files). Returns an empty list and count 0 if the knowledge directory does not exist.
    
    Raises:
        HTTPException: 404 if the project is not registered or the project directory does not exist.
    """
    _init_imports()
    _, _, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")

    knowledge_dir = get_knowledge_dir(project_dir)

    if not knowledge_dir.exists():
        return KnowledgeFileList(files=[], count=0)

    files = []
    for filepath in knowledge_dir.glob("*.md"):
        if filepath.is_file():
            stat = filepath.stat()
            from datetime import datetime
            files.append(KnowledgeFile(
                name=filepath.name,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime)
            ))

    # Sort by name
    files.sort(key=lambda f: f.name.lower())

    return KnowledgeFileList(files=files, count=len(files))


@router.get("/{name}/knowledge/{filename}", response_model=KnowledgeFileContent)
async def get_knowledge_file(name: str, filename: str):
    """
    Retrieve the UTF-8 content of a project's knowledge markdown file.
    
    Parameters:
        name (str): Project name to look up in the registry.
        filename (str): Markdown filename to read; must match the pattern `^[a-zA-Z0-9_\-\.]+\.md$`.
    
    Returns:
        KnowledgeFileContent: Object containing the `name` (filename) and `content` of the file.
    
    Raises:
        HTTPException: 400 if the filename is invalid; 404 if the project, project directory, or file is not found; 500 if the file cannot be read.
    """
    _init_imports()
    _, _, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")

    # Validate filename (prevent path traversal)
    if not re.match(r'^[a-zA-Z0-9_\-\.]+\.md$', filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    knowledge_dir = get_knowledge_dir(project_dir)
    filepath = knowledge_dir / filename

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Knowledge file '{filename}' not found")

    try:
        content = filepath.read_text(encoding="utf-8")
        return KnowledgeFileContent(name=filename, content=content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")


@router.post("/{name}/knowledge", response_model=KnowledgeFileContent)
async def upload_knowledge_file(name: str, file: KnowledgeFileUpload):
    """
    Save an uploaded knowledge file into the project's knowledge directory.
    
    Parameters:
        name (str): Project name used to locate the project directory.
        file (KnowledgeFileUpload): Uploaded file payload. Expected fields:
            - filename: target filename to write under the project's knowledge directory.
            - content: UTF-8 text content to write to the file.
    
    Returns:
        KnowledgeFileContent: The written file's `filename` and `content`.
    
    Raises:
        HTTPException: 404 if the project is not registered or its directory is missing.
        HTTPException: 500 if writing the file fails.
    """
    _init_imports()
    _, _, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")

    knowledge_dir = get_knowledge_dir(project_dir)
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    filepath = knowledge_dir / file.filename

    try:
        filepath.write_text(file.content, encoding="utf-8")
        return KnowledgeFileContent(name=file.filename, content=file.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {e}")


@router.delete("/{name}/knowledge/{filename}")
async def delete_knowledge_file(name: str, filename: str):
    """
    Remove a markdown knowledge file from the specified project.
    
    Parameters:
        name (str): Registered project name.
        filename (str): Name of the markdown file to delete (must match pattern `^[a-zA-Z0-9_\-\.]+\.md$`).
    
    Returns:
        dict: {"success": True, "message": "<info>"} on successful deletion.
    
    Raises:
        HTTPException: 400 if `filename` is invalid; 404 if the project or file is not found; 500 if deletion fails.
    """
    _init_imports()
    _, _, get_project_path, _, _ = _get_registry_functions()

    name = validate_project_name(name)
    project_dir = get_project_path(name)

    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")

    # Validate filename (prevent path traversal)
    if not re.match(r'^[a-zA-Z0-9_\-\.]+\.md$', filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    knowledge_dir = get_knowledge_dir(project_dir)
    filepath = knowledge_dir / filename

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Knowledge file '{filename}' not found")

    try:
        filepath.unlink()
        return {"success": True, "message": f"Deleted '{filename}'"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")