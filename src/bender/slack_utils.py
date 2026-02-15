"""Shared Slack utilities — message splitting and formatting."""

import logging
import re
import tempfile
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

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


def extract_urls(text: str) -> List[str]:
    """Extract URLs from text, including Slack-formatted URLs.

    Args:
        text: The text to extract URLs from.

    Returns:
        List of URLs found in the text.
    """
    # Match Slack-format URLs: <http://...|text> or <https://...|text>
    slack_urls = re.findall(r'<https?://[^|>]+', text)

    # Match regular URLs
    regular_urls = re.findall(r'https?://[^\s<>"\']+', text)

    urls = []
    for url in slack_urls + regular_urls:
        url = url.lstrip('<')
        if url not in urls:
            urls.append(url)

    return urls


async def fetch_url_content(url: str, timeout: int = 15) -> str | None:
    """Fetch content from a URL to provide context to Claude.

    Args:
        url: The URL to fetch.
        timeout: Timeout in seconds.

    Returns:
        Formatted content from the URL, or None if failed.
    """
    try:
        import aiohttp

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as response:
                # Accept 200, 201, 202, 301, 302 as success if they return content
                if response.status not in [200, 201, 202, 301, 302]:
                    logger.warning(f"Failed to fetch {url}: HTTP {response.status}")
                    return None

                # Check if we got content even with a redirect status
                content_type = response.headers.get('content-type', '')
                if 'text/html' not in content_type and response.status != 200:
                    # Some sites return 202 with HTML content
                    if 'text/html' not in (await response.text())[:1000]:
                        logger.warning(f"Non-HTML response from {url}: {content_type}")
                        return None

                content_type = response.headers.get('content-type', '')

                # Handle HTML pages
                if 'text/html' in content_type:
                    html = await response.text()

                    # Extract title
                    title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
                    title = title_match.group(1).strip() if title_match else "Sin título"

                    # Extract meta description
                    desc_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
                    description = desc_match.group(1).strip() if desc_match else ""

                    # Extract meta keywords for design context
                    keywords_match = re.search(r'<meta[^>]+name=["\']keywords["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
                    keywords = keywords_match.group(1).strip() if keywords_match else ""

                    # Extract colors from inline styles, CSS in <style> tags, and JS
                    colors = []
                    all_colors = set()

                    # First, extract from <style> tags
                    style_tags = re.findall(r'<style[^>]*>(.*?)</style>', html, re.IGNORECASE | re.DOTALL)
                    style_content = ' '.join(style_tags)

                    # Find hex colors in styles (#ffffff or #fff)
                    hex_colors = re.findall(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b', style_content, re.IGNORECASE)
                    for color in hex_colors[:30]:
                        if len(color) == 3:
                            c = f"#{color[0]}{color[0]}{color[1]}{color[1]}{color[2]}{color[2]}"
                        else:
                            c = f"#{color}"
                        all_colors.add(c.upper())

                    # Find rgb/rgba colors in styles
                    rgb_colors = re.findall(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', style_content)
                    for r, g, b in rgb_colors[:20]:
                        c = f"#{int(r):02x}{int(g):02x}{int(b):02x}"
                        all_colors.add(c.upper())

                    # Also search in the entire HTML for hex colors
                    hex_colors = re.findall(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b', html, re.IGNORECASE)
                    for color in hex_colors[:30]:
                        if len(color) == 3:
                            c = f"#{color[0]}{color[0]}{color[1]}{color[1]}{color[2]}{color[2]}"
                        else:
                            c = f"#{color}"
                        all_colors.add(c.upper())

                    # Filter out very dark, very light, or gray colors
                    filtered_colors = []
                    for c in all_colors:
                        if len(c) == 7:
                            r = int(c[1:3], 16)
                            g = int(c[3:5], 16)
                            b = int(c[5:7], 16)
                            # Skip grays
                            if abs(r - g) < 10 and abs(r - b) < 10:
                                continue
                            # Skip very dark
                            if r < 25 and g < 25 and b < 25:
                                continue
                            # Skip very light
                            if r > 230 and g > 230 and b > 230:
                                continue
                            filtered_colors.append(c)

                    colors = filtered_colors[:15]

                    # Extract font families
                    fonts = re.findall(r'font-family:\s*["\']?([^;"\'>]+)', html, re.IGNORECASE)
                    fonts = list(set([f.strip().split(',')[0].strip('"\'') for f in fonts[:10]]))

                    # Extract tailwind classes (popular CSS framework)
                    tailwind_classes = re.findall(r'\b(tailwind|tw-)[a-z-]+\b', html, re.IGNORECASE)
                    tailwind_classes = list(set(tailwind_classes[:20]))

                    # Extract Bootstrap classes
                    bootstrap_classes = re.findall(r'\b(bg-|text-|btn-|card-|modal-|navbar-|container-|row|col-)[a-z0-9-]+', html, re.IGNORECASE)
                    bootstrap_classes = list(set(bootstrap_classes[:20]))

                    # Extract main text content (simplified)
                    # Remove script and style tags
                    html_clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.IGNORECASE | re.DOTALL)
                    html_clean = re.sub(r'<style[^>]*>.*?</style>', '', html_clean, flags=re.IGNORECASE | re.DOTALL)

                    # Get text from body
                    body_match = re.search(r'<body[^>]*>(.*?)</body>', html_clean, re.IGNORECASE | re.DOTALL)
                    body_content = body_match.group(1) if body_match else html_clean

                    # Remove HTML tags and get plain text
                    text = re.sub(r'<[^>]+>', ' ', body_content)
                    text = re.sub(r'\s+', ' ', text).strip()

                    # Limit text length
                    if len(text) > 2000:
                        text = text[:2000] + "..."

                    # Build result
                    result = f"""[URL: {url}]
Título: {title}
{('Descripción: ' + description) if description else ''}
{('Palabras clave: ' + keywords) if keywords else ''}

"""

                    # Add design tokens if found
                    if colors:
                        result += f"COLORES ENCONTRADOS ({len(colors)}):\n"
                        for i, color in enumerate(list(set(colors))[:12]):
                            result += f"  - {color}\n"
                        result += "\n"

                    if fonts:
                        result += f"FUENTES ENCONTRADAS:\n"
                        for font in fonts[:8]:
                            result += f"  - {font}\n"
                        result += "\n"

                    if tailwind_classes:
                        result += f"TAILWIND CSS detectado ({len(tailwind_classes)} clases):\n"
                        result += f"  {', '.join(tailwind_classes[:10])}\n\n"

                    if bootstrap_classes:
                        result += f"BOOTSTRAP detectado ({len(bootstrap_classes)} clases):\n"
                        result += f"  {', '.join(bootstrap_classes[:10])}\n\n"

                    result += f"Contenido:\n{text}"
                    return result

                # Handle JSON APIs (like Dribbble)
                elif 'application/json' in content_type:
                    data = await response.json()

                    # Try to extract useful info
                    result = f"""[URL: {url}]

"""
                    # Try common fields
                    if isinstance(data, dict):
                        for key in ['title', 'name', 'description', 'html_url', 'url']:
                            if key in data:
                                result += f"{key.title()}: {data[key]}\n"
                        # Dump other fields briefly
                        other = {k: v for k, v in data.items() if k not in ['title', 'name', 'description', 'html_url', 'url']}
                        if other:
                            result += f"\nOtros datos: {str(other)[:500]}"
                    else:
                        result += str(data)[:1000]

                    return result

                else:
                    # Plain text or other
                    text = await response.text()
                    return f"[URL: {url}]\n\n{text[:2000]}"

    except ImportError:
        logger.warning("aiohttp not installed, skipping URL fetch")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return None


def is_figma_url(url: str) -> bool:
    """Check if URL is a Figma URL."""
    return 'figma.com' in url.lower()


async def fetch_figma_design(url: str, api_key: str | None = None, timeout: int = 30) -> str | None:
    """Fetch design tokens from Figma using their API.

    Args:
        url: The Figma URL.
        api_key: Figma API token (optional, can use FIGMA_API_KEY env var).
        timeout: Timeout in seconds.

    Returns:
        Formatted design tokens, or None if failed.
    """
    import os
    import re
    import json

    # Get API key from parameter or environment
    if not api_key:
        api_key = os.environ.get('FIGMA_API_KEY')

    if not api_key:
        return None

    # Extract file key from Figma URL
    # Formats: https://www.figma.com/file/FILE_KEY/... or https://www.figma.com/design/FILE_KEY/...
    match = re.search(r'figma\.com/(?:file|design)/([a-zA-Z0-9]+)', url)
    if not match:
        return None

    file_key = match.group(1)

    try:
        import aiohttp

        headers = {'X-Figma-Token': api_key}

        async with aiohttp.ClientSession() as session:
            # Get file info
            async with session.get(
                f'https://api.figma.com/v1/files/{file_key}',
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status != 200:
                    logger.warning(f"Figma API error: HTTP {response.status}")
                    return None

                data = await response.json()

            result = f"""[FIGMA DESIGN]

"""

            # Extract document name
            if 'name' in data:
                result += f"Nombre del archivo: {data['name']}\n\n"

            # Extract colors from styles
            colors = []
            styles = data.get('styles', {})

            # Try to get colors from the document
            document = data.get('document', {})

            # Extract colors from fills in the document tree
            def extract_colors_from_node(node):
                found = []
                if 'fills' in node:
                    for fill in node['fills']:
                        if fill.get('type') == 'SOLID' and 'color' in fill:
                            c = fill['color']
                            # Convert 0-1 RGB to hex
                            r = int(c.get('r', 0) * 255)
                            g = int(c.get('g', 0) * 255)
                            b = int(c.get('b', 0) * 255)
                            hex_color = f"#{r:02x}{g:02x}{b:02x}"
                            if hex_color not in found:
                                found.append(hex_color)

                # Recurse into children
                if 'children' in node:
                    for child in node['children']:
                        found.extend(extract_colors_from_node(child))

                return found

            colors = extract_colors_from_node(document)

            if colors:
                result += "COLORES:\n"
                for i, color in enumerate(colors[:20]):  # Limit to 20 colors
                    result += f"  {i+1}. {color}\n"
                result += "\n"

            # Extract typography if available
            result += "INFO: Para obtener todos los tokens de diseño (tipografía, espaciado, shadows), "
            result += "necesitas explorar el archivo en Figma directamente.\n\n"
            result += f"Enlace: {url}\n"

            return result

    except ImportError:
        logger.warning("aiohttp not installed, skipping Figma fetch")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch Figma design: {e}")
        return None


async def process_urls_in_text(text: str) -> str:
    """Extract and fetch content from URLs in text, appending as context.

    Args:
        text: The original text with URLs.

    Returns:
        Original text + URL contents as context.
    """
    urls = extract_urls(text)

    if not urls:
        return text

    context_parts = [text, "\n\n--- CONTEXTO DE ENLACES REFERENCIADOS ---\n"]

    for url in urls:
        content = None

        # Check if it's a Figma URL and try Figma API first
        if is_figma_url(url):
            content = await fetch_figma_design(url)
            if content:
                context_parts.append(f"\n{content}\n")
            else:
                context_parts.append(f"\n[URL de Figma detectada: {url}]\n")
                context_parts.append("[Para obtener tokens de diseño de Figma, configura FIGMA_API_KEY]\n")
        else:
            # Regular URL fetch
            content = await fetch_url_content(url)
            if content:
                context_parts.append(f"\n{content}\n")
            else:
                context_parts.append(f"\n[No se pudo obtener contenido de: {url}]\n")

    return "".join(context_parts)
