# Module 05: Symbols and Source Lines

## The Concept

After Module 04 the agent can stop the CPU and read `eip=0x100029`. That number is useless
to it. It's useless to you too, unless you have the linker map memorised.

What the agent needs is this:

```
eip = 0x100029 <add+0x9> at kernel.c:10
```

Two lookups produce that line, and both come from the kernel's own ELF file:

```
            ELF file (kernel.elf)
   ┌───────────────────────────────────────┐
   │ .symtab      name → address, size     │──► "add+0x9"
   │ .debug_line  address → file, line     │──► "kernel.c:10"
   └───────────────────────────────────────┘
```

Neither needs the target. A kernel is linked at the address it runs at, with no
relocation, so an ELF symbol value *is* the address the gdbstub reports. (gdbstub-mcp
still takes a `load_bias` for the odd image loaded elsewhere.)

## The Symbol Table

`.symtab` is a list of entries. Each has a name, a value (the address), a size, a type,
and a section index:

```
'add'       0x100020  17  STT_FUNC    section 1 (.text)
'counter'   0x1021a0   4  STT_OBJECT  section 5 (.bss)
'_start'    0x10000c   0  STT_FUNC    section 1      ← assembly: no size
'MAGIC'   0x1badb002   0  STT_NOTYPE  SHN_ABS        ← a constant!
'FLAGS'          0x0   0  STT_NOTYPE  SHN_ABS        ← a constant!
```

Symbolizing an address means finding the last symbol that starts at or below it. With the
table sorted by address, that's a binary search:

```python
i = bisect.bisect_right(starts, addr) - 1
start, size, name, _ = symbols[i]
off = addr - start
if off == 0:
    return name
if size == 0 or off < size:
    return f"{name}+{off:#x}"
return f"{addr:#x}"
```

`bisect_right` returns the insertion point *after* any equal entries, so `- 1` lands on
the symbol that starts exactly at `addr` when one does. The size check stops an address in
the padding after `add` (0x100031) from being called `add+0x11`. Assembly labels have size
0, so they get the benefit of the doubt.

## The SHN_ABS Bug

This is a real bug from building gdbstub-mcp. The first time it connected to MORT OS, the
CPU was halted at the reset vector, and the tool said:

```
pc = 0xfff0 <SYSCALL_VECTOR+0xff70>
```

`SYSCALL_VECTOR` is `.set SYSCALL_VECTOR, 0x80` in the kernel's assembly: a **constant**,
not a location. The assembler still emits it into `.symtab`, with section index `SHN_ABS`
("absolute, belongs to no section"). Since 0x80 is the closest symbol below 0xfff0 and its
size is 0, the lookup above happily used it.

The test kernel reproduces it with `MAGIC` and `FLAGS` from the multiboot header:

```
[1] naive table: FLAGS+0xfff0    fixed table: 0xfff0
```

The fix is one line when loading:

```python
if sym["st_shndx"] == "SHN_ABS":
    continue
```

The general lesson: not every symbol is a place. Section symbols, file symbols and absolute
constants all need filtering before you call anything a label.

## The DWARF Line Program

Compile with `-g` and the ELF gains `.debug_line`. You might expect a table in it. Instead
it holds a **program**: a bytecode for a tiny state machine whose registers include
`address`, `file` and `line`. Opcodes like "advance line by 1, advance address by 3" are
cheaper to store than full rows. Each time the program emits a row, you get a snapshot of
the state:

```
0x100020  kernel.c:9      int add(int a, int b) {
0x100029  kernel.c:10         return a + b;
0x10002c  kernel.c:10
0x10002f  kernel.c:10
0x100040  kernel.c:13     int compute(int n) {
...
```

pyelftools runs the state machine for you. You collect the rows:

```python
for entry in prog.get_entries():
    st = entry.state
    if st is None or st.end_sequence:
        continue
    idx = st.file if version >= 5 else st.file - 1
    rows.append((st.address, files[idx].name.decode(), st.line))
```

Two details bite people:

- `end_sequence` rows mark one-past-the-end of a code block. They aren't a line.
- **DWARF 5 numbers files from 0; DWARF 2–4 from 1.** Get this wrong and every location
  shows the neighbouring file's name. The test kernel is DWARF 4; Zig's default for MORT OS
  isn't always.

`address → line` is the same bisect as symbols. `line → address` is a filter, and it
usually returns several addresses:

```
[5] kernel.c:10 -> 0x100029, 0x10002c, 0x10002f
```

`return a + b;` compiles to three instructions and each gets a row. A breakpoint on
`kernel.c:10` should go on the first. gdbstub-mcp does that and tells the agent so:
`(kernel.c:10 maps to 3 addresses; using the first (0x100029))`.

## The Data-Address Bug

The second real bug: `debug_registers` on MORT OS printed

```
esp = 0x0010c4c8  <stack_bottom+0x3ff8> at idt.s:226
```

The stack has no source line. But the stack is at a higher address than all the code, so
bisect returned the **last row of the line table**, and `idt.s:226` happened to be last.
The test kernel shows the same with `counter`:

```
[4] counter 0x1021a0: naive -> kernel.c:27, fixed -> None
```

The fix: only answer for addresses inside an executable section (`SHF_EXECINSTR`).

## Address Expressions

The last piece turns what an agent types into an address. gdbstub-mcp's `resolve()` accepts:

```
0x100029        → a number
add             → symbol address
add+0x9         → symbol plus offset
kernel.c:10     → first address of that line
$esp+8          → register plus offset (needs a live target)
```

Every tool that takes an address uses this one function, so the agent learns one syntax.

## Putting It Together

```
[6] stopped: eip=0x100029 = add+0x9 at kernel.c:10
```

That line is what `describe_stop()` in gdbstub-mcp prints after every breakpoint, step and
interrupt. It's the single most useful thing the server does.

→ [Next: Module 06](../06-backtraces/README.md)
