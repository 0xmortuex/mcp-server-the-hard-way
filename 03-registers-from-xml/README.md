# Module 03: Registers From XML

## The Concept

Module 02 found EIP by reading "bytes 32 to 36 of the `g` reply". That only worked
because the offset was hard-coded for one architecture. On x86-64 the same bytes hold
half of RSI. On AArch64 they're part of X4. A debugger that guesses register offsets is
correct by coincidence.

The fix is to ask the stub. A modern stub describes its own registers in an XML
document called `target.xml`, which it sends when asked with the `qXfer` packet. Module
02's `qSupported` reply advertised this as `qXfer:features:read+`.

```
 qXfer:features:read:target.xml   ──► <architecture>i386</architecture>
                                       <xi:include href="i386-32bit.xml"/>
 qXfer:features:read:i386-32bit.xml ─► <reg name="eax" bitsize="32" regnum="0"/>
                                       <reg name="ecx" .../> ... 50 registers
```

## Reading in Chunks

A packet has a maximum size (`PacketSize=20020` for QEMU), and an XML file can be bigger
than that. `qXfer` reads are therefore ranged, `<annex>:<offset>,<length>`, and every
reply is prefixed with a one-letter status:

```
qXfer:features:read:i386-32bit.xml:0,400     → m<1024 bytes>    more to come
qXfer:features:read:i386-32bit.xml:400,400   → m<1024 bytes>
...
qXfer:features:read:i386-32bit.xml:1c00,400  → l<204 bytes>     last piece
```

```python
reply = rsp.request(f"qXfer:features:read:{annex}:{len(out):x},{chunk:x}")
kind, data = reply[:1], reply[1:]
out.extend(data)
if kind == b"l":
    return out.decode()
```

- The offset is `len(out)`: however much has been read so far, in hex.
- `m` means keep going. `l` means done. Anything else is a protocol error, and the code
  asserts that.
- `main.py` deliberately uses a small chunk (0x400) so the loop runs. Against QEMU:
  `read i386-32bit.xml: 7372 bytes in 8 chunk(s)`.

## Includes

QEMU's `target.xml` is only 147 bytes:

```xml
<target><architecture>i386</architecture><xi:include href="i386-32bit.xml"/></target>
```

The registers live in the included file, which can include more files in turn. The
expansion fetches each `href` from the stub and splices it in, recursively:

```python
def fetch(m):
    inner = read_xfer(rsp, m.group(1))
    inner = re.sub(r"<\?xml[^>]*\?>", "", inner)
    return expand_includes(rsp, inner)
return INCLUDE.sub(fetch, xml)
```

- Every included file starts with its own `<?xml ...?>` prolog. A second prolog in the
  middle of a document is invalid XML, so it's stripped before splicing.
- The recursive call handles nested includes.

## Numbering Registers

Registers don't carry their offset; it's derived. The rules:

```
<reg name="eax" bitsize="32" regnum="0"/>   → 0   (explicit)
<reg name="ecx" bitsize="32"/>               → 1   (previous + 1)
<reg name="edx" bitsize="32"/>               → 2
...
<reg name="cr0" bitsize="64" regnum="40"/>  → 40  (explicit: counter resets)
<reg name="cr2" bitsize="64"/>               → 41
```

```python
if reg.get("regnum") is not None:
    num = int(reg.get("regnum", 0))
...
num += 1
```

The `g` reply is the registers concatenated in regnum order, each `bitsize / 8` bytes,
little-endian on x86. Splitting it is a walk through the sorted list:

```python
for r in sorted(regs, key=lambda r: r.regnum):
    n = r.bitsize // 8
    if offset + n > len(blob):
        break
    out[r.name] = int.from_bytes(blob[offset:offset + n], "little")
    offset += n
```

The `break` matters. A stub's `g` reply may cover only the "general" set, so registers
past the end of the blob must be read one at a time with `p<regnum>`. QEMU's i386 stub
happens to return all 50 registers in 344 bytes, including the 80-bit x87 registers, so
`p` isn't needed here. Other stubs, and QEMU on other architectures, stop earlier.
`read_reg()` handles both cases.

## Flags

Some register types aren't plain integers. The XML defines bit fields:

```xml
<flags id="i386_cr0" size="4">
  <field name="PG" start="31" end="31"/> ... <field name="PE" start="0" end="0"/>
</flags>
<reg name="cr0" bitsize="32" type="i386_cr0"/>
```

`describe_flags` turns a value into the names of its set bits. A multi-bit field like
IOPL becomes `IOPL=3`. The flag types are collected across the *whole* document first,
because a register can use a type defined in another `<feature>`. The gdbstub-mcp test
suite caught exactly this bug.

## What It Shows

```
architecture 'i386', 50 registers
at reset:  eip    = 0xfff0
           cr0    = 0x60000010  [CD NW ET]  <- PE clear: real mode
ran for 1s, interrupted: T02thread:01;
in kernel: eip    = 0x1000b2
           cr0    = 0x00000011  [ET PE]  <- PE set: protected mode
```

At reset, CR0 is `CD NW ET`: caches disabled, and **PE clear**, so the CPU is in 16-bit
real mode. After running for a second, the bootloader has switched to protected mode, so
PE is set. EIP is now `0x1000b2`, inside the kernel linked at 1 MiB. Without the XML,
`0x60000010` is just a number. With it, the agent reads "real mode", which is the kind of
fact that explains why a breakpoint at a 32-bit address hasn't been hit yet.

The run-and-interrupt step uses `c` and the `0x03` byte without explaining them.
Execution control turns out to be the hardest part of the protocol, and it's the subject
of the next module.

## Run It

```bash
qemu-system-i386 -kernel kernel/kernel.elf -s -S -display none
python 03-registers-from-xml/main.py --port 1234
```

→ [Next: Module 04 — Memory, Breakpoints and Time](../04-memory-breakpoints-and-time/README.md)
