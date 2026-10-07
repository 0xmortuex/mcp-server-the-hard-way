# Module 07: Designing Tools for an Agent

## The Concept

By now you can talk to a CPU. You can read its registers, stop it at a breakpoint, and name every address. None of that matters until it's exposed as **tools**, and tools are a user interface for a reader with unusual constraints:

- It can't see a screen. The tool result *is* its entire view of the machine.
- It can't ask "what did you mean?" mid-call. Every result has to stand on its own.
- It reads your docstrings as instructions, because they are the tool descriptions.
- It will make mistakes: wrong session names, typo'd symbols, reading registers while the CPU runs.

`main.py` is a five-tool MCP server over a tiny embedded RSP client, plus a client that drives it over real stdio the way Claude Code would. Every design decision below is asserted by that demo.

## Granularity: Why "continue" Is Three Tools

The obvious design for continue is one blocking call: resume, wait for a stop, return. It breaks the first time no breakpoint is ever hit. The CPU runs forever, the tool call never returns, and the agent is stuck.

```
one tool:     debug_continue ──────────────────────────────► (never returns)

three tools:  debug_continue(timeout_s=10) ──► "Stopped at breakpoint #1"
                                          └──► "Still running after 10s ..."
                                                  │
                         ┌────────────────────────┴───────────┐
                         ▼                                    ▼
               debug_continue (wait more)          debug_interrupt (halt now)
```

The rule is **never block forever, and say what to do next**:

```python
except TimeoutError:
    return (f"Still running after {timeout_s}s (no breakpoint hit). "
            "Call debug_continue to keep waiting or debug_interrupt to halt.")
```

"Still running" is a state, not an error. The CPU is fine; the agent just needs to pick its next move, and the result names both options.

## Sessions by Name

Every tool takes `name`. The agent picks a name at `debug_connect` and passes it back on each call:

```python
SESSIONS: dict[str, Session] = {}
```

This costs one parameter and buys a lot. The agent can debug two VMs at once ("the old kernel vs. the new one"). Every result is unambiguous. And a stale name gives a clean, explainable error instead of acting on the wrong machine.

## Results Are Written for a Reader With No Screen

Compare two answers to "where did it stop?":

```
T05thread:01;                          ← what the stub said
pc = 0x100020 <add>                    ← what the agent needs
```

`where()` turns every address into `address <symbol+offset>`. The full gdbstub-mcp adds `at kernel.c:10` and the next instruction (`next: push ebp`). Then the agent can reason about the stop without making a second call. A bare `0x100020` forces it to call a symbol tool, and that's one more round trip spent on bookkeeping instead of debugging.

## Errors Are Instructions

Here's the error for a mistake agents really make, a wrong session name:

```python
raise RSPError(f"no debug session named {name!r} (active: {known}) - "
               "call debug_connect first")
```

Three parts, every time: **what's wrong**, **what exists**, and **what to call**. The demo checks all of them. It asks for session `kernel` when only `k` exists and gets back `active: k ... call debug_connect first`.

The same pattern applies everywhere:

| Situation | Dead-end message | Instructive message |
|---|---|---|
| reading state while running | `invalid state` | `target is running - call debug_interrupt ...` |
| typo'd symbol | `KeyError: 'ad'` | `no symbol 'ad'; similar: add` |
| QEMU not started | `connection refused` | `... Start QEMU with -s -S` |

The table shows exactly what the agent sees, not what's in your log.

## The Trap: Your Error Message Never Arrives

This is the most important part of this module, and the full gdbstub-mcp got it wrong at first.

In the `mcp` SDK (v2), a tool that raises **`ToolError`** has its message sent to the agent. A tool that raises **anything else** (`ValueError`, `KeyError`, your own `RuntimeError` subclass) is treated as a *crash*. The agent receives only:

```
Error executing tool demo_crash
```

Your carefully written message goes to the server's stderr, where no agent will ever read it. The SDK does this on purpose, because crash text can leak internals. But it means **every error you intend the agent to read must be a `ToolError`**:

```python
from mcp.server.mcpserver.exceptions import ToolError

class RSPError(ToolError):
    pass
```

The demo proves both halves. `debug_break` with location `ad` raises an `RSPError` and the agent sees `similar: add`. A demo-only `demo_crash` tool raises `ValueError("this detailed message never reaches the agent")`, and the assertion checks that the text really doesn't arrive.

Note what this rule is *not*: it's not "catch everything and return a friendly string". Swallowing an exception hides real bugs. A genuine crash should still crash, and appear in your server log with its traceback. Make the errors you *planned for* `ToolError`s, and let the rest be crashes.

## Docstrings Are the Prompt

The MCP SDK turns each function's signature into a JSON schema and its docstring into the tool description. The agent reads that description before deciding what to call, so write it as an instruction:

```python
@mcp.tool()
def debug_continue(name: str, timeout_s: float = 10.0) -> str:
    """Resume and wait up to timeout_s for a stop. If none comes, the target
    KEEPS RUNNING: call debug_continue again to keep waiting, or
    debug_interrupt to halt it."""
```

The demo asserts that `KEEPS RUNNING` reaches the client in `list_tools()`. Parameter names matter for the same reason: `timeout_s` says its unit, and `timeout` would make the agent guess.

The server-level `instructions` field is the one place to describe the *workflow*, not a single tool:

```python
instructions=("Debug a kernel in QEMU started with -s -S. Loop: debug_connect -> "
              "debug_break <symbol> -> debug_continue -> debug_registers. ...")
```

## Helping Without Lying

Mort compiles `kmain` to the C symbol `mort_kmain`. An agent asked to "break in kmain" will type `kmain`. The lookup accepts a unique `*_kmain` match, and *says so*:

```python
return syms[hits[0]][0], f"resolved {name!r} to {hits[0]!r}"
```

That note shows up in the tool result, so the agent learns the real name. A silent redirect would be friendlier for one call and confusing for the next, when `mort_kmain` shows up in a backtrace it can't explain. If two symbols match, it's an error listing both. Don't guess.

## Run It

```bash
python main.py --port 1234 --elf ../kernel/kernel.elf
```

The output is a transcript of an agent session: connect, break on `add`, continue, registers, then four deliberate mistakes and the errors they produce.
