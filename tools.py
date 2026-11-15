import ast
import operator
import time
import threading
from mcp_client import list_mcp_tools, call_mcp_tool

MCP_CACHE_TTL = 60
_mcp_tools_cache = None
_mcp_tools_cache_time = 0
_mcp_tools_lock = threading.Lock()
_mcp_refresh_lock = threading.Lock()


def _refresh_mcp_tools():
    global _mcp_tools_cache, _mcp_tools_cache_time
    try:
        discovered = list_mcp_tools()
        with _mcp_tools_lock:
            _mcp_tools_cache = discovered
            _mcp_tools_cache_time = time.monotonic()
    except Exception as error:
        print(f"MCP discovery warning: {error}")
        with _mcp_tools_lock:
            if _mcp_tools_cache is None:
                _mcp_tools_cache = []
            _mcp_tools_cache_time = time.monotonic()
    finally:
        _mcp_refresh_lock.release()

OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

def calculate(expression):
    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in OPERATORS:
            return OPERATORS[type(node.op)](evaluate(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
            return OPERATORS[type(node.op)](
                evaluate(node.left), evaluate(node.right)
            )
        raise ValueError("Unsupported mathematical expression")

    if len(expression) > 200:
        raise ValueError("Expression too long")

    return evaluate(ast.parse(expression, mode="eval").body)

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Evaluate a mathematical expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "A mathematical expression."
                    }
                },
                "required": ["expression"]
            }
        }
    }
]

def get_all_tool_definitions():
    global _mcp_tools_cache, _mcp_tools_cache_time

    with _mcp_tools_lock:
        cached = list(_mcp_tools_cache or [])
        needs_refresh = (
            _mcp_tools_cache is None
            or time.monotonic() - _mcp_tools_cache_time > MCP_CACHE_TTL
        )

    if needs_refresh and _mcp_refresh_lock.acquire(blocking=False):
        try:
            discovered = list_mcp_tools()
            with _mcp_tools_lock:
                _mcp_tools_cache = discovered
                _mcp_tools_cache_time = time.monotonic()
                cached = list(discovered)
        except Exception as error:
            print(f"MCP discovery warning: {error}")
            _mcp_refresh_lock.release()
        else:
            _mcp_refresh_lock.release()

    return list(TOOL_DEFINITIONS) + cached


def execute_tool(name, arguments):
    if name == "calculate":
        return str(calculate(arguments["expression"]))

    if name.startswith("mcp_"):
        return call_mcp_tool(name[4:], arguments)

    raise ValueError(f"Unknown tool: {name}")
