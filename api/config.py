"""
Autocoder Configuration
=======================

Centralized configuration using Pydantic BaseSettings.
Loads settings from environment variables and .env files.
"""

from typing import Optional
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AutocoderConfig(BaseSettings):
    """Centralized configuration for Autocoder.

    Settings are loaded from:
    1. Environment variables (highest priority)
    2. .env file in project root
    3. Default values (lowest priority)

    Usage:
        config = AutocoderConfig()
        print(config.playwright_browser)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra env vars
    )

    # ==========================================================================
    # API Configuration
    # ==========================================================================

    anthropic_base_url: Optional[str] = Field(
        default=None,
        description="Base URL for Anthropic-compatible API"
    )

    anthropic_auth_token: Optional[str] = Field(
        default=None,
        description="Auth token for Anthropic-compatible API"
    )

    anthropic_api_key: Optional[str] = Field(
        default=None,
        description="Anthropic API key (if using Claude directly)"
    )

    api_timeout_ms: int = Field(
        default=120000,
        description="API request timeout in milliseconds"
    )

    # ==========================================================================
    # Model Configuration
    # ==========================================================================

    anthropic_default_sonnet_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Default model for Sonnet tier"
    )

    anthropic_default_opus_model: str = Field(
        default="claude-opus-4-20250514",
        description="Default model for Opus tier"
    )

    anthropic_default_haiku_model: str = Field(
        default="claude-haiku-3-5-20241022",
        description="Default model for Haiku tier"
    )

    # ==========================================================================
    # Playwright Configuration
    # ==========================================================================

    playwright_browser: str = Field(
        default="firefox",
        description="Browser to use for testing (firefox, chrome, webkit, msedge)"
    )

    playwright_headless: bool = Field(
        default=True,
        description="Run browser in headless mode"
    )

    # ==========================================================================
    # Webhook Configuration
    # ==========================================================================

    progress_n8n_webhook_url: Optional[str] = Field(
        default=None,
        description="N8N webhook URL for progress notifications"
    )

    # ==========================================================================
    # Server Configuration
    # ==========================================================================

    autocoder_allow_remote: bool = Field(
        default=False,
        description="Allow remote access to the server"
    )

    # ==========================================================================
    # Computed Properties
    # ==========================================================================

    @property
    def is_using_alternative_api(self) -> bool:
        """
        Indicates whether an alternative Anthropic-compatible API endpoint and auth token are configured.
        
        Returns:
            True if both `anthropic_base_url` and `anthropic_auth_token` are set, False otherwise.
        """
        return bool(self.anthropic_base_url and self.anthropic_auth_token)

    @property
    def is_using_ollama(self) -> bool:
        """
        Determine whether the configuration targets a local Ollama instance.
        
        Returns:
            `true` if `anthropic_base_url` is set, `anthropic_auth_token` equals `"ollama"`, and the base URL's hostname is `localhost`, `127.0.0.1`, or `::1`; `false` otherwise.
        """
        if not self.anthropic_base_url or self.anthropic_auth_token != "ollama":
            return False
        host = urlparse(self.anthropic_base_url).hostname or ""
        return host in {"localhost", "127.0.0.1", "::1"}


# Global config instance (lazy loaded)
_config: Optional[AutocoderConfig] = None


def get_config() -> AutocoderConfig:
    """
    Retrieve the global AutocoderConfig singleton.
    
    Creates and caches the AutocoderConfig on first access by loading settings from the environment and .env file.
    
    Returns:
        The global AutocoderConfig instance; created on first access if not already initialized.
    """
    global _config
    if _config is None:
        _config = AutocoderConfig()
    return _config


def reload_config() -> AutocoderConfig:
    """
    Reloads the global AutocoderConfig by re-reading environment variables.
    
    Returns:
        AutocoderConfig: The reloaded configuration instance.
    """
    global _config
    _config = AutocoderConfig()
    return _config