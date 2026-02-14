"""Configuration module — loads and validates environment variables."""

import logging
import os
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Required: Slack tokens
    slack_bot_token: str
    slack_app_token: str

    # Required: Claude Code authentication (at least one)
    anthropic_api_key: str | None = None
    claude_code_oauth_token: str | None = None

    # Optional
    bender_workspace: Path = Path.cwd()
    bender_api_port: int = 8080
    log_level: str = "info"

    # Optional: API key for authenticating external HTTP requests
    bender_api_key: str | None = None

    # Optional: API mode (claude or ollama)
    bender_api_mode: str = "claude"  # "claude" or "ollama"

    # Optional: Model to use (for ollama mode or custom Claude models)
    # Will be populated by validator from OLLAMA_MODEL or ANTHROPIC_MODEL
    anthropic_model: str | None = None

    # Optional: Custom base URL (for Ollama proxy)
    anthropic_base_url: str | None = None

    # Optional: Timeout for Claude Code invocations (in seconds)
    # Set to 0 for no timeout (unlimited)
    # Default: 7200 (2 hours)
    claude_timeout: int = 7200

    model_config = {
        "case_sensitive": False,
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # Ignore extra env vars like OLLAMA_MODEL_7b, MINIMAX_MODEL_CHAT, etc.
    }

    @model_validator(mode="before")
    @classmethod
    def load_model_from_env(cls, data: dict) -> dict:
        """Load model from provider-specific env vars (OLLAMA_MODEL, MINIMAX_MODEL, etc.)."""
        if "anthropic_model" not in data or data["anthropic_model"] is None:
            # Try provider-specific models first, then generic ANTHROPIC_MODEL
            model = (
                os.getenv("OLLAMA_MODEL")
                or os.getenv("MINIMAX_MODEL")
                or os.getenv("NVIDIA_MODEL")
                or os.getenv("ANTHROPIC_MODEL")
            )
            if model:
                data["anthropic_model"] = model
        return data

    def validate_auth(self) -> None:
        """Ensure at least one Claude Code authentication method is configured."""
        if not self.anthropic_api_key and not self.claude_code_oauth_token:
            raise ValueError(
                "At least one authentication method is required: "
                "ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN"
            )


def configure_logging(level: str) -> None:
    """Configure application-wide logging."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_settings() -> Settings:
    """Load settings from environment, validate, and configure logging."""
    settings = Settings()
    settings.validate_auth()
    configure_logging(settings.log_level)
    return settings
