"""Interactive prompts for user configuration."""

import os
import sys


def _get_available_models(provider: str) -> dict[str, str]:
    """
    Get available models for a provider from environment variables.

    Args:
        provider: Provider name (e.g., "OLLAMA", "MINIMAX")

    Returns:
        dict: {display_name: model_value}
    """
    models = {}
    prefix = f"{provider}_MODEL_"

    for key, value in os.environ.items():
        if key.startswith(prefix):
            # Extract display name (e.g., "OLLAMA_MODEL_14B" -> "14B")
            display_name = key[len(prefix):].replace("_", " ").title()
            models[display_name] = value

    # Also check for single model without suffix
    single_model = os.getenv(f"{provider}_MODEL")
    if single_model:
        models["Default"] = single_model

    return models


def _prompt_model_selection(provider: str, models: dict[str, str]) -> str:
    """
    Prompt user to select a model from available models.

    Args:
        provider: Provider name for display
        models: dict of {display_name: model_value}

    Returns:
        Selected model value
    """
    print(f"\n📋 Available {provider} models:")
    model_list = list(models.items())

    for idx, (display_name, model_value) in enumerate(model_list, 1):
        print(f"  {idx}) {display_name}: {model_value}")
    print()

    while True:
        choice = input(f"Select model (1-{len(model_list)}) [default: 1]: ").strip() or "1"

        try:
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(model_list):
                selected_model = model_list[choice_idx][1]
                print(f"✅ Selected: {selected_model}\n")
                return selected_model
            else:
                print(f"❌ Invalid choice. Please enter 1-{len(model_list)}.")
        except ValueError:
            print(f"❌ Invalid input. Please enter a number 1-{len(model_list)}.")


def prompt_api_mode() -> tuple[str, str | None]:
    """
    Prompt user to choose API provider and model.

    Returns:
        tuple: (api_mode, model_name)
            - api_mode: "claude", "ollama", "minimax", etc.
            - model_name: Model name for the selected provider, None if claude
    """
    print("\n" + "="*60)
    print("🤖 Bender - Claude Code Agent")
    print("="*60)

    # Detect available providers
    providers = []
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
        providers.append(("claude", "Claude API (Anthropic Cloud)"))

    ollama_models = _get_available_models("OLLAMA")
    if ollama_models or os.getenv("ANTHROPIC_BASE_URL"):
        providers.append(("ollama", f"Ollama API (Local Models) - {len(ollama_models)} models"))

    minimax_models = _get_available_models("MINIMAX")
    if minimax_models:
        providers.append(("minimax", f"MiniMax API - {len(minimax_models)} models"))

    nvidia_models = _get_available_models("NVIDIA")
    if nvidia_models:
        providers.append(("nvidia", f"NVIDIA Build API - {len(nvidia_models)} models"))

    if not providers:
        providers.append(("claude", "Claude API (Anthropic Cloud - default)"))

    print("\nSelect API provider:")
    for idx, (_, description) in enumerate(providers, 1):
        print(f"  {idx}) {description}")
    print()

    while True:
        choice = input(f"Enter your choice (1-{len(providers)}) [default: 1]: ").strip() or "1"

        try:
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(providers):
                api_mode, _ = providers[choice_idx]
                break
            else:
                print(f"❌ Invalid choice. Please enter 1-{len(providers)}.")
        except ValueError:
            print(f"❌ Invalid input. Please enter a number 1-{len(providers)}.")

    # Claude doesn't need model selection
    if api_mode == "claude":
        print("\n✅ Using Claude API (Anthropic Cloud)")
        return ("claude", None)

    # For other providers, check if multiple models are available
    if api_mode == "ollama":
        models = ollama_models
        provider_name = "Ollama"
        setup_info = [
            "   - Ollama running: ollama serve",
            "   - Model pulled: ollama pull <model>",
            "   - Proxy configured: ANTHROPIC_BASE_URL=http://localhost:11434",
        ]
    elif api_mode == "minimax":
        models = minimax_models
        provider_name = "MiniMax"
        setup_info = [
            "   - API key configured: ANTHROPIC_API_KEY",
            "   - Base URL set: ANTHROPIC_BASE_URL",
        ]
    elif api_mode == "nvidia":
        models = nvidia_models
        provider_name = "NVIDIA Build"
        setup_info = [
            "   - API key from: https://build.nvidia.com/settings/api-keys",
            "   - Set ANTHROPIC_API_KEY to your NVIDIA API key",
            "   - Set ANTHROPIC_BASE_URL=https://integrate.api.nvidia.com/v1",
        ]
    else:
        models = {}
        provider_name = api_mode.title()
        setup_info = []

    # If multiple models, let user choose
    if len(models) > 1:
        model = _prompt_model_selection(provider_name, models)
    elif len(models) == 1:
        model = list(models.values())[0]
        print(f"\n✅ Using {provider_name} with model: {model}")
    else:
        # No models configured, prompt for manual input
        model = input(f"Enter {provider_name} model name: ").strip()
        if not model:
            print(f"❌ Model name is required for {provider_name} mode")
            return prompt_api_mode()  # Retry

    if setup_info:
        print(f"\n⚠️  Make sure you have:")
        for info in setup_info:
            print(info)
        print()

    return (api_mode, model)


def should_prompt_api_mode() -> bool:
    """
    Check if we should prompt for API mode.

    Don't prompt if:
    - BENDER_API_MODE is already set (explicit configuration)
    - Running in non-interactive mode (CI, Docker, etc.)
    """
    # Already configured in environment
    if os.getenv("BENDER_API_MODE"):
        return False

    # Check if stdin is interactive
    if not sys.stdin.isatty():
        return False

    return True
