# Module 01: What an Agent Needs

## The Concept

Kernel development has a tight loop:

```
 edit kmain.c ──► build kernel.elf ──► boot in QEMU ──► look at the screen
      ▲                                                        │
      └──────────── debug: why did it do that? ◄───────────────┘
```

An AI agent already handles the first two steps well. It edits source and runs the
build. Give it [qemu-mcp](https://github.com/0xmortuex/qemu-mcp) and it can do steps
three and four as well: boot the kernel headless, press keys, take screenshots and read
the serial console.

That still leaves the last arrow. When the screenshot shows a frozen screen, or the VM
reboots in a loop, the agent can *see* that something went wrong but has no way to find
out *why*. A human would open gdb, attach to QEMU, set a breakpoint and look at
registers. This course builds that ability for the agent, as an MCP server that speaks
gdb's protocol directly.

Before the protocol, there's the plumbing. This module builds the smallest MCP server
that does something real: one tool that checks whether a debugger can connect at all.

## What MCP Actually Is

The Model Context Protocol is JSON-RPC 2.0 over a pipe. For a local server, the pipe is
stdin/stdout. The client (Claude Code, for example) launches your server as a subprocess,
writes JSON requests to its stdin, and reads JSON responses from its stdout.

```
 Agent / client                                  Your server (subprocess)
 ──────────────                                  ────────────────────────
 initialize           ─────────────────────────►
                      ◄─────────────────────────  server name, capabilities
 tools/list           ─────────────────────────►
                      ◄─────────────────────────  [{name, description, inputSchema}]
 tools/call ping_stub {"port": 1234} ──────────►
                      ◄─────────────────────────  {content: [{type: text, text: ...}]}
```

There are three requests to understand:

- **`initialize`** is the handshake. Nothing else is allowed until it completes.
- **`tools/list`** is how the agent learns what it can do. Each tool is a name, a
  description and a JSON Schema for its arguments. The model reads all of it as prose
  when deciding what to call.
- **`tools/call`** runs a tool and returns content, usually text, for the model to read.

That's the whole contract. Everything else in this course is about what goes *inside*
the tools.

## A Tool Is a Function Plus Its Docstring

The `mcp` Python SDK builds the schema from your function signature:

```python
mcp = MCPServer("hello-gdbstub", instructions="Check whether a QEMU gdbstub ...")

@mcp.tool()
def ping_stub(port: int = 1234, host: str = "127.0.0.1") -> str:
    """Check whether a gdbstub is listening on host:port.
    ...
```

- `MCPServer(...)` creates the server. `instructions` is sent once at handshake, as a
  short manual on how the tools fit together.
- `@mcp.tool()` registers the function. The name becomes the tool name.
- The type hints become the input schema. The demo prints what the agent actually sees:
  `{'port': {'default': 1234, 'type': 'integer'}, 'host': {...}}`.
- The docstring becomes the description. **It's documentation for a model, not a
  human**, so it should say when to use the tool and what the result means.

The body is ordinary Python:

```python
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except OSError as e:
        return (f"UNREACHABLE: nothing accepted a connection on {host}:{port} ({e}). "
                f"Start QEMU with `-s -S` (or `-gdb tcp::{port} -S`) and try again.")
    return f"REACHABLE: a gdbstub is listening on {host}:{port}."
```

- `create_connection` with a 3-second timeout. A tool that hangs, hangs the agent.
- Connecting and closing straight away is harmless. QEMU treats it as a debugger that
  attached and detached again.
- The failure message has two halves: what happened, then **what to do next**. An agent
  reading "connection refused" has to guess. An agent reading "start QEMU with -s -S"
  just does it. Module 07 builds this into a design rule.

## Testing Over the Real Transport

It's tempting to test `ping_stub` by calling it as a Python function. That skips the
part that actually breaks: the server process, the handshake, and the schema the client
receives. So `main.py` tests the real path. It launches *itself* as a server
subprocess, then connects as a client:

```python
params = StdioServerParameters(command=sys.executable, args=[__file__, "--serve"])
async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
    init = await session.initialize()
    tools = await session.list_tools()
    result = await session.call_tool("ping_stub", {"port": port})
```

- `StdioServerParameters` is the same information `claude mcp add` stores: a command and
  its arguments.
- `stdio_client` spawns the process. `ClientSession` speaks JSON-RPC over its pipes.
- `initialize`, `list_tools` and `call_tool` are the three requests from the diagram
  above, in order.

Output against a halted QEMU:

```
connected to server 'hello-gdbstub'
tool: ping_stub
  description: Check whether a gdbstub is listening on host:port.
  input schema: {'port': {'default': 1234, 'title': 'Port', 'type': 'integer'}, ...}
ping_stub(port=12360) -> REACHABLE: a gdbstub is listening on 127.0.0.1:12360.
ping_stub(port=1) -> UNREACHABLE: nothing accepted a connection on 127.0.0.1:1 (...).
  Start QEMU with `-s -S` (or `-gdb tcp::1 -S`) and try again.
```

One rule in `--serve` mode: **never `print()`**. Stdout *is* the protocol channel. A stray
debug print corrupts a JSON-RPC message, and the client sees a garbled stream instead of
an error. Log to stderr.

## Run It

```bash
qemu-system-i386 -kernel kernel/kernel.elf -s -S -display none
python 01-what-an-agent-needs/main.py --port 1234
```

`-s` opens the gdbstub on TCP port 1234. `-S` freezes the CPU before its first
instruction, so nothing runs until a debugger says so. Every module from here on relies
on that frozen CPU.

## What's Missing

The server knows a stub is *there* but can't talk to it. Module 02 opens the connection
and speaks the GDB Remote Serial Protocol byte by byte.

→ [Next: Module 02 — Speaking RSP](../02-speaking-rsp/README.md)
