"""Module 01: the smallest useful MCP server - one tool, tested over real stdio.

Two roles live in this one file:

  python main.py --serve
      Runs the MCP server. An MCP client (Claude Code, or the demo below)
      starts this as a subprocess and talks JSON-RPC to it over stdin/stdout.

  python main.py --port P --elf E --log L
      Runs the demo: starts *itself* with --serve as a subprocess, connects
      as an MCP client, lists the tools, and calls ping_stub on port P - the
      exact path a real agent takes. --elf and --log are accepted (every
      module in this course takes the same three flags) but unused here.
"""

from __future__ import annotations

import argparse      # command-line flags (--serve, --port, ...)
import asyncio       # the MCP client API is async
import socket        # ping_stub opens a plain TCP connection
import sys           # sys.executable: the Python running this file

# MCPServer is the high-level server class in the `mcp` SDK (2.x). It turns
# plain Python functions into MCP tools: the function name becomes the tool
# name, the type hints become the JSON schema for its arguments, and the
# docstring becomes the description the agent reads when deciding to call it.
from mcp.server.mcpserver import MCPServer

# The server object. "instructions" is sent to the client once, during the
# handshake - a short manual for the agent on how the tools fit together.
mcp = MCPServer(
    "hello-gdbstub",
    instructions="Check whether a QEMU gdbstub is listening before trying to debug.",
)


# @mcp.tool() registers the function. Nothing else is needed: no schema file,
# no routing table. The decorator inspects the signature at import time.
@mcp.tool()
def ping_stub(port: int = 1234, host: str = "127.0.0.1") -> str:
    """Check whether a gdbstub is listening on host:port.

    QEMU opens one when started with -s (port 1234) or -gdb tcp::PORT.
    Returns REACHABLE or UNREACHABLE plus what to do next.
    """
    # create_connection does DNS + connect in one call. A 3 second timeout
    # keeps the tool from hanging the agent if the port is filtered.
    try:
        with socket.create_connection((host, port), timeout=3):
            # Connecting and immediately closing is harmless: QEMU treats it
            # as a debugger that attached and detached again.
            pass
    except OSError as e:
        # The error message is written for the agent: it says what failed
        # AND what to do about it (Key Design Principle 2).
        return (f"UNREACHABLE: nothing accepted a connection on {host}:{port} ({e}). "
                f"Start QEMU with `-s -S` (or `-gdb tcp::{port} -S`) and try again.")
    return f"REACHABLE: a gdbstub is listening on {host}:{port}."


async def demo(port: int) -> None:
    """Act like an agent: spawn the server, handshake, list tools, call one."""
    # Imported here so the --serve path doesn't pay for client code.
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    # How to start the server: this same file, same Python, with --serve.
    # This is exactly what `claude mcp add` stores in its config.
    params = StdioServerParameters(command=sys.executable, args=[__file__, "--serve"])

    # stdio_client launches the subprocess and gives back two streams;
    # ClientSession speaks JSON-RPC over them.
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        # 1. initialize: client and server exchange names, versions and
        #    capabilities. Nothing else is allowed before this.
        init = await session.initialize()
        print(f"connected to server {init.server_info.name!r}")
        assert init.server_info.name == "hello-gdbstub"

        # 2. tools/list: what an agent sees before it decides anything.
        tools = await session.list_tools()
        for t in tools.tools:
            print(f"tool: {t.name}")
            print(f"  description: {t.description.splitlines()[0]}")
            print(f"  input schema: {t.input_schema['properties']}")
        assert [t.name for t in tools.tools] == ["ping_stub"]

        # 3. tools/call: the agent fills in arguments matching the schema.
        result = await session.call_tool("ping_stub", {"port": port})
        text = result.content[0].text
        print(f"ping_stub(port={port}) -> {text}")
        assert not result.is_error
        assert text.startswith("REACHABLE"), text

        # The same call against a port nothing listens on: the tool doesn't
        # raise, it returns guidance.
        bad = await session.call_tool("ping_stub", {"port": 1})
        print(f"ping_stub(port=1) -> {bad.content[0].text}")
        assert bad.content[0].text.startswith("UNREACHABLE")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, default=1234, help="QEMU gdbstub port")
    ap.add_argument("--elf", help="kernel ELF (unused in this module)")
    ap.add_argument("--log", help="QEMU interrupt log (unused in this module)")
    args = ap.parse_args()
    if args.serve:
        # Blocks forever, reading JSON-RPC from stdin. Never print() in this
        # mode: stdout IS the protocol channel, and stray text corrupts it.
        mcp.run()
    else:
        asyncio.run(demo(args.port))
        print("OK")


if __name__ == "__main__":
    main()
