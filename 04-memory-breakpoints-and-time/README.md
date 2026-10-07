# Module 04: Memory, Breakpoints and Time

## The Concept

By now you can frame packets (Module 02) and read registers (Module 03). Both are
*question and answer*: you send a packet, the stub replies, and the CPU sits halted the
whole time.

This module adds the commands that change the machine, and one of them breaks the
question-and-answer model completely:

- **Memory**: `m` reads and `M` writes. These are still simple request and reply.
- **Breakpoints and watchpoints**: `Z` sets them and `z` clears them. Also simple request and reply.
- **Continue**: `c` starts the CPU running. It has no reply until the CPU stops, which might be never.

The memory and breakpoint commands are easy. Continue is the reason a debugger needs
state, and the reason an MCP tool built on it needs a timeout.

## Reading Memory in Chunks

```
→ $m100020,8#..        "8 bytes at 0x100020"
← $5589e58b450c8b45#.. hex, two characters per byte
```

The reply is hex, so a byte costs two characters. A stub also limits how big a packet can
be: QEMU announces `PacketSize=20020` in its `qSupported` reply. Ask for too much and the
reply gets truncated or rejected. So reads are split:

```python
def read_memory(self, addr, length, chunk=0x800):
    out = bytearray()
    while len(out) < length:
        n = min(chunk, length - len(out))
        out += bytes.fromhex(self.request(f"m{addr + len(out):x},{n:x}").decode())
    return bytes(out)
```

`len(out)` does double duty. It's how many bytes we have, and it's also the offset of the
next chunk. 0x800 bytes is 0x1000 hex characters, comfortably under any real stub's limit.

Writing is the mirror image: `M addr,len:hexbytes`, answered with `OK`.

`main.py` checks memory against the ELF. QEMU loaded `kernel.elf` into RAM, so the bytes
at `add` must be exactly the bytes in the file's `.text` section:

```
[1] read add()'s code from memory, matches the ELF: 55 89 e5 8b 45 0c 8b 45 ...
```

`55 89 e5` is `push ebp; mov ebp, esp`, the function's prologue. You'll meet it again in
Module 06.

## Breakpoints: Software vs Hardware

```
Z0,addr,1   software breakpoint
Z1,addr,1   hardware breakpoint
Z2,addr,len write watchpoint
Z3,addr,len read watchpoint
Z4,addr,len access (read or write) watchpoint
```

A **software** breakpoint is a patch: the stub saves the byte at `addr` and writes `0xCC`
(`int3`) over it. Executing that traps. There's no limit on how many you can have.

A **hardware** breakpoint uses the CPU's debug registers DR0–DR3. Nothing is written to
memory; the CPU compares every instruction fetch against those registers. There are only
four. But they matter in exactly the places osdev hurts most:

- **Code that isn't there yet.** A breakpoint on a program your kernel will load later
  gets overwritten by the load if it's a patch. A debug register can't be overwritten.
- **Address spaces that change.** Turning on paging, or switching CR3, can move what a
  virtual address points to. A patched `int3` stays in the old physical page.

That's why gdbstub-mcp's `debug_break` takes `kind="hw"` and its docstring says *use this
before paging is enabled or for code that isn't loaded yet*. `main.py` uses a hardware
breakpoint on `add`:

```
[3] hw breakpoint hit: T05thread:01;  eip=0x100020
```

`T05` is a stop reply: `T` plus a signal number, here 5 (SIGTRAP), followed by `key:value;`
fields.

## Watchpoints

A watchpoint stops the CPU when memory is **touched** rather than executed. It's the
answer to "who keeps overwriting my variable?":

```
[4] watchpoint hit: T05thread:01;watch:1021a0;  (wrote counter, eip=0x1000c0)
```

The stop reply carries a `watch:` field naming the address. Notice `eip`: x86 data
breakpoints fire *after* the instruction that wrote, so `eip` points to the instruction
after the store. A debugger that reports "the write happened at eip" is off by one
instruction. gdbstub-mcp reports the watched address and the current location, and leaves
it there.

## Time: The Packet With No Reply

Every packet so far gets an answer within milliseconds. `c` doesn't:

```
→ $c#63
                ... the CPU runs ...
                ... maybe forever ...
← $T05thread:01;#..     only when something stops it
```

A naive `request("c")` blocks until the kernel hits a breakpoint. If it never does, your
MCP tool hangs, the agent's tool call hangs, and the session is dead. So continue is split
into three operations:

```python
def resume(self):                 # send 'c', return immediately
    self.sock.sendall(frame(b"c"))
    self.running = True

def wait_stop(self, timeout):     # wait a bounded time for the stop reply
    try:
        reply = self._read_packet(timeout)
    except TimeoutError:
        return None               # "still running" is an ANSWER, not an error
    self.running = False
    return reply

def interrupt(self):              # force a stop
    self.sock.sendall(b"\x03")    # raw byte, outside any packet
    return self.wait_stop(5.0)
```

Three details matter:

1. **`None` isn't an error.** A kernel that's still running after 10 seconds is often
   exactly what you wanted. The MCP tool turns it into "Still running after 10s — use
   debug_wait to keep waiting or debug_interrupt to halt it" (Module 07).
2. **`0x03` is out of band.** It isn't a `$...#` packet; it's the byte a terminal sends on
   Ctrl-C. The stub answers with a stop reply carrying signal 2 (SIGINT): `T02`.
3. **The running flag guards everything else.** While the CPU runs, a stub only answers the
   interrupt. Send `g` and you'll get nothing, then read the stop reply later as if it were
   the register dump. So `request()` refuses:

```python
if self.running:
    raise RuntimeError("target is running - interrupt it or wait for it to stop")
```

```
[5] after 1s: still running; asking for registers -> target is running - interrupt it or wait for it to stop
    interrupted: T02thread:01;  eip=0x100071
```

The real client in gdbstub-mcp also holds a lock around the whole send-and-read, because an
MCP server can receive two tool calls at once. Two threads reading one socket each get half
of the other's reply.

## What This Gives the Agent

With memory, breakpoints and controlled time, an agent can ask questions that need the
CPU to move: *break where `counter` changes, tell me who changed it*. It can also never
hang the session, because every wait is bounded. What it can't do yet is understand
`0x1000c0`. That's next.

→ [Next: Module 05](../05-symbols-and-source-lines/README.md)
