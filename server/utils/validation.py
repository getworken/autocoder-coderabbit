"""
Shared validation utilities for the server.
"""

import re

from fastapi import HTTPException

# Compiled regex for project name validation (reused across functions)
PROJECT_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{1,50}$')


def is_valid_project_name(name: str) -> bool:
    """
    Determine whether a project name matches the allowed pattern.
    
    Project names must be 1–50 characters long and may contain letters, digits, underscores, or hyphens.
    
    Parameters:
        name: The project name to validate.
    
    Returns:
        True if the name matches the allowed pattern, False otherwise.
    """
    return bool(PROJECT_NAME_PATTERN.match(name))


def validate_project_name(name: str) -> str:
    """
    Validate a project name against the allowed pattern and return it if valid.
    
    Returns:
        The validated project name.
    
    Raises:
        HTTPException: If `name` does not match the allowed pattern (letters, numbers, hyphens, and underscores; 1-50 characters).
    """
    if not is_valid_project_name(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid project name. Use only letters, numbers, hyphens, and underscores (1-50 chars)."
        )
    return name