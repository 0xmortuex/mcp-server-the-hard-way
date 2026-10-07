# MCP Server — The Hard Way

Build an MCP server that lets an AI agent debug an operating-system kernel. Start from raw
TCP bytes and end with the agent explaining a triple fault. No gdb, no frameworks hiding
the protocol. Every line explained.

At the end you'll have built [gdbstub-mcp](https://github.com/0xmortuex/gdbstub-mcp) yourself,
and you'll understand the two things most MCP tutorials skip: the **system on the other
end of the wire**, and **how to design tools that an agent can actually use**.

## Why This Exists

Most MCP tutorials stop at a weather API: a tool that calls a REST endpoint and returns
JSON. That teaches you the SDK and nothing else. The hard part of a useful MCP server is
everywhere else:

- speaking a real protocol correctly
- representing a system that changes state while the agent isn't looking
- turning thousands of lines of raw output into the three lines an agent needs
- failing in ways that tell the agent what to do next

A kernel debugger exercises all of that. A CPU halts, resumes, faults and resets. Its
state lives in registers you have to discover and memory you have to decode. When it
crashes, it gives you nothing but a reboot.

## Who This Is For

- Developers who've built a "hello world" MCP server and want to build a real one
- Hobby OS developers who want an AI agent in their debug loop
- Anyone curious how gdb actually talks to a target

**Prerequisites:** Python and basic sockets. Some assembly helps but isn't required, and
every x86 detail is explained where it appears. You need [QEMU](https://www.qemu.org/download/)
and `pip install -r requirements.txt`.

## Modules

| # | Module | What You'll Build |
|---|--------|-------------------|
| 01 | [What an Agent Needs](01-what-an-agent-needs/) | The osdev debug loop, what MCP is, and a first one-tool server tested over real stdio |
| 02 | [Speaking RSP](02-speaking-rsp/) | Packet framing, checksums, acks, run-length decoding, and your first conversation with QEMU |
| 03 | [Registers From XML](03-registers-from-xml/) | Discovering the CPU's register layout from `target.xml`, so you never hard-code an architecture |
| 04 | [Memory, Breakpoints and Time](04-memory-breakpoints-and-time/) | Reading and writing memory, breakpoints and watchpoints, and why "continue" is the hardest command |
| 05 | [Symbols and Source Lines](05-symbols-and-source-lines/) | ELF symbol tables, DWARF line programs, and turning `kernel.c:10` into an address |
| 06 | [Backtraces](06-backtraces/) | Frame-pointer walking, the prologue bug every debugger has to handle, and stack scanning |
| 07 | [Designing Tools for an Agent](07-designing-tools-for-an-agent/) | Tool granularity, errors as instructions, timeouts and long-running state, and wiring the MCP server |
| 08 | [The Triple Fault](08-the-triple-fault/) | Crashing the kernel on purpose, then parsing QEMU's interrupt log into a root cause |
| 09 | [Testing It For Real](09-testing-it-for-real/) | A fake gdbstub on a real socket, a fixture kernel, and CI that boots QEMU |

Each module contains:
- **`README.md`**: the concepts and design decisions
- **`main.py`**: a working, commented implementation of that stage, runnable against QEMU
- **`break-it.md`**: challenges that break your own code, with hints

## How To Use This Course

**Option A: Build along.** Read the modules in order and run each `main.py`, then do the
break-it challenges.

**Option B: Reference.** Jump to the module you need. Each `main.py` stands alone, with no
imports between modules.

**Option C: Use the finished tool.** `pip install gdbstub-mcp` is what Module 07 assembles.

## Run It

Every `main.py` runs against the test kernel in [`kernel/`](kernel/), a ~40-line C kernel
with a function to break on, a loop to watch, and a `triple_fault()` you can trigger from
the debugger:

```bash
# terminal 1: boot the kernel, halted, with its gdbstub on port 1234
qemu-system-i386 -kernel kernel/kernel.elf -s -S -d int,cpu_reset -D int.log -no-reboot -display none

# terminal 2
python 02-speaking-rsp/main.py --port 1234 --elf kernel/kernel.elf --log int.log
```

## Every Module Is Verified

```bash
python verify.py
```

This boots a fresh QEMU for each module, runs its `main.py`, and fails if any assertion
fails. CI runs it on every push and weekly, so the code in this course can't silently
stop working.

## Architecture Overview

```
 AI agent (Claude Code, ...)
        │  MCP over stdio: JSON-RPC tool calls          ← Module 01, 07
        ▼
 ┌──────────────────────────────────────────────┐
 │ MCP server: tools, sessions, error design    │      ← Module 07
 │   ├── symbols:   ELF + DWARF                 │      ← Module 05, 06
 │   ├── layout:    target.xml registers        │      ← Module 03
 │   └── RSP client: $packet#cs over TCP        │      ← Module 02, 04
 └──────────────────────────────────────────────┘
        │  GDB Remote Serial Protocol
        ▼
 QEMU gdbstub (-s)  ──►  your kernel's CPU
 QEMU -d int log    ──►  triple-fault explainer          ← Module 08
```

## Key Design Principles

**1. The agent can't see anything you don't show it.** Every tool result is the agent's
entire view of the machine. `0x100029` means nothing to it; `add+0x9 at kernel.c:10`
means everything.

**2. Errors are instructions.** "connection refused" is a dead end. "Could not connect to
gdbstub on 127.0.0.1:1234 — start QEMU with -s -S" is the next step.

**3. Never block forever.** A running CPU may never stop. Every wait has a timeout and a
result that says what to do next.

**4. Discover, don't hard-code.** The stub tells you its registers. The ELF tells you its
symbols. A tool that works on one architecture by accident will fail on the next one.

## Tech Stack

- **Language:** Python 3.10+
- **Protocol:** GDB Remote Serial Protocol (RSP), MCP over stdio
- **Dependencies:** `mcp`, `pyelftools`, `capstone`, nothing else. No gdb binary.

## License

MIT. Use it, modify it, ship it.

---

Built by [0xmortuex](https://github.com/0xmortuex)
