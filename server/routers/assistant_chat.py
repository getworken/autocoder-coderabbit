"""
Assistant Chat Router
=====================

WebSocket and REST endpoints for the read-only project assistant.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..services.assistant_chat_session import (
    AssistantChatSession,
    create_session,
    get_session,
    list_sessions,
    remove_session,
)
from ..services.assistant_database import (
    create_conversation,
    delete_conversation,
    get_conversation,
    get_conversations,
)
from ..utils.auth import reject_unauthenticated_websocket
from ..utils.validation import is_valid_project_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assistant", tags=["assistant-chat"])

# Root directory
ROOT_DIR = Path(__file__).parent.parent.parent


def _get_project_path(project_name: str) -> Optional[Path]:
    """
    Return the filesystem path of a registered project.
    
    Parameters:
        project_name (str): Name of the project to look up.
    
    Returns:
        Optional[Path]: Path to the project's root directory if found, otherwise None.
    """
    import sys
    root = Path(__file__).parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from registry import get_project_path
    return get_project_path(project_name)


# ============================================================================
# Pydantic Models
# ============================================================================

class ConversationSummary(BaseModel):
    """Summary of a conversation."""
    id: int
    project_name: str
    title: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    message_count: int


class ConversationMessageModel(BaseModel):
    """A message within a conversation."""
    id: int
    role: str
    content: str
    timestamp: Optional[str]


class ConversationDetail(BaseModel):
    """Full conversation with messages."""
    id: int
    project_name: str
    title: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    messages: list[ConversationMessageModel]


class SessionInfo(BaseModel):
    """Active session information."""
    project_name: str
    conversation_id: Optional[int]
    is_active: bool


# ============================================================================
# REST Endpoints - Conversation Management
# ============================================================================

@router.get("/conversations/{project_name}", response_model=list[ConversationSummary])
async def list_project_conversations(project_name: str):
    """
    List all conversations for the given project.
    
    Returns:
        List[ConversationSummary]: Conversation summaries for the project.
    
    Raises:
        HTTPException: 400 if `project_name` is invalid; 404 if the project does not exist.
    """
    if not is_valid_project_name(project_name):
        raise HTTPException(status_code=400, detail="Invalid project name")

    project_dir = _get_project_path(project_name)
    if not project_dir or not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    conversations = get_conversations(project_dir, project_name)
    return [ConversationSummary(**c) for c in conversations]


@router.get("/conversations/{project_name}/{conversation_id}", response_model=ConversationDetail)
async def get_project_conversation(project_name: str, conversation_id: int):
    """
    Retrieve a conversation and its messages for the given project.
    
    Parameters:
        project_name (str): Project identifier to look up.
        conversation_id (int): Numeric ID of the conversation to retrieve.
    
    Returns:
        ConversationDetail: Conversation metadata and a list of messages as ConversationMessageModel entries.
    
    Raises:
        HTTPException: 400 if the project name is invalid.
        HTTPException: 404 if the project or the conversation is not found.
    """
    if not is_valid_project_name(project_name):
        raise HTTPException(status_code=400, detail="Invalid project name")

    project_dir = _get_project_path(project_name)
    if not project_dir or not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    conversation = get_conversation(project_dir, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationDetail(
        id=conversation["id"],
        project_name=conversation["project_name"],
        title=conversation["title"],
        created_at=conversation["created_at"],
        updated_at=conversation["updated_at"],
        messages=[ConversationMessageModel(**m) for m in conversation["messages"]],
    )


@router.post("/conversations/{project_name}", response_model=ConversationSummary)
async def create_project_conversation(project_name: str):
    """
    Create a new conversation for the given project and return its summary.
    
    Parameters:
        project_name (str): The project's registry name.
    
    Returns:
        ConversationSummary: Summary of the newly created conversation (message_count is 0).
    
    Raises:
        HTTPException: 400 if `project_name` is invalid; 404 if the project cannot be found.
    """
    if not is_valid_project_name(project_name):
        raise HTTPException(status_code=400, detail="Invalid project name")

    project_dir = _get_project_path(project_name)
    if not project_dir or not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    conversation = create_conversation(project_dir, project_name)
    return ConversationSummary(
        id=conversation.id,
        project_name=conversation.project_name,
        title=conversation.title,
        created_at=conversation.created_at.isoformat() if conversation.created_at else None,
        updated_at=conversation.updated_at.isoformat() if conversation.updated_at else None,
        message_count=0,
    )


@router.delete("/conversations/{project_name}/{conversation_id}")
async def delete_project_conversation(project_name: str, conversation_id: int):
    """
    Delete a conversation for a given project.
    
    Parameters:
        project_name (str): Name of the project containing the conversation.
        conversation_id (int): Identifier of the conversation to delete.
    
    Returns:
        dict: {"success": True, "message": "Conversation deleted"} on successful deletion.
    
    Raises:
        HTTPException: 400 if the project name is invalid.
        HTTPException: 404 if the project does not exist or the conversation is not found.
    """
    if not is_valid_project_name(project_name):
        raise HTTPException(status_code=400, detail="Invalid project name")

    project_dir = _get_project_path(project_name)
    if not project_dir or not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    success = delete_conversation(project_dir, conversation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"success": True, "message": "Conversation deleted"}


# ============================================================================
# REST Endpoints - Session Management
# ============================================================================

@router.get("/sessions", response_model=list[str])
async def list_active_sessions():
    """List all active assistant sessions."""
    return list_sessions()


@router.get("/sessions/{project_name}", response_model=SessionInfo)
async def get_session_info(project_name: str):
    """
    Retrieve information about the active session for a project.
    
    Parameters:
        project_name (str): Project identifier to query.
    
    Returns:
        SessionInfo: The active session's info containing `project_name`, `conversation_id`, and `is_active` set to `True`.
    
    Raises:
        HTTPException: 400 if `project_name` is invalid; 404 if no active session exists for the project.
    """
    if not is_valid_project_name(project_name):
        raise HTTPException(status_code=400, detail="Invalid project name")

    session = get_session(project_name)
    if not session:
        raise HTTPException(status_code=404, detail="No active session for this project")

    return SessionInfo(
        project_name=project_name,
        conversation_id=session.get_conversation_id(),
        is_active=True,
    )


@router.delete("/sessions/{project_name}")
async def close_session(project_name: str):
    """
    Close the active assistant session for the specified project.
    
    Parameters:
        project_name (str): Name of the project whose session should be closed.
    
    Returns:
        dict: {"success": True, "message": "Session closed"} on successful closure.
    
    Raises:
        HTTPException: 400 if `project_name` is invalid; 404 if no active session exists for the project.
    """
    if not is_valid_project_name(project_name):
        raise HTTPException(status_code=400, detail="Invalid project name")

    session = get_session(project_name)
    if not session:
        raise HTTPException(status_code=404, detail="No active session for this project")

    await remove_session(project_name)
    return {"success": True, "message": "Session closed"}


# ============================================================================
# WebSocket Endpoint
# ============================================================================

@router.websocket("/ws/{project_name}")
async def assistant_chat_websocket(websocket: WebSocket, project_name: str):
    """
    Handle a WebSocket connection for assistant chat for the specified project.
    
    This endpoint accepts a persistent WebSocket and implements a simple JSON message protocol to start or resume conversations, forward user messages to the assistant, and stream assistant responses back to the client.
    
    Client -> Server messages:
    - {"type": "start", "conversation_id": int | null} — start a new session or resume if conversation_id provided
    - {"type": "resume", "conversation_id": int} — resume an existing conversation without sending the greeting
    - {"type": "message", "content": "..."} — send user message to the assistant
    - {"type": "ping"} — keep-alive ping
    
    Server -> Client messages:
    - {"type": "conversation_created", "conversation_id": int} — confirms a conversation was created/resumed
    - {"type": "text", "content": "..."} — text chunk from the assistant
    - {"type": "tool_call", "tool": "...", "input": {...}} — assistant is invoking a tool
    - {"type": "response_done"} — assistant finished its response
    - {"type": "error", "content": "..."} — error description
    - {"type": "pong"} — keep-alive pong
    
    Behavior notes:
    - Invalid project names or missing project directories result in the connection being closed.
    - Assistant responses are streamed as JSON chunks.
    - On disconnect the server retains session state so the client may resume later.
    """
    # Check authentication if Basic Auth is enabled
    if not await reject_unauthenticated_websocket(websocket):
        return

    if not is_valid_project_name(project_name):
        await websocket.close(code=4000, reason="Invalid project name")
        return

    project_dir = _get_project_path(project_name)
    if not project_dir:
        await websocket.close(code=4004, reason="Project not found in registry")
        return

    if not project_dir.exists():
        await websocket.close(code=4004, reason="Project directory not found")
        return

    await websocket.accept()
    logger.info(f"Assistant WebSocket connected for project: {project_name}")

    session: Optional[AssistantChatSession] = None

    try:
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                msg_type = message.get("type")
                logger.debug(f"Assistant received message type: {msg_type}")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                elif msg_type == "start":
                    # Get optional conversation_id to resume
                    conversation_id = message.get("conversation_id")
                    logger.debug(f"Processing start message with conversation_id={conversation_id}")

                    try:
                        # Create a new session
                        logger.debug(f"Creating session for {project_name}")
                        session = await create_session(
                            project_name,
                            project_dir,
                            conversation_id=conversation_id,
                        )
                        logger.debug("Session created, starting...")

                        # Stream the initial greeting
                        async for chunk in session.start():
                            if logger.isEnabledFor(logging.DEBUG):
                                logger.debug(f"Sending chunk: {chunk.get('type')}")
                            await websocket.send_json(chunk)
                        logger.debug("Session start complete")
                    except Exception as e:
                        logger.exception(f"Error starting assistant session for {project_name}")
                        await websocket.send_json({
                            "type": "error",
                            "content": f"Failed to start session: {str(e)}"
                        })

                elif msg_type == "resume":
                    # Resume an existing conversation without sending greeting
                    conversation_id = message.get("conversation_id")

                    # Validate conversation_id is present and valid
                    if not conversation_id or not isinstance(conversation_id, int):
                        logger.warning(f"Invalid resume request for {project_name}: missing or invalid conversation_id")
                        await websocket.send_json({
                            "type": "error",
                            "content": "Missing or invalid conversation_id for resume"
                        })
                        continue

                    try:
                        # Create session
                        session = await create_session(
                            project_name,
                            project_dir,
                            conversation_id=conversation_id,
                        )
                        # Initialize but skip the greeting
                        async for chunk in session.start(skip_greeting=True):
                            await websocket.send_json(chunk)
                        # Confirm we're ready
                        await websocket.send_json({
                            "type": "conversation_created",
                            "conversation_id": conversation_id,
                        })
                    except Exception as e:
                        logger.exception(f"Error resuming assistant session for {project_name}")
                        await websocket.send_json({
                            "type": "error",
                            "content": f"Failed to resume session: {str(e)}"
                        })

                elif msg_type == "message":
                    if not session:
                        session = get_session(project_name)
                        if not session:
                            await websocket.send_json({
                                "type": "error",
                                "content": "No active session. Send 'start' first."
                            })
                            continue

                    user_content = message.get("content", "").strip()
                    if not user_content:
                        await websocket.send_json({
                            "type": "error",
                            "content": "Empty message"
                        })
                        continue

                    # Stream Claude's response
                    async for chunk in session.send_message(user_content):
                        await websocket.send_json(chunk)

                else:
                    await websocket.send_json({
                        "type": "error",
                        "content": f"Unknown message type: {msg_type}"
                    })

            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "content": "Invalid JSON"
                })

    except WebSocketDisconnect:
        logger.info(f"Assistant chat WebSocket disconnected for {project_name}")

    except Exception as e:
        logger.exception(f"Assistant chat WebSocket error for {project_name}")
        try:
            await websocket.send_json({
                "type": "error",
                "content": f"Server error: {str(e)}"
            })
        except Exception:
            pass

    finally:
        # Don't remove session on disconnect - allow resume
        pass