"""Entry point for the Bender application."""

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from bender.app import create_app, start
from bender.config import load_settings
from bender.interactive import prompt_api_mode, should_prompt_api_mode

logger = logging.getLogger(__name__)


async def main() -> None:
    """Start the Bender application."""
    # Load .env file first (so interactive prompts can check environment variables)
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file)

    # Interactive prompt for API mode selection (if not already configured)
    if should_prompt_api_mode():
        api_mode, model = prompt_api_mode()
        os.environ["BENDER_API_MODE"] = api_mode
        if model:
            # Set the model for the selected provider
            if api_mode == "ollama":
                os.environ["OLLAMA_MODEL"] = model
            elif api_mode == "minimax":
                os.environ["MINIMAX_MODEL"] = model
            elif api_mode == "nvidia":
                os.environ["NVIDIA_MODEL"] = model
            else:
                os.environ["ANTHROPIC_MODEL"] = model

    settings = load_settings()

    # Log API mode configuration
    api_mode = settings.bender_api_mode.upper()
    if api_mode != "CLAUDE":
        logger.info(
            "🤖 Bender starting in %s mode | model=%s | base_url=%s",
            api_mode,
            settings.anthropic_model or "not specified",
            settings.anthropic_base_url or "not specified",
        )
    else:
        logger.info("🤖 Bender starting in CLAUDE mode (Anthropic Cloud)")

    logger.info(
        "Workspace: %s | Port: %d",
        settings.bender_workspace,
        settings.bender_api_port,
    )

    app = create_app(settings)
    await start(app, settings)


if __name__ == "__main__":
    asyncio.run(main())
