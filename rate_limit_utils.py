"""
Rate Limit Utilities
====================

Shared utilities for detecting and handling API rate limits.
Used by both agent.py (production) and test_agent.py (tests).
"""

import re
from typing import Optional

# Rate limit detection patterns (used in both exception messages and response text)
RATE_LIMIT_PATTERNS = [
    "limit reached",
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota exceeded",
    "please wait",
    "try again later",
    "429",
    "overloaded",
]


def parse_retry_after(error_message: str) -> Optional[int]:
    """
    Extracts a retry-after duration in seconds from an error message.
    
    Supports common textual formats such as "Retry-After: 60", "try again in 5 seconds", and "30 seconds remaining".
    
    Parameters:
        error_message (str): The error message to inspect.
    
    Returns:
        int | None: The number of seconds extracted from the message, or `None` if no duration is found.
    """
    patterns = [
        r"retry.?after[:\s]+(\d+)\s*(?:seconds?)?",
        r"try again in\s+(\d+)\s*(?:seconds?|s\b)",
        r"(\d+)\s*seconds?\s*(?:remaining|left|until)",
    ]

    for pattern in patterns:
        match = re.search(pattern, error_message, re.IGNORECASE)
        if match:
            return int(match.group(1))

    return None


def is_rate_limit_error(error_message: str) -> bool:
    """
    Determine whether an error message indicates a rate limit.
    
    Checks the message against known rate-limit indicator phrases.
    
    Returns:
        `true` if the message indicates a rate limit, `false` otherwise.
    """
    error_lower = error_message.lower()
    return any(pattern in error_lower for pattern in RATE_LIMIT_PATTERNS)