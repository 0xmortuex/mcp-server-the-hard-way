# Module 09: Testing It For Real

## The Concept

A debugger is a strange thing to test. Its job is to sit between two systems you don't
control: an agent on one side and a CPU on the other. The bugs gather at the edges:
bytes split across TCP reads, an ack arriving glued to a packet, a symbol table entry
that isn't what it looks like.

This module covers three layers of tests, from fast to real:

```
 ┌───────────────────────────────┐   milliseconds, no QEMU
 │ 1. fake stub on a real socket │   framing, acks, nacks, timeouts
 ├───────────────────────────────┤
 │ 2. fixture kernel + real QEMU │   seconds; registers, symbols, backtraces, crashes
 ├───────────────────────────────┤
 │ 3. CI boots QEMU every push   │   nobody's laptop is the source of truth
 └───────────────────────────────┘
```

`main.py` runs layer 1 against a fake stub, then checks layer 2 against the QEMU that
`verify.py` started.

## Layer 1: A Fake Stub, Not a Mock

The tempting shortcut is to mock `socket.recv` and return a canned reply. Don't. A mock
returns what you *assumed* the wire delivers, and the bugs live in what it actually
delivers.

So the fake stub is a real server on `127.0.0.1:0` (port 0 means "any free port"),
running on a thread:

```python
self.srv = socket.socket()
self.srv.bind(("127.0.0.1", 0))
self.srv.listen(1)
self.port = self.srv.getsockname()[1]
threading.Thread(target=self._serve, daemon=True).start()
```

The client connects to it exactly as it would connect to QEMU. Then the stub
misbehaves on purpose, in the ways real networks and real stubs do.

**It splits packets.** Every reply goes out in two `sendall` calls with a pause between
them:

```python
conn.sendall(good[:3])
time.sleep(0.01)
conn.sendall(good[3:])
```

A client that assumes "one `recv` = one packet" fails here instantly. That bug would
otherwise only show up on a loaded machine, with a large register dump, at random.

**It corrupts a checksum.** RSP's checksum is a one-byte sum, and the receiver must
reply `-` so the sender retransmits:

```python
conn.sendall(good[:-2] + b"00")      # wrong checksum
verdict = conn.recv(1)
assert verdict == b"-", f"client accepted a corrupt packet ({verdict!r})"
```

The stub *asserts* that the client rejected the bad packet, and the test then asserts
the client accepted the resend. Both sides of the protocol are checked. gdbstub-mcp's
`test_bad_checksum_is_nacked_and_resent` does the same against the full client.

**It knows what QEMU actually does.** One assertion in Part B is there because it
caught a real mistake while writing Module 07. That module's first draft only
implemented no-ack mode, and QEMU doesn't offer it:

```python
assert "QStartNoAckMode+" not in sup
```

Part B asks the real QEMU what it supports. If a QEMU update ever adds no-ack mode, this
line fails loudly, so you can update the course instead of finding out from a confused
reader.

## Layer 2: A Fixture Kernel

To test symbols, line numbers and backtraces you need a binary whose contents you know.
The course's `kernel/kernel.c` is about 40 lines: `add`, `compute`, `kmain`, a
`counter` to watch, and a `trigger` that causes a triple fault. It's built with:

```
-O0 -g -fno-omit-frame-pointer -fno-sanitize=undefined
```

Each flag earns its place:

- `-O0` keeps `add` a real function instead of inlining it, so "break on add" means
  something.
- `-g` emits DWARF line tables for `kernel.c:10`.
- `-fno-omit-frame-pointer` makes the backtrace exact: `add ← compute ← kmain ← _start`.
- `-fno-sanitize=undefined` matters because Zig's `cc` enables UBSan in debug builds,
  and a freestanding kernel has no runtime to link it against.

**The binary is committed** (`kernel/kernel.elf`, about 7 KB). That looks wrong, since
build outputs normally don't go in git, but the alternative is worse. If CI rebuilt it,
the tests would depend on the exact compiler version, and a new Zig release that moved
`add` by 4 bytes would fail every test that mentions an address. Committing the binary
makes the test input fixed. `kernel/build.py` rebuilds it on purpose, when you choose to.

## What the Real Tests Caught

When gdbstub-mcp first ran against real QEMU and real ELF files, four bugs appeared
that no unit test had caught. They were all at the edges:

1. **Constants posing as locations.** `boot.s` contains `.set MAGIC, 0x1BADB002`. ELF
   records `MAGIC` as a symbol with section `SHN_ABS`: it's a number, not a place. MortOS
   had an absolute `SYSCALL_VECTOR = 0x80`, so address `0xfff0` symbolized as
   `SYSCALL_VECTOR+0xff70`. The fix was to skip `SHN_ABS` symbols when labelling
   addresses.
2. **Line numbers for data.** A stack address came back as `at idt.s:226`, because the
   line lookup found the nearest code line *below* it. The fix is that line tables only
   describe code, so the lookup returns nothing unless the address is in an executable
   section.
3. **Flags defined in another feature.** `target.xml` can define a flag type (like CR0's
   bits) in one `<feature>` block and use it in another. A parser that scopes flag types
   per feature silently decodes nothing.
4. **The prologue backtrace.** Stopped on `add`'s first instruction, before
   `push ebp; mov ebp, esp`, EBP still belongs to `compute`. The frame-pointer walk
   skipped `compute` entirely. The 4-deep backtrace assertion caught it on the first run
   (Module 06 covers the fix).

None of these could have been found with mocks, because each one is a fact about real
data that a mock's author doesn't know.

## Layer 3: CI That Boots QEMU

Installing QEMU on a CI runner takes one line:

```yaml
- run: sudo apt-get update && sudo apt-get install -y qemu-system-x86
```

gdbstub-mcp's CI runs the unit tests on Linux, Windows and macOS. A separate job installs
QEMU and runs `tests/test_qemu_integration.py`, which covers breakpoints, `file:line`
breakpoints, a watchpoint on `counter`, interrupt, memory writes and a full triple
fault. Each test boots its own QEMU on a free port with `-S`, so the tests never share
state.

This course uses the same idea. `verify.py` boots a fresh QEMU for each module, runs
that module's `main.py`, and fails on any assertion. CI runs it on every push and every
week. Every claim in these READMEs that has a matching `assert` in a `main.py` is
continuously checked against a real CPU.

## Run It

```bash
python ../verify.py          # every module, the way CI does
python main.py --port 1234   # just this one, against your own QEMU
```

## Where to Go Next

You've built the core of [gdbstub-mcp](https://github.com/0xmortuex/gdbstub-mcp). Its
[BACKLOG.md](https://github.com/0xmortuex/gdbstub-mcp/blob/main/BACKLOG.md) lists real
next steps, each one small enough to finish:

- a **page-table walker** that answers "why did this address fault?" by translating
  through CR3
- **`debug_finish` and `debug_next`**, built from a temporary breakpoint at the return
  address or after a `call`
- **local variables** from DWARF location expressions
- **aarch64 fault decoding** (ESR/FAR) for `debug_explain_fault`

Then pair it with [qemu-mcp](https://github.com/0xmortuex/qemu-mcp), which boots the
VM, types into it and takes screenshots. One agent loop then covers the whole osdev
cycle: build, boot, see the crash, debug it, fix it.
