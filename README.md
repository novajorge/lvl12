# Bender

Open-source framework that connects Slack to Claude Code in headless mode. Bender provides a thin orchestration layer: receives messages from Slack, invokes Claude Code CLI, maintains conversational context using session management, and exposes an HTTP API for external triggers.

Bender is completely agnostic — it has no business logic, no predefined agent roles, and no integrations beyond Slack and Claude Code. Users inject their own behavior through `CLAUDE.md` files, skills, and agent team definitions.

## Architecture

```
                    Slack (Socket Mode - WebSocket)
                              |
                    +---------+---------+
                    |      Bender       |
                    |                   |
              +-----+-----+     +------+------+
              | slack-bolt |     |   FastAPI   |
              | @mention   |     |  HTTP API   |
              | thread     |     |  POST /api  |
              +-----+------+     +------+------+
                    |                   |
                    +--------+----------+
                             |
                    +--------+--------+
                    |   Claude Code   |
                    |   (headless)    |
                    |   --print       |
                    |   --resume      |
                    |   --session-id  |
                    +-----------------+
```

### Session Model

Each Slack thread maps to exactly one Claude Code session, enabling multi-turn conversations:

- New `@Bender` mention creates a new thread + new Claude Code session
- Reply in a Bender thread resumes the existing session with `--resume`
- External API call creates a new thread + new Claude Code session
- Sessions persist on disk (`~/.claude/projects/`) and survive process restarts

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) installed and in PATH
- A Slack app configured with Socket Mode

## Installation

```bash
# Clone the repository
git clone https://github.com/helmcode/bender.git
cd bender

# Create virtual environment and install dependencies
uv venv
uv pip install -e .

# For development (includes pytest, ruff, mypy)
uv pip install -e ".[dev]"
```

## Configuration

### Environment Variables

```bash
# Required: Slack
SLACK_BOT_TOKEN="xoxb-..."           # Bot User OAuth Token
SLACK_APP_TOKEN="xapp-..."           # App-Level Token (Socket Mode)

# Required: Claude Code authentication (at least one)
ANTHROPIC_API_KEY="sk-ant-..."       # API key (pay-per-use)
# OR
CLAUDE_CODE_OAUTH_TOKEN="..."        # Max subscription token

# Optional
BENDER_WORKSPACE="/home/agent"       # Working directory for Claude Code (default: cwd)
BENDER_API_PORT="8080"               # FastAPI port (default: 8080)
BENDER_API_KEY="your-secret-key"     # Bearer token for HTTP API authentication
LOG_LEVEL="info"                     # Logging level (default: info)
```

### Slack App Setup

1. Create a new Slack app at [api.slack.com/apps](https://api.slack.com/apps)
2. Enable **Socket Mode** and generate an App-Level Token (`xapp-...`)
3. Add the following **Bot Token Scopes**:
   - `app_mentions:read` — Listen for @Bender mentions
   - `chat:write` — Post messages and thread replies
   - `channels:history` — Read messages in public channels
   - `groups:history` — Read messages in private channels (if needed)
4. Subscribe to these **Events**:
   - `app_mention` — Trigger on @mentions
   - `message.channels` — Listen for thread replies
5. Install the app to your workspace and copy the Bot User OAuth Token (`xoxb-...`)

### Workspace Directory

The workspace is where Claude Code runs. It defines the agent's behavior, permissions, and available skills. The repository includes an example workspace in `workspace/` that you can use as a starting point:

```
workspace/                           # Example agent configuration
├── CLAUDE.md                        # Agent instructions (identity, behavior, rules)
├── .claude/
│   ├── commands/                    # Skills (slash commands available to the agent)
│   │   └── hello.md                # Example skill
│   └── settings.json               # Tool permissions (pre-approved commands)
└── (your project files)
```

**To customize:** Edit the `workspace/` contents or replace them entirely with your own configuration. The `CLAUDE.md` file controls the agent's behavior, `.claude/commands/` defines available skills, and `.claude/settings.json` sets which tools the agent can use without manual approval.

## Usage

### Running Bender

```bash
# Using the module
python -m bender

# Or with environment variables inline
SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-... ANTHROPIC_API_KEY=sk-ant-... python -m bender
```

Bender starts both the Slack Socket Mode handler and the FastAPI HTTP server concurrently.

### Slack Interaction

Mention `@Bender` in any channel where the bot is present:

```
User: @Bender What's the status of the deployment?
Bender: [Creates thread, invokes Claude Code, responds with result]

User (in thread): Can you rollback to the previous version?
Bender: [Resumes same Claude Code session, preserving context]
```

### HTTP API

Trigger Bender programmatically from external systems (cron jobs, webhooks, CI/CD):

```bash
# Invoke Claude Code via API
curl -X POST http://localhost:8080/api/invoke \
  -H "Authorization: Bearer your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"channel": "C0XXXXXXX01", "message": "Check deployment status"}'

# Health check
curl http://localhost:8080/health
```

**Response:**

```json
{
  "thread_ts": "1234567890.123456",
  "session_id": "abc-123-def-456",
  "response": "Claude Code's response text"
}
```

> **Note:** The `/api/invoke` endpoint requires a Bearer token (`BENDER_API_KEY`). If `BENDER_API_KEY` is not configured, the endpoint returns HTTP 503 (fail-closed behavior).

## Docker

```dockerfile
FROM python:3.12-slim

# Node.js + Claude Code CLI
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g @anthropic-ai/claude-code

# Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ /app/src/

WORKDIR /app
CMD ["python", "-m", "bender"]
```

```bash
# Build and run
docker build -t bender .
docker run -e SLACK_BOT_TOKEN=xoxb-... \
           -e SLACK_APP_TOKEN=xapp-... \
           -e ANTHROPIC_API_KEY=sk-ant-... \
           -e BENDER_WORKSPACE=/workspace \
           -v /path/to/workspace:/workspace \
           -p 8080:8080 \
           bender
```

## Project Structure

```
bender/
├── src/
│   └── bender/
│       ├── __init__.py            # Package metadata
│       ├── __main__.py            # Entry point
│       ├── app.py                 # FastAPI + slack-bolt wiring
│       ├── api.py                 # HTTP API endpoints (/api/invoke, /health)
│       ├── claude_code.py         # Claude Code CLI subprocess wrapper
│       ├── config.py              # Environment variable loading (pydantic-settings)
│       ├── session_manager.py     # Thread <-> Session mapping
│       ├── slack_handler.py       # Slack event handlers (@mention, thread replies)
│       └── slack_utils.py         # Message splitting utilities
├── tests/
│   ├── conftest.py                # Shared fixtures
│   ├── test_api.py                # API endpoint tests
│   ├── test_app.py                # App wiring tests
│   ├── test_claude_code.py        # CLI invocation tests
│   ├── test_config.py             # Config loading tests
│   ├── test_session_manager.py    # Session mapping tests
│   ├── test_slack_handler.py      # Slack handler tests
│   └── test_slack_utils.py        # Message splitting tests
├── workspace/                     # Example agent configuration (CLAUDE.md, skills, settings)
├── docker/                        # Infra-oriented Dockerfile (kubectl, vault, argocd)
├── pyproject.toml                 # Project metadata and dependencies
├── CLAUDE.md                      # Development instructions
└── README.md                      # This file
```

## Development

```bash
# Install with dev dependencies
uv venv
uv pip install -e ".[dev]"

# Run tests
.venv/bin/pytest

# Lint
.venv/bin/ruff check src/ tests/

# Type check
.venv/bin/mypy src/
```

## License

Open source. See [LICENSE](LICENSE) for details.
