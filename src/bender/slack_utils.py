"""Shared Slack utilities — message splitting and formatting."""

import re
import tempfile
from pathlib import Path

# Slack message character limit
SLACK_MSG_LIMIT = 4000

# Threshold for uploading as file instead of posting
LONG_RESPONSE_THRESHOLD = 8000


def md_to_mrkdwn(text: str) -> str:
    """Convert standard Markdown to Slack mrkdwn format."""
    lines = text.split("\n")
    result: list[str] = []

    for line in lines:
        # Headers → bold (Slack has no heading syntax)
        line = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", line)

        # Horizontal rules → empty line
        if re.match(r"^---+\s*$", line):
            result.append("")
            continue

        # Bold: **text** → *text*
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)

        # Italic: *text* (but not already bold) → _text_
        line = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"_\1_", line)

        # Inline code: `code` → `code` (Slack supports this)
        # Code blocks: ```lang\ncode\n``` → ```code```
        line = re.sub(r"```(\w*)\n?([\s\S]*?)```", r"```\2```", line)

        # Strikethrough: ~~text~~ → ~~text~~
        line = re.sub(r"~~(.+?)~~", r"~~\1~~", line)

        # Markdown links: [text](url) → <url|text>
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", line)

        # Unordered lists: - item or * item → • item
        line = re.sub(r"^[\-\*]\s+", "• ", line)

        # Ordered lists: 1. item → 1. item (keep as is)
        line = re.sub(r"^(\d+)\.\s+", r"\1. ", line)

        # Blockquotes: > text → | text (Slack style)
        line = re.sub(r"^>\s+", "| ", line)

        result.append(line)

    return "\n".join(result)


def split_text(text: str, max_length: int = SLACK_MSG_LIMIT) -> list[str]:
    """Split text into chunks, preferring to break at newlines."""
    chunks: list[str] = []
    while len(text) > max_length:
        split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1:
            split_pos = max_length
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def create_temp_file(content: str, prefix: str = "response") -> Path:
    """Create a temporary file with the given content.

    Args:
        content: The text content to write to the file.
        prefix: Prefix for the temporary filename.

    Returns:
        Path to the created temporary file.
    """
    # Create a temporary directory
    temp_dir = tempfile.mkdtemp()
    file_path = Path(temp_dir) / f"{prefix}.txt"
    file_path.write_text(content, encoding="utf-8")
    return file_path
