# Module 06: Backtraces

## The Concept

"Stopped at `add+0x9`" tells the agent *where*. Debugging usually needs *how it got
there*: which caller passed the bad argument, which path led to the fault. That's a
backtrace:

```
#0  0x100029 <add+0x9>      at kernel.c:10
#1  0x100071 <compute+0x31> at kernel.c:16
#2  0x1000b2 <kmain+0x12>   at kernel.c:28
#3  0x100016 <_start+0xa>   at boot.s:22
```

The CPU doesn't keep a call stack for you. It has a stack pointer, and `call` pushes a
return address onto the stack. Everything else is convention. A backtrace is the debugger
reconstructing history from those conventions, and when a convention isn't followed,
from guesses.

This module builds both approaches: the convention (frame pointers) and the guess
(stack scanning). It also covers the bug that hides in the convention.

## The Frame-Pointer Chain

Compiled with frame pointers, every x86 function starts with the same two instructions:

```
add:    push ebp          ; save the caller's frame pointer
        mov  ebp, esp     ; our frame starts here
        ...
        pop  ebp          ; restore the caller's
        ret               ; pop the return address and jump to it
```

After that prologue the stack looks like this. Higher addresses are at the top, because
the stack grows down:

```
           ┌──────────────────────┐
           │ ...compute's frame...│
           │ return into compute  │  ← [ebp + 4]
 ebp ───►  │ compute's saved ebp  │  ← [ebp + 0] ──► points at compute's frame
           │ add's locals         │
 esp ───►  └──────────────────────┘
```

Each frame stores a pointer to the previous one, so the frames form a linked list. Walking
it is a loop:

```python
while ebp:
    ret = rsp.word(ebp + 4)      # where this frame returns to
    if funcs.find(ret) is None:  # not inside a known function: chain ended
        break
    out.append(ret)
    nxt = rsp.word(ebp)          # the caller's ebp
    if nxt <= ebp:               # callers live at HIGHER addresses
        break
    ebp = nxt
```

The two `break`s are sanity checks. A real stack ends in garbage: `_start` never set up
an `ebp`, or a corrupted frame points somewhere random. Stopping when the return address
isn't code, or when the chain doesn't move up the stack, keeps the walk from looping
forever or reading wild memory.

## The Prologue Bug

This bug surfaced in gdbstub-mcp's integration test. The test breaks on `add`, continues,
and expects four frames. It got three:

```
AssertionError: assert ['add', 'kmain', '_start'] == ['add', 'compute', 'kmain', '_start']
  At index 1 diff: 'kmain' != 'compute'
```

The breakpoint is on `add`'s **first instruction**. `push ebp` hasn't run yet, so `ebp`
still points at `compute`'s frame. The walk reads `compute`'s saved return address, which
leads into `kmain`, and `compute` itself never appears:

```
 at add's first instruction:

 ebp ───►  │ kmain's saved ebp    │   ← walk starts HERE (compute's frame)
           │ ...compute locals... │
           │ return into compute  │   ← [esp]: add's return address, never visited
 esp ───►  └──────────────────────┘
```

`main.py` reproduces it exactly:

```
[1] naive frame-pointer walk:
    #0  0x100020 <add>
    #1  0x1000b2 <kmain+0x12>
    #2  0x100016 <_start+0xa>
```

Every real debugger handles this. Before the frame is set up, the current function's
return address has to be read from `esp`:

| Where `eip` is | Return address is at | Then walk from |
|---|---|---|
| before `push ebp` | `[esp]` | `ebp` (caller's) |
| after `push ebp`, before `mov ebp, esp` | `[esp + 4]` | `ebp` |
| on `ret` (after `pop ebp`) | `[esp]` | `ebp` |
| anywhere else | (normal walk) | `ebp` |

To know which case applies, the debugger disassembles from the function's start up to
`eip` and counts which prologue instructions have run:

```python
for insn in X86.disasm(code, fn_start):
    if insn.address == eip:
        if insn.mnemonic == "ret":
            return rsp.word(esp)
        return rsp.word(esp + 4) if pushed else rsp.word(esp)
    if insn.mnemonic == "push" and insn.op_str == "ebp":
        pushed = True
    elif insn.mnemonic == "mov" and insn.op_str == "ebp, esp":
        return None          # frame is set up: the normal walk is right
    else:
        return None          # not a standard prologue: don't guess
```

The final `else` matters: if the function doesn't start with the standard prologue, the
code refuses to apply the fix rather than guessing. With the fix:

```
[2] prologue-aware walk:
    #0  0x100020 <add>
    #1  0x100071 <compute+0x31>
    #2  0x1000b2 <kmain+0x12>
    #3  0x100016 <_start+0xa>
```

Real debuggers go further and read DWARF's `.debug_frame` call-frame information, which
describes the stack layout at *every* instruction. That's the right long-term answer, and
far more code than these 20 lines.

## Stack Scanning: When There Are No Frame Pointers

MORT OS builds with `-O2`. Compilers at `-O2` drop frame pointers by default
(`-fomit-frame-pointer`), and `ebp` becomes an ordinary register. The chain doesn't exist.

The fallback is a guess: scan the stack, and treat any word that looks like a return
address as one. "Looks like" has two tests:

1. The value is inside a known function.
2. The instruction just before it is a `call`, because return addresses always point
   right after one.

```python
for back in (5, 6, 2, 3, 7):           # common x86 call lengths
    insns = list(X86.disasm(code_before(addr, back), addr - back))
    if len(insns) == 1 and insns[0].mnemonic == "call" and insns[0].size == back:
        return True
```

Test 2 is what makes it usable. Plenty of stack words point into code (function pointers,
for example), but few of them point *just after a call*. On MORT OS this produced
`_start+0x10 at boot.s:47`, the real caller of `kmain`, from an `-O2` kernel with no
frame pointers at all.

It's still a heuristic, and gdbstub-mcp labels it as one in its output:

- **Stale frames.** A return address left in a dead stack slot from an earlier call still
  passes both tests.
- **Missed frames.** A tail call (`jmp` instead of `call`) leaves no return address.

The fix for both is the same: build kernels you debug with `-fno-omit-frame-pointer`. The
test kernel does, which is why its backtraces are exact.

## What the Tool Does

`debug_backtrace` takes `mode`: `"fp"`, `"scan"`, or `"auto"` (the default), which tries
the frame-pointer walk and falls back to scanning when the chain is empty. It always
reports which method it used, so the agent knows how far to trust the result.

→ [Next: Module 07](../07-designing-tools-for-an-agent/README.md)
