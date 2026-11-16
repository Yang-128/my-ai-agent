import sys
import faulthandler

faulthandler.enable()

if "--mcp-server" in sys.argv:
    from mcp_server import mcp
    mcp.run(transport="stdio")
else:
    import app
    import uvicorn

    uvicorn.run(
        app.app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )
