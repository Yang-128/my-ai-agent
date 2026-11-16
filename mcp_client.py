import sys
from runtime import RESOURCE_DIR
import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = RESOURCE_DIR / "mcp_server.py"

async def _list_tools():
    params = StdioServerParameters(
        command=sys.executable,
        args=["--mcp-server"] if getattr(sys, "frozen", False) else [str(SERVER)]
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [
                {
                    "type": "function",
                    "function": {
                        "name": f"mcp_{tool.name}",
                        "description": tool.description or "",
                        "parameters": tool.inputSchema
                    }
                }
                for tool in result.tools
            ]

async def _call_tool(name, arguments):
    params = StdioServerParameters(
        command=sys.executable,
        args=["--mcp-server"] if getattr(sys, "frozen", False) else [str(SERVER)]
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)

            if result.isError:
                raise RuntimeError("MCP tool execution failed")

            return "\n".join(
                item.text for item in result.content
                if hasattr(item, "text")
            )

def list_mcp_tools():
    return anyio.run(_list_tools)

def call_mcp_tool(name, arguments):
    return anyio.run(_call_tool, name, arguments)
