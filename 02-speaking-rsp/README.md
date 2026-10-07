# Module 02: Speaking RSP

## The Concept

gdb doesn't need to run on the machine it debugs. It talks to a **stub**, a small piece
of code next to the target, using the GDB Remote Serial Protocol (RSP). The stub could
be inside an embedded board's firmware, a JTAG probe, or a hypervisor. QEMU has one
built in: `-s` opens it on TCP port 1234.

RSP is old. It was designed for serial cables, and it shows. That's good news here:
the protocol is small enough to implement from scratch in about 150 lines, and once you
have, you don't need gdb at all.

```
 your code                                QEMU gdbstub
 ─────────                                ────────────
 $qSupported:...#f0   ──────────────────►
                      ◄──────────────────  +                 (ack: checksum OK)
                      ◄──────────────────  $PacketSize=...#c5
 +                    ──────────────────►                    (ack the reply)
```

## The Packet Format

Every message is a packet:

```
$ payload # cc
│    │    │ └─ checksum: two lowercase hex digits
│    │    └─── end of payload
│    └──────── ASCII command or reply, e.g. "g" or "T05core:01;"
└───────────── start of packet
```

The checksum is the payload bytes summed modulo 256:

```python
def checksum(payload: bytes) -> int:
    return sum(payload) % 256

def frame(payload: bytes) -> bytes:
    return b"$" + payload + b"#%02x" % checksum(payload)
```

- `sum(payload)` adds the byte values. `% 256` keeps the result in one byte.
- `b"#%02x"` formats it as exactly two hex digits, so a checksum of 7 becomes `#07`.
- `g` is byte 0x67, so the packet for "read registers" is `$g#67`. That's checked with an
  `assert` in `main.py` before anything touches the network.

## Acks

On a serial line bytes get corrupted, so every packet is acknowledged: `+` means the
checksum matched, `-` means "send it again". The ack goes in *both* directions: the stub
acks your request, and you ack its reply. Here's the real trace from `main.py`:

```
  -> b'$qSupported:multiprocess+;swbreak+;hwbreak+;xmlRegisters=i386#f0'
  <- b'+'
  <- b'$PacketSize=20020;qXfer:features:read+;vContSupported+;multiprocess+#c5'
  -> b'+'
```

Over TCP, acks are pointless, because TCP already guarantees delivery. Stubs can offer
`QStartNoAckMode+` in their `qSupported` reply to switch them off. Notice that this QEMU
*doesn't* offer it. Your client has to handle both cases, so `main.py` only asks for
no-ack mode when it's offered:

```python
if "QStartNoAckMode+" in features:
    assert rsp.request("QStartNoAckMode") == b"OK"
    rsp.no_ack = True
```

## Reading a Packet

`recv()` gives you whatever bytes have arrived. That might be half a packet, or a packet
and a half. The reader keeps a buffer and only returns once a whole packet is in it:

```python
start = self.buf.find(b"$")
end = self.buf.find(b"#", start) if start >= 0 else -1
if start >= 0 and end >= 0 and len(self.buf) >= end + 3:
    payload = self.buf[start + 1:end]
    sent = int(self.buf[end + 1:end + 3], 16)
    self.buf = self.buf[end + 3:]
```

- Find `$` and the following `#`. Stray `+` bytes before the `$` are skipped naturally.
- `end + 3` means the `#` plus two checksum digits have arrived.
- Leftover bytes stay in `self.buf` for the next call. A real stub *can* send two packets
  back to back.

## Two Encodings Inside the Payload

Reply payloads can use two transformations, and `unescape()` undoes both:

```
}X      escape:      the real byte is X ^ 0x20        "}\x03" → "#"
X*N     run-length:  X repeated (ord(N) - 29) more    "0* "   → "0000"
```

- Escaping lets binary data contain `$`, `#`, `}` and `*` without breaking the framing.
- Run-length encoding shrinks long runs. QEMU uses it in register dumps that are mostly
  zeros. The `- 29` offset keeps the count byte printable: a space (32) means three more
  copies.

## Errors vs. "I Don't Know That"

There are two kinds of non-answer, and confusing them causes real bugs:

```
E14        an error: the command was understood but failed (e.g. bad address)
(empty)    unsupported: the stub doesn't implement this packet at all
```

`request()` raises on `Exx`, but returns an empty reply as-is. Module 04 relies on this
difference to tell "this stub has no hardware watchpoints" apart from "that watchpoint
address is invalid".

## The First Real Answers

With framing working, three packets reveal a lot:

```
qSupported → PacketSize=20020;qXfer:features:read+;vContSupported+;multiprocess+
?          → T05core:01;
g          → 344 bytes: 00000000 00000000 63060000 ... f0ff0000 ...
```

- **`qSupported`** lists features. `qXfer:features:read+` means the stub will hand over a
  description of its registers. Module 03 is built on it.
- **`?`** asks why the target stopped. `T05` is signal 5, SIGTRAP: halted by the debugger.
  `-S` froze the CPU before it ran anything. `core:01` names the CPU.
- **`g`** returns every general register as one hex blob. The ninth 32-bit word, bytes
  32–36 little-endian, is `0xfff0`. That's EIP at the x86 reset vector: the CPU starts at
  `F000:FFF0`, the top of the BIOS. Look one word back, at bytes 8–12: `0x663` is EDX,
  where the CPU leaves its family/model signature at reset.

That last step only worked because the byte offset 32 was hard-coded for i386. On
x86-64, or with a different QEMU, the layout differs, and the code would silently read
the wrong register. Module 03 removes that guess.

## Run It

```bash
qemu-system-i386 -kernel kernel/kernel.elf -s -S -display none
python 02-speaking-rsp/main.py --port 1234
```

→ [Next: Module 03 — Registers From XML](../03-registers-from-xml/README.md)
