"""MCP entry: python -m propgen.mcp"""

import asyncio
import logging
import sys

from propgen.mcp_server.server import main as mcp_main

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    asyncio.run(mcp_main())
