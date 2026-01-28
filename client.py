"""
Claude SDK Client Configuration
===============================

Functions for creating and configuring the Claude Agent SDK client.
"""

import json
import logging
import os
import shutil
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import HookContext, HookInput, HookMatcher, SyncHookJSONOutput
from dotenv import load_dotenv

from security import bash_security_hook

# Module logger
logger = logging.getLogger(__name__)

# Load environment variables from .env file if present
load_dotenv()

# Default Playwright headless mode - can be overridden via PLAYWRIGHT_HEADLESS env var
# When True, browser runs invisibly in background (default - saves CPU)
# When False, browser window is visible (useful for monitoring agent progress)
DEFAULT_PLAYWRIGHT_HEADLESS = True

# Default browser for Playwright - can be overridden via PLAYWRIGHT_BROWSER env var
# Options: chrome, firefox, webkit, msedge
# Firefox is recommended for lower CPU usage
DEFAULT_PLAYWRIGHT_BROWSER = "firefox"

# Environment variables to pass through to Claude CLI for API configuration
# These allow using alternative API endpoints (e.g., GLM via z.ai) without
# affecting the user's global Claude Code settings
API_ENV_VARS = [
    "ANTHROPIC_BASE_URL",              # Custom API endpoint (e.g., https://api.z.ai/api/anthropic)
    "ANTHROPIC_AUTH_TOKEN",            # API authentication token
    "API_TIMEOUT_MS",                  # Request timeout in milliseconds
    "ANTHROPIC_DEFAULT_SONNET_MODEL",  # Model override for Sonnet
    "ANTHROPIC_DEFAULT_OPUS_MODEL",    # Model override for Opus
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",   # Model override for Haiku
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",   # Max output tokens (default 32000, GLM 4.7 supports 131072)
]

# Default max output tokens for GLM 4.7 compatibility (131k output limit)
DEFAULT_MAX_OUTPUT_TOKENS = "131072"


def get_playwright_headless() -> bool:
    """
    Return whether Playwright should run in headless mode.
    
    Reads the PLAYWRIGHT_HEADLESS environment variable and interprets "true", "1", "yes", "on" as headless and "false", "0", "no", "off" as headed. If the value is unset or invalid, the function falls back to DEFAULT_PLAYWRIGHT_HEADLESS.
    
    Returns:
        `true` if headless, `false` otherwise.
    """
    value = os.getenv("PLAYWRIGHT_HEADLESS", str(DEFAULT_PLAYWRIGHT_HEADLESS).lower()).strip().lower()
    truthy = {"true", "1", "yes", "on"}
    falsy = {"false", "0", "no", "off"}
    if value not in truthy | falsy:
        logger.warning(f"Invalid PLAYWRIGHT_HEADLESS='{value}', defaulting to {DEFAULT_PLAYWRIGHT_HEADLESS}")
        return DEFAULT_PLAYWRIGHT_HEADLESS
    return value in truthy


# Valid browsers supported by Playwright MCP
VALID_PLAYWRIGHT_BROWSERS = {"chrome", "firefox", "webkit", "msedge"}


def get_playwright_browser() -> str:
    """
    Get the browser to use for Playwright.

    Reads from PLAYWRIGHT_BROWSER environment variable, defaults to firefox.
    Options: chrome, firefox, webkit, msedge
    Firefox is recommended for lower CPU usage.
    """
    value = os.getenv("PLAYWRIGHT_BROWSER", DEFAULT_PLAYWRIGHT_BROWSER).strip().lower()
    if value not in VALID_PLAYWRIGHT_BROWSERS:
        print(f"   - Warning: Invalid PLAYWRIGHT_BROWSER='{value}', "
              f"valid options: {', '.join(sorted(VALID_PLAYWRIGHT_BROWSERS))}. "
              f"Defaulting to {DEFAULT_PLAYWRIGHT_BROWSER}")
        return DEFAULT_PLAYWRIGHT_BROWSER
    return value


# Feature MCP tools for feature/test management
FEATURE_MCP_TOOLS = [
    # Core feature operations
    "mcp__features__feature_get_stats",
    "mcp__features__feature_get_by_id",  # Get assigned feature details
    "mcp__features__feature_get_summary",  # Lightweight: id, name, status, deps only
    "mcp__features__feature_mark_in_progress",
    "mcp__features__feature_claim_and_get",  # Atomic claim + get details
    "mcp__features__feature_mark_passing",
    "mcp__features__feature_mark_failing",  # Mark regression detected
    "mcp__features__feature_skip",
    "mcp__features__feature_create_bulk",
    "mcp__features__feature_create",
    "mcp__features__feature_clear_in_progress",
    "mcp__features__feature_release_testing",  # Release testing claim
    # Dependency management
    "mcp__features__feature_add_dependency",
    "mcp__features__feature_remove_dependency",
    "mcp__features__feature_set_dependencies",
    # Query tools
    "mcp__features__feature_get_ready",
    "mcp__features__feature_get_blocked",
    "mcp__features__feature_get_graph",
]

# Playwright MCP tools for browser automation
PLAYWRIGHT_TOOLS = [
    # Core navigation & screenshots
    "mcp__playwright__browser_navigate",
    "mcp__playwright__browser_navigate_back",
    "mcp__playwright__browser_take_screenshot",
    "mcp__playwright__browser_snapshot",

    # Element interaction
    "mcp__playwright__browser_click",
    "mcp__playwright__browser_type",
    "mcp__playwright__browser_fill_form",
    "mcp__playwright__browser_select_option",
    "mcp__playwright__browser_hover",
    "mcp__playwright__browser_drag",
    "mcp__playwright__browser_press_key",

    # JavaScript & debugging
    "mcp__playwright__browser_evaluate",
    # "mcp__playwright__browser_run_code",  # REMOVED - causes Playwright MCP server crash
    "mcp__playwright__browser_console_messages",
    "mcp__playwright__browser_network_requests",

    # Browser management
    "mcp__playwright__browser_close",
    "mcp__playwright__browser_resize",
    "mcp__playwright__browser_tabs",
    "mcp__playwright__browser_wait_for",
    "mcp__playwright__browser_handle_dialog",
    "mcp__playwright__browser_file_upload",
    "mcp__playwright__browser_install",
]

# Built-in tools
BUILTIN_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "WebFetch",
    "WebSearch",
]


def create_client(
    project_dir: Path,
    model: str,
    yolo_mode: bool = False,
    agent_id: str | None = None,
):
    """
    Create and configure a ClaudeSDKClient for a project with layered security and optional Playwright integration.
    
    Ensures the project directory exists and writes a per-project security settings file (.claude_settings.json), configures allowed tools, permissions, MCP servers (features and optional Playwright), security hooks, compaction hook, and API environment overrides passed to the Claude CLI subprocess.
    
    Parameters:
        project_dir (Path): Project directory used as the client's working directory and the scope for filesystem permissions; created if it does not exist.
        model (str): Claude model identifier to use.
        yolo_mode (bool): If True, omit the Playwright MCP server and related Playwright tools/permissions for faster prototyping.
        agent_id (str | None): Optional identifier to enable isolated browser contexts per agent when Playwright is enabled.
    
    Returns:
        ClaudeSDKClient: A fully configured ClaudeSDKClient instance ready to run within the specified project directory.
    """
    # Build allowed tools list based on mode
    # In YOLO mode, exclude Playwright tools for faster prototyping
    allowed_tools = [*BUILTIN_TOOLS, *FEATURE_MCP_TOOLS]
    if not yolo_mode:
        allowed_tools.extend(PLAYWRIGHT_TOOLS)

    # Build permissions list
    permissions_list = [
        # Allow all file operations within the project directory
        "Read(./**)",
        "Write(./**)",
        "Edit(./**)",
        "Glob(./**)",
        "Grep(./**)",
        # Bash permission granted here, but actual commands are validated
        # by the bash_security_hook (see security.py for allowed commands)
        "Bash(*)",
        # Allow web tools for documentation lookup
        "WebFetch",
        "WebSearch",
        # Allow Feature MCP tools for feature management
        *FEATURE_MCP_TOOLS,
    ]
    if not yolo_mode:
        # Allow Playwright MCP tools for browser automation (standard mode only)
        permissions_list.extend(PLAYWRIGHT_TOOLS)

    # Create comprehensive security settings
    # Note: Using relative paths ("./**") restricts access to project directory
    # since cwd is set to project_dir
    security_settings = {
        "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True},
        "permissions": {
            "defaultMode": "acceptEdits",  # Auto-approve edits within allowed directories
            "allow": permissions_list,
        },
    }

    # Ensure project directory exists before creating settings file
    project_dir.mkdir(parents=True, exist_ok=True)

    # Write settings to a file in the project directory
    settings_file = project_dir / ".claude_settings.json"
    with open(settings_file, "w") as f:
        json.dump(security_settings, f, indent=2)

    logger.info(f"Created security settings at {settings_file}")
    logger.debug("  Sandbox enabled (OS-level bash isolation)")
    logger.debug(f"  Filesystem restricted to: {project_dir.resolve()}")
    logger.debug("  Bash commands restricted to allowlist (see security.py)")
    if yolo_mode:
        logger.info("  MCP servers: features (database) - YOLO MODE (no Playwright)")
    else:
        logger.debug("  MCP servers: playwright (browser), features (database)")
    logger.debug("  Project settings enabled (skills, commands, CLAUDE.md)")

    # Use system Claude CLI instead of bundled one (avoids Bun runtime crash on Windows)
    system_cli = shutil.which("claude")
    if system_cli:
        logger.debug(f"Using system CLI: {system_cli}")
    else:
        logger.warning("System 'claude' CLI not found, using bundled CLI")

    # Build MCP servers config - features is always included, playwright only in standard mode
    mcp_servers = {
        "features": {
            "command": sys.executable,  # Use the same Python that's running this script
            "args": ["-m", "mcp_server.feature_mcp"],
            "env": {
                # Only specify variables the MCP server needs
                # (subprocess inherits parent environment automatically)
                "PROJECT_DIR": str(project_dir.resolve()),
                "PYTHONPATH": str(Path(__file__).parent.resolve()),
            },
        },
    }
    if not yolo_mode:
        # Include Playwright MCP server for browser automation (standard mode only)
        # Browser and headless mode configurable via environment variables
        browser = get_playwright_browser()
        playwright_args = [
            "@playwright/mcp@latest",
            "--viewport-size", "1280x720",
            "--browser", browser,
        ]
        if get_playwright_headless():
            playwright_args.append("--headless")
        logger.debug(f"Browser: {browser} (headless={get_playwright_headless()})")

        # Browser isolation for parallel execution
        # Each agent gets its own isolated browser context to prevent tab conflicts
        if agent_id:
            # Use --isolated for ephemeral browser context
            # This creates a fresh, isolated context without persistent state
            # Note: --isolated and --user-data-dir are mutually exclusive
            playwright_args.append("--isolated")
            logger.debug(f"Browser isolation enabled for agent: {agent_id}")

        mcp_servers["playwright"] = {
            "command": "npx",
            "args": playwright_args,
        }

    # Build environment overrides for API endpoint configuration
    # These override system env vars for the Claude CLI subprocess,
    # allowing AutoCoder to use alternative APIs (e.g., GLM) without
    # affecting the user's global Claude Code settings
    sdk_env = {}
    for var in API_ENV_VARS:
        value = os.getenv(var)
        if value:
            sdk_env[var] = value

    # Set default max output tokens for GLM 4.7 compatibility if not already set
    if "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in sdk_env:
        sdk_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = DEFAULT_MAX_OUTPUT_TOKENS

    # Detect alternative API mode (Ollama or GLM)
    base_url = sdk_env.get("ANTHROPIC_BASE_URL", "")
    is_alternative_api = bool(base_url)
    is_ollama = "localhost:11434" in base_url or "127.0.0.1:11434" in base_url

    if sdk_env:
        logger.info(f"API overrides: {', '.join(sdk_env.keys())}")
        if is_ollama:
            logger.info("Ollama Mode: Using local models")
        elif "ANTHROPIC_BASE_URL" in sdk_env:
            logger.info(f"GLM Mode: Using {sdk_env['ANTHROPIC_BASE_URL']}")

    # Create a wrapper for bash_security_hook that passes project_dir via context
    async def bash_hook_with_context(input_data, tool_use_id=None, context=None):
        """
        Injects the project directory into the hook context and delegates validation to the bash security hook.
        
        Parameters:
            input_data: The hook input payload provided to the bash security hook.
            tool_use_id (optional): Identifier for the tool invocation, if available.
            context (optional): Existing hook context; the function will add a `project_dir` entry (absolute path) before invoking the security hook.
        
        Returns:
            The JSON output returned by `bash_security_hook` (hook validation result).
        """
        if context is None:
            context = {}
        context["project_dir"] = str(project_dir.resolve())
        return await bash_security_hook(input_data, tool_use_id, context)

    # PreCompact hook for logging and customizing context compaction
    # Compaction is handled automatically by Claude Code CLI when context approaches limits.
    # This hook allows us to log when compaction occurs and optionally provide custom instructions.
    async def pre_compact_hook(
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> SyncHookJSONOutput:
        """
        Called before the agent's context is compacted to allow optional guidance or to override compaction behavior.
        
        This hook reads input_data keys:
        - "trigger": either "auto" when compaction is automatic or "manual" when user-initiated.
        - "custom_instructions": optional string with summarization focus areas; if provided it will be logged.
        
        Parameters:
            input_data (HookInput): Hook input; may include "trigger" and "custom_instructions".
            tool_use_id (str | None): Identifier for the tool use that triggered the hook (may be None).
            context (HookContext): Current hook execution context.
        
        Returns:
            SyncHookJSONOutput: Empty output to permit the default compaction behavior, or a structure with `"hookSpecificOutput"` containing `"hookEventName": "PreCompact"` and `"customInstructions"` to customize compaction.
        """
        trigger = input_data.get("trigger", "auto")
        custom_instructions = input_data.get("custom_instructions")

        if trigger == "auto":
            logger.info("Auto-compaction triggered (context approaching limit)")
        else:
            logger.info("Manual compaction requested")

        if custom_instructions:
            logger.info(f"Compaction custom instructions: {custom_instructions}")

        # Return empty dict to allow compaction to proceed with default behavior
        # To customize, return:
        # {
        #     "hookSpecificOutput": {
        #         "hookEventName": "PreCompact",
        #         "customInstructions": "Focus on preserving file paths and test results"
        #     }
        # }
        return SyncHookJSONOutput()

    return ClaudeSDKClient(
        options=ClaudeAgentOptions(
            model=model,
            cli_path=system_cli,  # Use system CLI to avoid bundled Bun crash (exit code 3)
            system_prompt="You are an expert full-stack developer building a production-quality web application.",
            setting_sources=["project"],  # Enable skills, commands, and CLAUDE.md from project dir
            max_buffer_size=10 * 1024 * 1024,  # 10MB for large Playwright screenshots
            allowed_tools=allowed_tools,
            mcp_servers=mcp_servers,
            hooks={
                "PreToolUse": [
                    HookMatcher(matcher="Bash", hooks=[bash_hook_with_context]),
                ],
                # PreCompact hook for context management during long sessions.
                # Compaction is automatic when context approaches token limits.
                # This hook logs compaction events and can customize summarization.
                "PreCompact": [
                    HookMatcher(hooks=[pre_compact_hook]),
                ],
            },
            max_turns=1000,
            cwd=str(project_dir.resolve()),
            settings=str(settings_file.resolve()),  # Use absolute path
            env=sdk_env,  # Pass API configuration overrides to CLI subprocess
            # Enable extended context beta for better handling of long sessions.
            # This provides up to 1M tokens of context with automatic compaction.
            # See: https://docs.anthropic.com/en/api/beta-headers
            # Disabled for alternative APIs (Ollama, GLM) as they don't support Claude-specific betas.
            betas=[] if is_alternative_api else ["context-1m-2025-08-07"],
            # Note on context management:
            # The Claude Agent SDK handles context management automatically through the
            # underlying Claude Code CLI. When context approaches limits, the CLI
            # automatically compacts/summarizes previous messages.
            #
            # The SDK does NOT expose explicit compaction_control or context_management
            # parameters. Instead, context is managed via:
            # 1. betas=["context-1m-2025-08-07"] - Extended context window
            # 2. PreCompact hook - Intercept and customize compaction behavior
            # 3. max_turns - Limit conversation turns (set to 1000 for long sessions)
            #
            # Future SDK versions may add explicit compaction controls. When available,
            # consider adding:
            # - compaction_control={"enabled": True, "context_token_threshold": 80000}
            # - context_management={"edits": [...]} for tool use clearing
        )
    )