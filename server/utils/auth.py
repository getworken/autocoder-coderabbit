"""
Authentication Utilities
========================

HTTP Basic Authentication utilities for the Autocoder server.
Provides both HTTP middleware and WebSocket authentication support.

Configuration:
    Set both BASIC_AUTH_USERNAME and BASIC_AUTH_PASSWORD environment
    variables to enable authentication. If either is not set, auth is disabled.

Example:
    # In .env file:
    BASIC_AUTH_USERNAME=admin
    BASIC_AUTH_PASSWORD=your-secure-password

For WebSocket connections:
    - Clients that support custom headers can use Authorization header
    - Browser WebSockets can pass token via query param: ?token=base64(user:pass)
"""

import base64
import binascii
import os
import secrets

from fastapi import WebSocket


def is_basic_auth_enabled() -> bool:
    """
    Determine whether HTTP Basic Authentication is configured via environment variables.
    
    Returns:
        bool: `True` if both `BASIC_AUTH_USERNAME` and `BASIC_AUTH_PASSWORD` environment variables are set to non-empty values after stripping whitespace, `False` otherwise.
    """
    username = os.environ.get("BASIC_AUTH_USERNAME", "").strip()
    password = os.environ.get("BASIC_AUTH_PASSWORD", "").strip()
    return bool(username and password)


def get_basic_auth_credentials() -> tuple[str, str]:
    """
    Read the configured Basic Auth username and password from environment variables.
    
    Returns:
        A tuple (username, password) where each value is the corresponding environment
        variable trimmed of surrounding whitespace; if a variable is not set, its value
        is an empty string.
    """
    username = os.environ.get("BASIC_AUTH_USERNAME", "").strip()
    password = os.environ.get("BASIC_AUTH_PASSWORD", "").strip()
    return username, password


def verify_basic_auth(username: str, password: str) -> bool:
    """
    Validate provided Basic Auth credentials against the configured username and password.
    
    Comparison is performed in constant time to mitigate timing attacks. If no configured username or password is set, authentication is considered disabled and the function returns True.
    
    Returns:
        True if both the provided username and password match the configured credentials, False otherwise.
    """
    expected_user, expected_pass = get_basic_auth_credentials()
    if not expected_user or not expected_pass:
        return True  # Auth not configured, allow all

    user_valid = secrets.compare_digest(username, expected_user)
    pass_valid = secrets.compare_digest(password, expected_pass)
    return user_valid and pass_valid


def check_websocket_auth(websocket: WebSocket) -> bool:
    """
    Validate a WebSocket connection against configured HTTP Basic credentials.
    
    If no Basic Auth credentials are configured, the connection is allowed. Authentication is accepted either via an Authorization header of the form "Basic <base64(user:pass)>" or via a query parameter "token" containing base64("user:pass").
    
    Parameters:
        websocket: WebSocket-like object with `headers` and `query_params` mappings used to read the Authorization header and the `token` query parameter.
    
    Returns:
        `True` if authentication succeeds or is not required, `False` otherwise.
    """
    # If Basic Auth not configured, allow all connections
    if not is_basic_auth_enabled():
        return True

    # Try Authorization header first
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.startswith("Basic "):
        try:
            encoded = auth_header[6:]
            decoded = base64.b64decode(encoded).decode("utf-8")
            user, passwd = decoded.split(":", 1)
            if verify_basic_auth(user, passwd):
                return True
        except (ValueError, UnicodeDecodeError, binascii.Error):
            pass

    # Try query parameter (for browser WebSockets)
    # URL would be: ws://host/ws/projects/name?token=base64(user:pass)
    token = websocket.query_params.get("token", "")
    if token:
        try:
            decoded = base64.b64decode(token).decode("utf-8")
            user, passwd = decoded.split(":", 1)
            if verify_basic_auth(user, passwd):
                return True
        except (ValueError, UnicodeDecodeError, binascii.Error):
            pass

    return False


async def reject_unauthenticated_websocket(websocket: WebSocket) -> bool:
    """
    Validate a WebSocket's Basic Authentication and close the connection if authentication fails.
    
    Parameters:
        websocket (WebSocket): The WebSocket connection to validate; will be closed with code 4001 and reason "Authentication required" if authentication fails.
    
    Returns:
        bool: `True` if the connection is authenticated and may proceed, `False` if the connection was closed due to failed authentication.
    """
    if not check_websocket_auth(websocket):
        await websocket.close(code=4001, reason="Authentication required")
        return False
    return True