# Module 08: The Triple Fault

## The Concept

Every hobby OS developer hits this eventually. You change something small, boot, and the
machine **reboots instantly**. There's no panic message and no output, just the BIOS
again, in a loop. That's a triple fault, and from the outside it looks the same whatever
caused it.

It's the perfect job for an agent, because the evidence exists but is buried. QEMU can
log every interrupt it delivers. A few seconds of boot produces thousands of lines,
nearly all of them timer ticks. The three lines that explain the crash are in there, and
this module digs them out.

`main.py` crashes the test kernel on purpose through the debugger, then parses QEMU's
log into a root cause.

## How x86 Reports an Error

When the CPU hits a problem it raises an **exception**: a numbered event (the vector)
that makes it jump to a handler. Handlers live in the **IDT** (Interrupt Descriptor
Table), one 8-byte *gate* per vector, at an address loaded with `lidt`.

| Vector | Name | Meaning |
|---|---|---|
| 0x06 | #UD | invalid opcode: the bytes at EIP aren't an instruction |
| 0x0D | #GP | general protection: something broke a protection rule |
| 0x0E | #PF | page fault: bad virtual address (address in CR2) |
| 0x08 | #DF | double fault: an exception happened *while delivering* another |

The key word is *delivering*. Raising an exception means reading the IDT gate, checking
it, and switching stacks. All of that can fail, and the failures escalate:

```
 ud2 executes
     │
     ▼
  #UD (vector 6) ── read IDT[6] ── gate missing ──► #GP, error code names IDT entry 6
                                                     │
                                       read IDT[13] ─┴─ missing ──► #DF (double fault)
                                                                      │
                                                        read IDT[8] ──┴── missing ──► TRIPLE FAULT
                                                                                       CPU resets
```

A triple fault isn't an exception. There's no handler for it. The CPU gives up and
resets. That's why you see a reboot and nothing else.

The test kernel's `triple_fault()` sets this up deliberately:

```c
static const struct idtr empty = {0, 0};
__asm__ volatile("lidt %0" : : "m"(empty));   // IDT with zero entries
__asm__ volatile("ud2");                       // guaranteed #UD
```

## Error Codes: The Clue Most People Skip

Some exceptions push an **error code**. For #GP, #NP, #SS and #TS it describes a
*selector*: what the CPU was trying to load when it failed.

```
 bit:   15 ............ 3   2    1    0
       ┌─────────────────┬────┬────┬─────┐
       │      index      │ TI │IDT │ EXT │
       └─────────────────┴────┴────┴─────┘
```

If the **IDT** bit is set, the index is an IDT vector. Our #GP has error code `0x32` =
`0b110010`: the IDT bit is set and the index is `0x32 >> 3 = 6`. In plain English: *"I
couldn't use IDT entry 6."* Entry 6 is #UD's handler. The error code tells you exactly
which handler is broken.

Page faults use a different layout. Bit 0 tells you whether the page was present or
whether it was a protection violation, bit 1 write vs. read, bit 2 user vs. kernel, and
bit 4 instruction fetch. The faulting address is in **CR2**. An address under `0x1000`
is almost always a NULL dereference.

## Reading QEMU's Log

Start QEMU with `-d int,cpu_reset -D int.log -no-reboot`:

- `-d int` logs every interrupt
- `cpu_reset` logs the resets
- `-no-reboot` stops the VM at the first triple fault instead of looping

Here's a real excerpt, from gdbstub-mcp's test fixtures:

```
Servicing hardware INT=0x20
   150: v=20 e=0000 i=0 cpl=0 IP=0008:00105f11 pc=00105f11 SP=0010:0010c4b8 ...
check_exception old: 0xffffffff new 0x6
   152: v=06 e=0000 i=0 cpl=0 IP=0008:00007000 pc=00007000 SP=0010:0010c4b8 ...
check_exception old: 0xffffffff new 0xd
   153: v=0d e=0032 i=0 cpl=0 IP=0008:00007000 pc=00007000 SP=0010:0010c4b8 ...
check_exception old: 0xd new 0xd
   154: v=08 e=0000 i=0 cpl=0 IP=0008:00007000 pc=00007000 SP=0010:0010c4b8 ...
check_exception old: 0x8 new 0xd
Triple fault
```

Each event line is followed by a full register dump, which is how a few seconds of boot
becomes hundreds of lines. The fields that matter:

- `v=` is the vector
- `e=` is the error code
- `i=1` marks a software `int N` (a syscall, not a fault)
- `pc=` is where it happened

Line 150 is the timer. Vectors from `0x20` up are hardware IRQs. The parser drops those
and every `i=1`:

```python
if vector >= 0x20 or software:
    continue
```

What's left is the chain: `#UD → #GP → #DF`, followed by `Triple fault`.

## Finding the Root

The first exception in the final chain is the cause, and everything after it is the CPU
failing to report it:

```python
root = events[0]
```

The full gdbstub-mcp walks back from the last #DF through consecutively numbered events.
That way an earlier fault the kernel *did* handle, minutes before, isn't blamed. The
test kernel crashes on the first fault, so `events[0]` is enough here.

The most useful sentence in the output isn't the root cause, though. It's **why the
root cause killed the machine**:

```python
missing = [ev["error"] >> 3 for ev in events
           if ev["vector"] in (0x0B, 0x0D) and ev["error"] & 2]
```

A `ud2` in a working kernel produces a nice "invalid opcode" panic from your handler. It
only triple-faults because IDT entry 6 is unusable. So there are two bugs, and the
broken IDT is the one to fix first, because it hides every future crash too. The output
says exactly that:

```
TRIPLE FAULT. Root cause: #UD invalid opcode at triple_fault+0xa.
It escalated because IDT entry 6 has no usable gate: the CPU couldn't run
the handler, faulted again, then gave up.
```

## Driving the Crash From the Debugger

The demo uses the debugger to *cause* the crash, which is a useful technique in its own
right:

```python
assert stub.ask(f"Z1,{kmain:x},1") == "OK"      # hardware breakpoint at kmain
stub.ask("c", timeout=20)                        # run until we're in kmain
assert stub.ask(f"M{trigger:x},4:01000000") == "OK"   # trigger = 1 (little-endian)
final = stub.ask("c", timeout=20)                # ...and let it crash
assert final.startswith("W")                     # W00: the VM exited
```

`W00` is the stub's last packet: "process exited". With `-no-reboot`, QEMU exits at the
triple fault, so the debugger sees the machine die. gdbstub-mcp turns that into
*"Target exited (W00). The VM is gone"*, which tells the agent it's time for
`debug_explain_fault`.

## Run It

```bash
qemu-system-i386 -kernel ../kernel/kernel.elf -s -S -d int,cpu_reset -D int.log -no-reboot -display none
python main.py --port 1234 --elf ../kernel/kernel.elf --log int.log
```
