import time
import threading
from mcp_client import list_mcp_tools, call_mcp_tool

MCP_CACHE_TTL = 60
_mcp_tools_cache = None
_mcp_tools_cache_time = 0
_mcp_refresh_lock = threading.Lock()


TOOL_DEFINITIONS = []

def get_all_tool_definitions():
    global _mcp_tools_cache, _mcp_tools_cache_time

    with _mcp_refresh_lock:
        if _mcp_tools_cache is None or time.monotonic() - _mcp_tools_cache_time > MCP_CACHE_TTL:
            try:
                _mcp_tools_cache = list_mcp_tools()
                _mcp_tools_cache_time = time.monotonic()
            except Exception as error:
                print(f"MCP discovery warning: {error}")
        cached = list(_mcp_tools_cache or [])

    return list(TOOL_DEFINITIONS) + cached


def execute_tool(name, arguments):
    if name.startswith("mcp_"):
        return call_mcp_tool(name[4:], arguments)

    raise ValueError(f"Unknown tool: {name}")
