"""Module 07: a small but real MCP debugger server, plus a client that drives it.

Two modes in one file:

    python main.py --serve
        Run the MCP server on stdio. This is what Claude Code would launch.

    python main.py --port P --elf E --log L
        Demo/self-test. Spawns `main.py --serve` as a subprocess, connects to
        it over stdio exactly as an agent's client would, and drives the
        QEMU gdbstub on port P through the server's tools.

The RSP client below is deliberately minimal (Modules 02-04 build the full
one). This module is about what sits on top: the tool surface an agent sees.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import time

# ---------------------------------------------------------------------------
# Part 1: a minimal RSP client (Modules 02-04 build the full one)
# ---------------------------------------------------------------------------

# In mcp >= 2, only a ToolError's message reaches the agent. Any other
# exception is treated as a crash: the agent sees just "Error executing tool
# X" and your carefully written message stays in the server log. So every
# error we *mean* to show the agent derives from ToolError.
from mcp.server.mcpserver.exceptions import ToolError


class RSPError(ToolError):
    pass


class MiniRSP:
    def __init__(self, host: str, port: int, timeout: float = 10.0):
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            # An error the agent can act on: say what to start, and how.
            raise RSPError(
                f"could not connect to gdbstub on {host}:{port} ({e}). "
                "Start QEMU with `-s -S` (stub on 1234) or `-gdb tcp::PORT -S`."
            ) from None
        self.buf = b""
        self.running = False

    def _send(self, payload: bytes) -> None:
        # QEMU doesn't offer QStartNoAckMode, so every packet we send is
        # answered with '+' before anything else. Wait for it.
        self.sock.sendall(b"$" + payload + b"#%02x" % (sum(payload) % 256))
        self.sock.settimeout(10.0)
        while b"+" not in self.buf:
            self.buf += self.sock.recv(4096)
        self.buf = self.buf[self.buf.index(b"+") + 1:]

    def _recv(self, timeout: float) -> bytes:
        # One packet's payload, acked; raises TimeoutError if none arrives.
        deadline = time.monotonic() + timeout
        while True:
            start = self.buf.find(b"$")
            end = self.buf.find(b"#", start)
            if start >= 0 and end >= 0 and len(self.buf) >= end + 3:
                payload = self.buf[start + 1:end]
                self.buf = self.buf[end + 3:]
                self.sock.sendall(b"+")  # our ack for their packet
                return payload
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(65536)  # socket timeout -> TimeoutError
            if not chunk:
                raise RSPError("gdbstub closed the connection (did the VM exit?)")
            self.buf += chunk

    def request(self, payload: str) -> bytes:
        if self.running:
            # Not "invalid state": say exactly which tool fixes it.
            raise RSPError("target is running - call debug_interrupt (or wait for "
                           "debug_continue to report a stop) before reading state")
        self._send(payload.encode())
        reply = self._recv(10.0)
        if reply.startswith(b"E") and len(reply) == 3:
            raise RSPError(f"stub returned {reply.decode()} for {payload!r}")
        return reply


# ---------------------------------------------------------------------------
# Part 2: symbols - just enough to name addresses (Module 05 does it properly)
# ---------------------------------------------------------------------------


def load_symbols(path: str) -> dict[str, tuple[int, int]]:
    from elftools.elf.elffile import ELFFile

    out: dict[str, tuple[int, int]] = {}
    with open(path, "rb") as fh:
        for sym in ELFFile(fh).get_section_by_name(".symtab").iter_symbols():
            if sym["st_info"]["type"] in ("STT_FUNC", "STT_OBJECT") and sym.name:
                out[sym.name] = (sym["st_value"], sym["st_size"])
    return out


def where(syms: dict[str, tuple[int, int]], addr: int) -> str:
    """'0x100029 <add+0x9>' - the agent can't read hex, so never show bare hex."""
    for name, (base, size) in syms.items():
        if base <= addr < base + max(size, 1):
            off = addr - base
            return f"{addr:#x} <{name}{f'+{off:#x}' if off else ''}>"
    return f"{addr:#x}"


def lookup(syms: dict[str, tuple[int, int]], name: str) -> tuple[int, str | None]:
    """Exact name, else a unique *_name suffix (Mort emits mort_kmain for kmain).

    The second return value is a note shown to the agent, so it learns the
    real name instead of being silently redirected.
    """
    if name in syms:
        return syms[name][0], None
    hits = [n for n in syms if n.endswith("_" + name)]
    if len(hits) == 1:
        return syms[hits[0]][0], f"resolved {name!r} to {hits[0]!r}"
    near = sorted(n for n in syms if name.lower() in n.lower())[:8]
    raise RSPError(f"no symbol {name!r}" + (f"; similar: {', '.join(near)}" if near else ""))


# ---------------------------------------------------------------------------
# Part 3: the MCP server - the part this module is about
# ---------------------------------------------------------------------------


class Session:
    def __init__(self, port: int, elf: str):
        self.rsp = MiniRSP("127.0.0.1", port)
        self.syms = load_symbols(elf)
        self.bps: dict[int, int] = {}  # id -> address


SESSIONS: dict[str, Session] = {}


def get(name: str) -> Session:
    s = SESSIONS.get(name)
    if s is None:
        known = ", ".join(SESSIONS) or "none"
        # The fix is in the message: which sessions exist, and what to call.
        raise RSPError(f"no debug session named {name!r} (active: {known}) - "
                       "call debug_connect first")
    return s


def eip(s: Session) -> int:
    # i386 'g' packet: eax ecx edx ebx esp ebp esi edi eip ... (little endian)
    g = bytes.fromhex(s.rsp.request("g").decode())
    return int.from_bytes(g[32:36], "little")


def describe_stop(s: Session, reply: bytes) -> str:
    if reply[:1] in (b"W", b"X"):
        return f"Target exited ({reply.decode()}). The VM is gone."
    pc = eip(s)
    hit = next((i for i, a in s.bps.items() if a == pc), None)
    head = f"Stopped at breakpoint #{hit}" if hit else f"Stopped ({reply[:3].decode()})"
    return f"{head}\npc = {where(s.syms, pc)}"


def build_server():  # type: ignore[no-untyped-def]
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer(
        "mini-gdbstub",
        # Read by the agent before any tool call: the workflow, in one breath.
        instructions=(
            "Debug a kernel in QEMU started with -s -S. Loop: debug_connect -> "
            "debug_break <symbol> -> debug_continue -> debug_registers. If "
            "debug_continue says 'Still running', call it again or debug_interrupt."
        ),
    )

    @mcp.tool()
    def debug_connect(name: str, elf: str, port: int = 1234) -> str:
        """Connect to a QEMU gdbstub and register the session under `name`.

        elf is the kernel image with symbols. The target stays halted.
        """
        if name in SESSIONS:
            raise RSPError(f"session {name!r} exists - pick another name")
        SESSIONS[name] = s = Session(port, elf)
        return f"Connected {name!r} ({len(s.syms)} symbols).\n" + describe_stop(
            s, s.rsp.request("?"))

    @mcp.tool()
    def debug_break(name: str, location: str) -> str:
        """Set a hardware breakpoint at a symbol name or 0x address."""
        s = get(name)
        try:
            addr, note = int(location, 0), None
        except ValueError:
            addr, note = lookup(s.syms, location)
        if s.rsp.request(f"Z1,{addr:x},1") != b"OK":
            raise RSPError(f"stub refused a breakpoint at {addr:#x}")
        bp = len(s.bps) + 1
        s.bps[bp] = addr
        return f"#{bp} at {where(s.syms, addr)}" + (f"\n({note})" if note else "")

    @mcp.tool()
    def debug_continue(name: str, timeout_s: float = 10.0) -> str:
        """Resume and wait up to timeout_s for a stop. If none comes, the target
        KEEPS RUNNING: call debug_continue again to keep waiting, or
        debug_interrupt to halt it."""
        s = get(name)
        if not s.rsp.running:
            s.rsp._send(b"c")
            s.rsp.running = True
        try:
            reply = s.rsp._recv(timeout_s)
        except TimeoutError:
            return (f"Still running after {timeout_s}s (no breakpoint hit). "
                    "Call debug_continue to keep waiting or debug_interrupt to halt.")
        s.rsp.running = False
        return describe_stop(s, reply)

    @mcp.tool()
    def debug_interrupt(name: str) -> str:
        """Halt a running target now (like Ctrl-C in gdb)."""
        s = get(name)
        if not s.rsp.running:
            raise RSPError("target is already halted - nothing to interrupt")
        s.rsp.sock.sendall(b"\x03")
        reply = s.rsp._recv(5.0)
        s.rsp.running = False
        return describe_stop(s, reply)

    @mcp.tool()
    def debug_registers(name: str) -> str:
        """Read the general registers. Pointers into code are symbolized."""
        s = get(name)
        g = bytes.fromhex(s.rsp.request("g").decode())
        names = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi", "eip", "eflags"]
        lines = []
        for i, n in enumerate(names):
            v = int.from_bytes(g[i * 4:i * 4 + 4], "little")
            lines.append(f"{n:>7} = {where(s.syms, v) if n == 'eip' else f'{v:#010x}'}")
        return "\n".join(lines)

    if os.environ.get("MODULE07_DEMO_CRASH"):
        @mcp.tool()
        def demo_crash() -> str:
            """Demo only: fails with a plain exception, to show what the agent sees."""
            raise ValueError("this detailed message never reaches the agent")

    return mcp


# ---------------------------------------------------------------------------
# Part 4: the demo - an MCP client, playing the agent
# ---------------------------------------------------------------------------


async def demo(port: int, elf: str) -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable,
                                   args=[os.path.abspath(__file__), "--serve"],
                                   env={**os.environ, "MODULE07_DEMO_CRASH": "1"})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        init = await session.initialize()
        print(f"server: {init.server_info.name}")
        tools = {t.name: t for t in (await session.list_tools()).tools}
        print("tools:", ", ".join(sorted(t for t in tools if t != "demo_crash")))
        # The docstring IS the description the agent reads.
        assert "KEEPS RUNNING" in (tools["debug_continue"].description or "")

        async def call(tool: str, **args: object) -> tuple[bool, str]:
            res = await session.call_tool(tool, args)
            text = "\n".join(c.text for c in res.content if hasattr(c, "text"))
            print(f"\n> {tool} {args}\n{text}")
            return bool(res.is_error), text

        err, out = await call("debug_connect", name="k", elf=elf, port=port)
        assert not err and "Connected 'k'" in out

        err, out = await call("debug_break", name="k", location="add")
        assert not err and "<add>" in out

        err, out = await call("debug_continue", name="k", timeout_s=20)
        assert not err and "breakpoint #1" in out and "<add>" in out, out

        err, out = await call("debug_registers", name="k")
        assert not err and "eip = 0x" in out and "<add>" in out

        # A mistake an agent really makes: the wrong session name. The error
        # comes back as a tool result with is_error set - not a crash - and
        # the text tells the agent exactly what to do.
        err, out = await call("debug_registers", name="kernel")
        assert err, "a bad session name must be reported as an error"
        assert "call debug_connect first" in out and "active: k" in out

        # Interrupting a halted target is also an instructive error.
        err, out = await call("debug_interrupt", name="k")
        assert err and "already halted" in out

        # A typo'd symbol: the error carries suggestions.
        err, out = await call("debug_break", name="k", location="ad")
        assert err and "similar: add" in out

        # And the trap: a plain exception's message is withheld from the agent.
        err, out = await call("demo_crash")
        assert err and "never reaches" not in out, out
        print("    ^ the ValueError's text stayed on the server - use ToolError")

    print("\nmodule 07: all checks passed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="run as an MCP stdio server")
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--elf", default=os.path.join("..", "kernel", "kernel.elf"))
    ap.add_argument("--log", default=None, help="unused here; accepted for verify.py")
    args = ap.parse_args()
    if args.serve:
        build_server().run()
    else:
        asyncio.run(demo(args.port, os.path.abspath(args.elf)))


if __name__ == "__main__":
    main()
