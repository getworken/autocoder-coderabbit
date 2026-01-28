"""
Settings Router
===============

API endpoints for global settings management.
Settings are stored in the registry database and shared across all projects.
"""

import mimetypes
import os
import sys
from pathlib import Path

from fastapi import APIRouter

from ..schemas import (
    DeniedCommandItem,
    DeniedCommandsResponse,
    ModelInfo,
    ModelsResponse,
    SettingsResponse,
    SettingsUpdate,
)

# Mimetype fix for Windows - must run before StaticFiles is mounted
mimetypes.add_type("text/javascript", ".js", True)

# Add root to path for registry import
ROOT_DIR = Path(__file__).parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from registry import (
    CLAUDE_MODELS,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_MODEL,
    OLLAMA_MODELS,
    get_all_settings,
    set_setting,
)
from security import clear_denied_commands, get_denied_commands

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _parse_yolo_mode(value: str | None) -> bool:
    """Parse YOLO mode string to boolean."""
    return (value or "false").lower() == "true"


def _is_glm_mode() -> bool:
    """Check if GLM API is configured via environment variables."""
    base_url = os.getenv("ANTHROPIC_BASE_URL", "")
    # GLM mode is when ANTHROPIC_BASE_URL is set but NOT pointing to Ollama
    return bool(base_url) and not _is_ollama_mode()


def _is_ollama_mode() -> bool:
    """Check if Ollama API is configured via environment variables."""
    base_url = os.getenv("ANTHROPIC_BASE_URL", "")
    return "localhost:11434" in base_url or "127.0.0.1:11434" in base_url


@router.get("/models", response_model=ModelsResponse)
async def get_available_models():
    """
    Return the available model list and the default model based on the configured API mode.
    
    Selects Ollama models and DEFAULT_OLLAMA_MODEL when Ollama mode is active; otherwise selects Claude models and DEFAULT_MODEL.
    
    Returns:
        ModelsResponse: Object containing the list of available ModelInfo entries and the default model id.
    """
    if _is_ollama_mode():
        return ModelsResponse(
            models=[ModelInfo(id=m["id"], name=m["name"]) for m in OLLAMA_MODELS],
            default=DEFAULT_OLLAMA_MODEL,
        )
    return ModelsResponse(
        models=[ModelInfo(id=m["id"], name=m["name"]) for m in CLAUDE_MODELS],
        default=DEFAULT_MODEL,
    )


def _parse_int(value: str | None, default: int) -> int:
    """Parse integer setting with default fallback."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _parse_bool(value: str | None, default: bool = False) -> bool:
    """Parse boolean setting with default fallback."""
    if value is None:
        return default
    return value.lower() == "true"


def _get_default_model() -> str:
    """
    Return the default model name for the currently configured API mode.
    
    Returns:
        default_model (str): The Ollama default model when Ollama mode is active; otherwise the standard default model.
    """
    return DEFAULT_OLLAMA_MODEL if _is_ollama_mode() else DEFAULT_MODEL


@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Get current global settings."""
    all_settings = get_all_settings()
    default_model = _get_default_model()

    return SettingsResponse(
        yolo_mode=_parse_yolo_mode(all_settings.get("yolo_mode")),
        model=all_settings.get("model", default_model),
        glm_mode=_is_glm_mode(),
        ollama_mode=_is_ollama_mode(),
        testing_agent_ratio=_parse_int(all_settings.get("testing_agent_ratio"), 1),
        preferred_ide=all_settings.get("preferred_ide"),
    )


@router.patch("", response_model=SettingsResponse)
async def update_settings(update: SettingsUpdate):
    """
    Apply partial updates to global settings from the provided update object.
    
    Parameters:
        update (SettingsUpdate): Object containing optional fields to update; only fields that are not None are persisted:
            - yolo_mode (bool): enable or disable YOLO mode
            - model (str): selected model name
            - testing_agent_ratio (int): ratio used for testing agent selection
            - preferred_ide (str | None): preferred IDE identifier
    
    Returns:
        SettingsResponse: The current global settings after applying updates, including:
            - yolo_mode (bool)
            - model (str)
            - glm_mode (bool)
            - ollama_mode (bool)
            - testing_agent_ratio (int)
            - preferred_ide (str | None)
    """
    if update.yolo_mode is not None:
        set_setting("yolo_mode", "true" if update.yolo_mode else "false")

    if update.model is not None:
        set_setting("model", update.model)

    if update.testing_agent_ratio is not None:
        set_setting("testing_agent_ratio", str(update.testing_agent_ratio))

    if update.preferred_ide is not None:
        set_setting("preferred_ide", update.preferred_ide)

    # Return updated settings
    all_settings = get_all_settings()
    default_model = _get_default_model()
    return SettingsResponse(
        yolo_mode=_parse_yolo_mode(all_settings.get("yolo_mode")),
        model=all_settings.get("model", default_model),
        glm_mode=_is_glm_mode(),
        ollama_mode=_is_ollama_mode(),
        testing_agent_ratio=_parse_int(all_settings.get("testing_agent_ratio"), 1),
        preferred_ide=all_settings.get("preferred_ide"),
    )


@router.get("/denied-commands", response_model=DeniedCommandsResponse)
async def get_denied_commands_list():
    """
    Retrieve recent security-denied commands.
    
    Returns:
        DeniedCommandsResponse: Contains `commands` — a list of denied command entries (each with `command`, `reason`, `timestamp`, and `project_dir`) and `count` — the total number of entries.
    """
    denied = get_denied_commands()
    return DeniedCommandsResponse(
        commands=[
            DeniedCommandItem(
                command=d["command"],
                reason=d["reason"],
                timestamp=d["timestamp"],
                project_dir=d["project_dir"],
            )
            for d in denied
        ],
        count=len(denied),
    )


@router.delete("/denied-commands")
async def clear_denied_commands_list():
    """
    Clear the stored history of denied commands.
    
    Returns:
        dict: A dictionary with the key `status` set to `'cleared'` indicating the denied commands history was cleared.
    """
    clear_denied_commands()
    return {"status": "cleared"}