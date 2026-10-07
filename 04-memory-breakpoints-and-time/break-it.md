# Break It: Memory, Breakpoints and Time

## Challenge 1: Remove the Running Guard

Delete the `if self.running: raise ...` check in `request()`. Then call `rsp.resume()`
followed immediately by `rsp.eip()`. What does `eip()` return, and what happens to the next
command after that?

**Hint:** The stub doesn't answer `g` while running, so `_read_packet` times out after 10s.
Interrupt and the stop reply `T02...` arrives. Then the *next* request reads that stale reply
as its own answer, and `bytes.fromhex("T02...")` explodes. Every later reply is shifted by
one. This is why the guard exists: the protocol has no request IDs to re-synchronise with.

## Challenge 2: Software Breakpoint vs a Write

Set a **software** breakpoint (`Z0`) on `add`, then before continuing, read 1 byte at `add`
with `m`. Do you see `0xCC`? Now write fresh bytes over `add` with `M` (copy them from the
ELF) and continue. Does the breakpoint still fire?

**Hint:** Under TCG (QEMU's default software CPU), the gdbstub doesn't patch guest memory
for `Z0`. It keeps an internal breakpoint list, so you'll read the original byte and the
breakpoint survives a rewrite. Under KVM, and with most hardware stubs (OpenOCD, a kernel's
own kgdb), memory *is* patched. If your tool will ever talk to those, treat "survives a
rewrite" as TCG-only behaviour.

## Challenge 3: Five Hardware Breakpoints

Set `Z1` hardware breakpoints on `add`, `compute`, `kmain`, `triple_fault` and `_start`.
What does the fifth one reply? Does it match what x86 can really do?

**Hint:** x86 has four debug address registers (DR0–DR3). Under TCG, QEMU emulates
breakpoints in software and may accept more than four, which makes your code look correct
in the emulator and fail on real hardware or under KVM. A good tool surfaces the stub's error
instead of assuming success. Check what your `z()` does with an `E` reply.

## Challenge 4: Off-by-One Watchpoint

Set a 4-byte write watchpoint on `counter + 2`. `kmain` writes all 4 bytes of `counter`.
Does the watchpoint fire? Try a length of 1 at `counter + 3`.

**Hint:** Hardware watchpoints match ranges, and x86 requires them to be aligned to their
length. QEMU may round, reject, or match on any overlap. Write down what you observe; it
decides how you document `length` in the `debug_break` tool.

## Challenge 5: Interrupt a Halted Target

Call `rsp.interrupt()` when the CPU is already halted (right after connecting). What happens?

**Hint:** No stop reply comes, because nothing was running, so `wait_stop` times out and the
assertion fires. gdbstub-mcp's `interrupt()` checks `self.running` first and raises
"target is not running". Add that check, and think about what message the agent should see.

→ [Next: Module 05](../05-symbols-and-source-lines/README.md)
