from mcp_client import list_mcp_tools, call_mcp_tool

tools = list_mcp_tools()

print("\nMCP TOOLS DISCOVERED:")
for tool in tools:
    print("-", tool["function"]["name"])
    print(" ", tool["function"]["description"])

print("\nTESTING MCP TOOL:")
result = call_mcp_tool("get_current_time", {})
print(result)
