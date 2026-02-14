# Using Ollama with Bender

This guide explains how to configure Bender to use local Ollama models instead of Claude API.

## ⚠️ Important Limitations

**Ollama models have significant limitations compared to Claude:**
- ❌ No native tool use support (Bash, Edit, Write commands)
- ❌ Lower code generation quality
- ❌ Limited context window
- ❌ No session persistence features
- ⚠️ Requires manual proxy setup

**Recommended use cases for Ollama:**
- ✅ Testing and development
- ✅ Privacy-sensitive environments
- ✅ Cost optimization for simple queries
- ✅ Offline operation

---

## Setup Instructions

### Step 1: Install and Run Ollama

```bash
# Install Ollama (https://ollama.ai)
# Windows: Download installer from https://ollama.ai/download
# Linux: curl -fsSL https://ollama.ai/install.sh | sh
# macOS: Download from https://ollama.ai/download

# Pull a coding model
ollama pull qwen2.5-coder:7b

# Start Ollama server (runs on port 11434 by default)
ollama serve
```

### Step 2: Install and Configure LiteLLM Proxy

LiteLLM provides an Anthropic-compatible API that forwards requests to Ollama:

```bash
# Install LiteLLM
pip install litellm[proxy]

# Create configuration file
cat > litellm_config.yaml <<EOF
model_list:
  - model_name: claude-sonnet-4.5  # Name that Bender will use
    litellm_params:
      model: ollama/qwen2.5-coder:7b
      api_base: http://localhost:11434

  - model_name: claude-haiku-4.5  # Faster model for simple tasks
    litellm_params:
      model: ollama/qwen2.5-coder:1.5b
      api_base: http://localhost:11434
EOF

# Start LiteLLM proxy (port 8000)
litellm --config litellm_config.yaml --port 8000
```

### Step 3: Configure Bender

#### Option A: Interactive Prompt (Recommended)

When you start Bender, it will ask you to choose:

```bash
python -m bender

# Output:
# ============================================================
# 🤖 Bender - Claude Code Agent
# ============================================================
#
# Select API mode:
#   1) Claude API (Anthropic Cloud)
#   2) Ollama API (Local Models)
#
# Enter your choice (1 or 2) [default: 1]:
```

Select option 2 and enter your model name (e.g., `qwen2.5-coder:7b`).

#### Option B: Environment Variables

Set these variables in your `.env` file or environment:

```bash
# API Mode
BENDER_API_MODE=ollama

# Model to use
ANTHROPIC_MODEL=qwen2.5-coder:7b

# Point to LiteLLM proxy
ANTHROPIC_BASE_URL=http://localhost:8000

# Fake API key (LiteLLM doesn't validate it)
ANTHROPIC_API_KEY=fake-key-not-used

# Slack tokens (required)
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token
```

#### Option C: Windows Command Prompt

```cmd
set BENDER_API_MODE=ollama
set ANTHROPIC_MODEL=qwen2.5-coder:7b
set ANTHROPIC_BASE_URL=http://localhost:8000
set ANTHROPIC_API_KEY=fake-key

python -m bender
```

#### Option D: PowerShell

```powershell
$env:BENDER_API_MODE="ollama"
$env:ANTHROPIC_MODEL="qwen2.5-coder:7b"
$env:ANTHROPIC_BASE_URL="http://localhost:8000"
$env:ANTHROPIC_API_KEY="fake-key"

python -m bender
```

---

## Architecture

```
┌──────────┐    Slack      ┌──────────┐
│  Slack   │◄──────────────┤  Bender  │
└──────────┘               └─────┬────┘
                                 │
                                 │ Claude Code CLI
                                 │ --model qwen2.5-coder:7b
                                 ▼
                           ┌─────────────┐
                           │  LiteLLM    │
                           │   Proxy     │
                           └──────┬──────┘
                                  │
                                  │ Ollama API
                                  ▼
                           ┌──────────────┐
                           │   Ollama     │
                           │ (localhost)  │
                           └──────────────┘
```

---

## Recommended Models

| Model | Size | Use Case | Quality |
|-------|------|----------|---------|
| qwen2.5-coder:32b | 19GB | Complex code tasks | ⭐⭐⭐⭐ |
| qwen2.5-coder:14b | 8.9GB | General coding | ⭐⭐⭐ |
| qwen2.5-coder:7b | 4.7GB | Simple tasks | ⭐⭐ |
| qwen2.5-coder:1.5b | 934MB | Very simple tasks | ⭐ |
| deepseek-coder-v2:16b | 9.0GB | Code generation | ⭐⭐⭐ |
| codellama:13b | 7.4GB | Legacy support | ⭐⭐ |

**Hardware Requirements:**
- **RAM:** Model size × 1.5 (e.g., 14b model needs ~14GB RAM)
- **GPU:** Optional but highly recommended (NVIDIA with CUDA)
- **Storage:** Model size + 2GB temporary space

---

## Verification

### Test Ollama

```bash
# Test Ollama directly
ollama run qwen2.5-coder:7b "Write a Python function to reverse a string"
```

### Test LiteLLM Proxy

```bash
# Test LiteLLM is forwarding to Ollama
curl http://localhost:8000/v1/models
```

### Test Bender

Send a message to Bender in Slack:
```
@Bender Write a simple Python hello world function
```

Check Bender logs for:
```
INFO Invoking Claude Code (model=qwen2.5-coder:7b, ...)
```

---

## Troubleshooting

### Issue: "Claude Code cannot find model"

**Solution:** Verify LiteLLM is running and accessible:
```bash
curl http://localhost:8000/health
```

### Issue: "Connection refused to localhost:11434"

**Solution:** Ensure Ollama is running:
```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags

# If not, start it
ollama serve
```

### Issue: "Model not found"

**Solution:** Pull the model first:
```bash
ollama pull qwen2.5-coder:7b
ollama list  # Verify it's installed
```

### Issue: "Very slow responses"

**Solutions:**
- Use a smaller model (7b instead of 32b)
- Enable GPU acceleration (requires NVIDIA GPU + CUDA)
- Increase context window limit in LiteLLM config
- Use `--low-memory` flag with Ollama

### Issue: "Poor code quality"

**Expected behavior:** Ollama models are not as capable as Claude. Consider:
- Use larger models (14b or 32b)
- Use Claude API for complex tasks
- Implement hybrid mode (Ollama for simple, Claude for complex)

---

## Advanced: Hybrid Mode

Use Ollama for simple queries and Claude API for complex tasks:

```python
# In bender/claude_code.py (custom modification)
def should_use_ollama(prompt: str) -> bool:
    """Determine if Ollama is sufficient for this prompt."""
    simple_keywords = ["hello", "what is", "explain", "list"]
    return any(keyword in prompt.lower() for keyword in simple_keywords)

# Then in handlers, choose model dynamically
model = None if should_use_ollama(prompt) else "claude-sonnet-4.5"
```

---

## Security Considerations

✅ **Advantages of Ollama:**
- 100% local processing (no data leaves your machine)
- No API costs
- No rate limits
- Full data sovereignty

⚠️ **Considerations:**
- LiteLLM proxy has no authentication by default
- Ollama API is unauthenticated (don't expose to internet)
- Run LiteLLM and Ollama on localhost only
- Use firewall rules to restrict access

---

## Performance Comparison

| Metric | Claude API | Ollama (14b) | Ollama (7b) |
|--------|-----------|--------------|-------------|
| Speed (simple) | ~2s | ~5s | ~3s |
| Speed (complex) | ~10s | ~30s | ~20s |
| Quality | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| Context | 200K tokens | 32K tokens | 8K tokens |
| Tool use | ✅ Native | ❌ Emulated | ❌ Emulated |
| Cost | $$ per token | Free | Free |

---

## Next Steps

1. ✅ Follow setup instructions
2. ✅ Test with simple queries
3. ⚠️ Compare quality with Claude API
4. 🔧 Adjust model based on performance
5. 📊 Monitor resource usage (RAM, CPU, GPU)

For production use, consider hybrid mode with Claude API for complex tasks.
