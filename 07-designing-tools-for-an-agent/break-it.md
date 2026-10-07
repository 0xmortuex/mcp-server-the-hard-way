# Break It: Tool Design

## Challenge 1: The Tool That Never Returns

Rewrite `debug_continue` as a single blocking call: send `c`, then `_recv()` with no timeout. Remove the breakpoint on `add` and call it from the demo. How long does the demo wait? What does an agent in Claude Code see while it waits, and can it do anything else?

**Hint:** Nothing comes back, because the kernel loops forever and never stops. Most MCP clients have their own request timeout, and when it fires the agent gets a transport error. That error says nothing about the CPU still running behind it, and your session is now in an unknown state: is the target running or not? The timeout belongs in *your* tool, where you can describe the state you left the target in.

## Challenge 2: Make the Error Disappear

Change `class RSPError(ToolError)` back to `class RSPError(RuntimeError)` and run the demo. Which assertions fail, and what exactly does the client receive for the wrong-session call?

**Hint:** Every instructive error collapses to `Error executing tool debug_registers`. Then try to fix it the "easy" way, by wrapping every tool body in `try/except Exception: return str(e)`. That makes the messages arrive, but now look at what happens to a real bug, like a `TypeError` in `describe_stop`. It becomes a normal-looking result with no traceback anywhere. That's swallowing errors. Raise `ToolError` on purpose instead.

## Challenge 3: Bare Hex

Replace `where()` with `return f"{addr:#x}"`. Then put yourself in the agent's position and answer from the output alone: which function was the CPU in when `debug_continue` returned? Count how many extra tool calls you'd need to find out.

**Hint:** With the gdbstub-mcp tool set it's at least one more call (`debug_symbol`), and with this module's five tools it's impossible. Every result that contains an address should contain its name too. It costs a dictionary lookup on the server and saves a full round trip through the model.

## Challenge 4: The Ambiguous Symbol

Add a second suffix match: pretend the ELF contains both `mort_init` and `vga_init`, then call `debug_break` with location `init`. What should happen? Now change `lookup` to just pick the first match. Describe a debugging session where that silently wastes an agent's next ten calls.

**Hint:** If you pick one, the breakpoint is set, `debug_continue` stops somewhere, and the agent assumes it's in the function it asked for. Ambiguity is exactly when a tool should refuse and list the options, because a guess turns one cheap error into a confusing session.

## Challenge 5: A Docstring That Lies

Change `debug_continue`'s docstring to "Resume execution." with no mention of the timeout or what happens after it. Then connect the server to a real agent (`claude mcp add mini -- python main.py --serve`) and ask it to "run until add, but don't set a breakpoint". Watch what it does after the first "Still running".

**Hint:** Agents plan from the tool descriptions. Without the sentence about `debug_interrupt`, many will call `debug_registers` next, get the "target is running" error, and only then find the right tool. The error message rescued the session, but the docstring should have prevented it. Good tools have both.

→ [Next: Module 08](../08-the-triple-fault/README.md)
