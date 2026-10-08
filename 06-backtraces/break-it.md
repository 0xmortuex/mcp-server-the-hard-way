# Break It: Backtraces

## Challenge 1: Break on the `ret`

Find `add`'s `ret` instruction (`debug_disassemble`, or disassemble `funcs.code(add, 17)`
with capstone) and break there instead of on the entry. Run all three walks. Which ones are
right?

**Hint:** At `ret`, `pop ebp` has already restored the caller's frame pointer, so the naive
walk skips `compute` again, the same bug from the other end. The `insn.mnemonic == "ret"`
branch in `unframed_return` is what fixes it. Remove that branch and watch the assertion
you'd need fail.

## Challenge 2: Build Without Frame Pointers

In `kernel/build.py`, change `-O0 -fno-omit-frame-pointer` to `-O2`, rebuild, and run the
module. Which walk survives?

**Hint:** At `-O2`, `add` is tiny and probably gets inlined into `compute`, so the
breakpoint may not even exist as a separate function. Break on `compute` instead. The
frame-pointer walk now reads whatever happens to be in `ebp`, and the sanity checks in
`fp_walk` decide whether you get garbage or nothing. The stack scan should still find
`kmain`.

## Challenge 3: Plant a Stale Frame

Before continuing, write a fake return address into an unused stack slot below the current
frame. Use `0x100071` (just after the call in `compute`), and write it at `esp - 64` with an
`M` packet. Then trigger a few calls and scan again. Does the fake frame show up?

**Hint:** Stack below `esp` is dead, and the scan starts at `esp`, so it shouldn't. But once
the kernel calls deeper and returns, that region is above a *later* `esp`, and any word
nobody overwrote survives. This is exactly how real stale frames appear. Can you think of a
check that would reject it? (Is the scanned frame's address consistent with the previous
frame's stack position?)

## Challenge 4: A Loop in the Chain

Corrupt the chain: write the current `ebp` value into `[ebp]`, so the frame points at
itself. Run `fp_walk`. What stops it?

**Hint:** The `nxt <= ebp` check, since a frame can't be its own caller. Remove it and the
walk spins until `limit`. gdbstub-mcp also keeps a `seen` set. Which check would still catch
a 2-frame cycle?

## Challenge 5: 64-Bit

Everything here assumed i386: 4-byte words, `ebp`/`esp`, `[ebp+4]`. List every line you
would change for an x86_64 kernel, then compare with how gdbstub-mcp's `Session` avoids
hard-coding them.

**Hint:** The word size comes from the program counter's `bitsize` in `target.xml`
(Module 03), and `layout.fp()`/`layout.sp()` find `rbp`/`rsp` by name. The prologue
recogniser compares against `fp_name`/`sp_name` rather than the literal `"ebp"`, so the same
code reads `push rbp; mov rbp, rsp`.

## Challenge 6: The Line After the Call

Add source lines to this module's backtrace using `05-symbols-and-source-lines`' line
table, but look up each frame's *return address* directly. Break on `add` and compare
the `_start` frame with `boot.s`. Then make a C function whose call is the last statement
on its line, followed by a different statement, and watch the caller frame land on the
wrong line.

**Hint:** Look up `return_address - 1` for every frame except #0, but print the real
return address. If the `call` is the very last instruction of a `noreturn` function,
`return_address` doesn't even belong to the same function, and the `- 1` fixes the
*symbol* too, not just the line.

→ [Next: Module 07](../07-designing-tools-for-an-agent/README.md)
