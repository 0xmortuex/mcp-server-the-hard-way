# Break It: Symbols and Source Lines

## Challenge 1: Two Names, One Address

`counter` and `stack_top` both live at `0x1021a0`. Symbolize that address. Which name do you
get? Now swap the order the two appear in the sorted list. Does the answer change?

**Hint:** `sorted()` on tuples breaks ties by size, then name, so the result is stable but
arbitrary. An agent asking "what's at 0x1021a0?" deserves to hear about both. Prefer
`STT_OBJECT` over a size-0 label, or list every symbol starting at that address.

## Challenge 2: Strip the Kernel

Run `python -m ziglang objcopy --strip-debug kernel/kernel.elf stripped.elf` and point
`main.py` at the copy. Which steps still work, and which crash?

**Hint:** `.symtab` survives `--strip-debug` but `.debug_line` doesn't, so steps 1 and 2 work
and step 3 dies on `get_dwarf_info()`. gdbstub-mcp checks `elf.has_dwarf_info()` and reports
"no DWARF line info" at connect time instead of crashing. Try `--strip-all` too: what should
the tool say then?

## Challenge 3: The Off-by-One File

Change `idx = st.file if version >= 5 else st.file - 1` to always use `st.file - 1`, then
rebuild the kernel with DWARF 5 (add `-gdwarf-5` to `kernel/build.py`). What file name does
`kernel.c:10` resolve to now?

**Hint:** With DWARF 5, file 0 is the primary source file, so `st.file - 1` points one entry
too early, or wraps to `-1` and Python happily returns the *last* file. Silent wrong answers
are worse than crashes. Add a bounds check that returns `"??"` like gdbstub-mcp's `filename()`.

## Challenge 4: Break on a Line With No Code

Ask for `kernel.c:11` (the closing brace of `add`). What does `addresses_for_line` return?
What should `debug_break` tell the agent?

**Hint:** An empty list, because no instruction maps to a lone `}`. gdbstub-mcp raises
"no code at kernel.c:11 in the DWARF line table" and lists the files that *do* have line
info. That's an error the agent can act on. Making it pick the next line with code, the way
gdb does, is a good extension.

## Challenge 5: Prefixed Names

MORT OS compiles Mort to C, so `kmain` becomes `mort_kmain` in the ELF. Add a lookup that
accepts `kmain` when exactly one symbol ends in `_kmain`. What should happen with `init` if
both `a_init` and `b_init` exist?

**Hint:** It should be an error naming both candidates, never a guess. gdbstub-mcp's
`SymbolTable.lookup()` returns `(symbol, note)`, and the note ("resolved 'kmain' to
'mort_kmain'") is shown to the agent so it knows a translation happened.

→ [Next: Module 06](../06-backtraces/README.md)
