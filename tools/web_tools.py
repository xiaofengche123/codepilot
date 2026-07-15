"""
码搭 CodePilot · Web 工具
web_search (DuckDuckGo) / web_fetch (httpx + BeautifulSoup)
"""

import httpx
from bs4 import BeautifulSoup


def web_search(query: str, max_results: int = 5) -> str:
    """
    在互联网上搜索。参数 query: 搜索关键词、max_results: 最大结果数（默认 5）。返回标题+URL+摘要。
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return "[错误] duckduckgo_search 库未安装，请运行: pip install duckduckgo_search"

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max(max_results, 1)))
    except Exception as e:
        return f"[错误] 搜索失败: {e}"

    if not results:
        return f"[未找到] 没有关于 '{query}' 的搜索结果"

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        href = r.get("href", "")
        body = r.get("body", "")
        lines.append(f"{i}. {title}")
        if href:
            lines.append(f"   {href}")
        if body:
            lines.append(f"   {body[:200]}")
    return "\n".join(lines)


def web_fetch(url: str, max_length: int = 4000) -> str:
    """
    抓取网页内容并提取纯文本。参数 url: 网页地址、max_length: 返回文本最大长度（默认 4000 字符）。返回纯文本内容。
    """
    if not url.startswith(("http://", "https://")):
        return f"[错误] URL 格式无效: {url}"

    try:
        response = httpx.get(url, timeout=15, follow_redirects=True)
    except httpx.TimeoutException:
        return f"[超时] 抓取 {url} 超时（15 秒）"
    except httpx.HTTPStatusError as e:
        return f"[错误] HTTP {e.response.status_code}: {url}"
    except Exception as e:
        return f"[错误] 抓取失败: {e}"

    if response.status_code != 200:
        return f"[错误] HTTP {response.status_code}: {url}"

    try:
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        return f"[错误] 解析 HTML 失败: {e}"

    # 移除不必要的元素
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    if len(text) > max_length:
        text = text[:max_length] + f"\n...[已截断，原文 {len(text)} 字符]"

    if not text.strip():
        return "[空] 抓取的页面没有可提取的文本内容"
    return text


WEB_TOOLS = {
    "web_search": web_search,
    "web_fetch": web_fetch,
}

WEB_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "在互联网上搜索最新信息（通过 DuckDuckGo）",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "最大结果数，默认 5"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取网页 URL 内容，提取纯文本（移除样式和脚本）",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要抓取的网页 URL（需包含 http/https）"},
                    "max_length": {"type": "integer", "description": "返回文本最大长度，默认 4000"},
                },
                "required": ["url"],
            },
        },
    },
]

WEB_DANGEROUS_TOOLS: set[str] = set()
