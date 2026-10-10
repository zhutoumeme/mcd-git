"""--demo：离线的假 MCP Server。接口和真实客户端一样（call_tool → MCP result），
返回同样带 structuredContent 信封和 Markdown 文本，所以解包、算价、下单闸走的都是同一套代码。"""
from .client import DemoClient

__all__ = ["DemoClient"]
