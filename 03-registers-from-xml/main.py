"""Module 03: discover the register layout from the stub's target.xml.

Module 02 found EIP by hard-coding "bytes 32..36". This module asks the stub
for its own description of the registers, so the same code works on i386,
x86-64, ARM or RISC-V without a lookup table.

Steps:
  1. read target.xml with chunked qXfer reads
  2. expand its <xi:include> references
  3. number the registers and split the 'g' blob with that layout
  4. read registers that 'g' doesn't cover with 'p'
  5. decode flag registers (eflags, cr0) into bit names
  6. run the kernel briefly and watch CR0 change from real to protected mode

Usage: python main.py --port P [--elf E --log L]   (elf/log unused here)
"""

from __future__ import annotations

import argparse
import re
import socket
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


# --- the RSP client from Module 02, condensed -----------------------------------

def frame(payload: bytes) -> bytes:
    return b"$" + payload + b"#%02x" % (sum(payload) % 256)


def unescape(data: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(data):
        if data[i] == 0x7D:                              # '}' escape
            i += 1
            out.append(data[i] ^ 0x20)
        elif data[i] == 0x2A:                            # '*' run-length
            i += 1
            out.extend(out[-1:] * (data[i] - 29))
        else:
            out.append(data[i])
        i += 1
    return bytes(out)


class RSP:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buf = b""

    def _fill(self) -> None:
        chunk = self.sock.recv(65536)
        if not chunk:
            raise RuntimeError("stub closed the connection")
        self.buf += chunk

    def read_packet(self) -> bytes:
        while True:
            s = self.buf.find(b"$")
            e = self.buf.find(b"#", s) if s >= 0 else -1
            if s >= 0 and e >= 0 and len(self.buf) >= e + 3:
                payload, self.buf = self.buf[s + 1:e], self.buf[e + 3:]
                self.sock.sendall(b"+")                  # ack mode (QEMU didn't offer no-ack)
                return unescape(payload)
            self._fill()

    def send(self, payload: str) -> None:
        self.sock.sendall(frame(payload.encode()))
        while True:                                      # wait for the '+' ack
            while not self.buf:
                self._fill()
            ch, self.buf = self.buf[:1], self.buf[1:]
            if ch == b"+":
                return

    def request(self, payload: str) -> bytes:
        self.send(payload)
        reply = self.read_packet()
        if len(reply) == 3 and reply.startswith(b"E"):
            raise RuntimeError(f"{payload!r} -> {reply.decode()}")
        return reply


# --- 1. chunked qXfer reads ---------------------------------------------------

def read_xfer(rsp: RSP, annex: str, chunk: int = 0x400) -> str:
    """Read a whole 'features' object in pieces.

    Each request is qXfer:features:read:<annex>:<offset>,<length>. The reply
    starts with 'm' (more data follows - ask again from the new offset) or
    'l' (last piece). A small chunk size is used here on purpose, so the
    loop runs several times and you can see it work.
    """
    out = bytearray()
    pieces = 0
    while True:
        reply = rsp.request(f"qXfer:features:read:{annex}:{len(out):x},{chunk:x}")
        kind, data = reply[:1], reply[1:]
        assert kind in (b"m", b"l"), f"unexpected qXfer reply {reply[:20]!r}"
        out.extend(data)
        pieces += 1
        if kind == b"l":
            print(f"  read {annex}: {len(out)} bytes in {pieces} chunk(s)")
            return out.decode()


# --- 2. include expansion -------------------------------------------------------

INCLUDE = re.compile(r'<xi:include\s+href="([^"]+)"\s*/>')


def expand_includes(rsp: RSP, xml: str) -> str:
    """Replace each <xi:include href="x"/> with the contents of x.

    QEMU's top-level target.xml is tiny - just an <architecture> and an
    include of i386-32bit.xml, which holds the actual register list.
    """
    def fetch(m: re.Match[str]) -> str:
        inner = read_xfer(rsp, m.group(1))
        inner = re.sub(r"<\?xml[^>]*\?>", "", inner)     # drop nested prolog
        inner = re.sub(r"<!DOCTYPE[^>]*>", "", inner)    # and doctype
        return expand_includes(rsp, inner)               # includes may nest
    return INCLUDE.sub(fetch, xml)


# --- 3. the layout --------------------------------------------------------------

@dataclass
class Register:
    name: str
    regnum: int
    bitsize: int
    type: str
    flags: list[tuple[str, int, int]] = field(default_factory=list)


def parse_layout(xml: str) -> tuple[str, list[Register]]:
    xml = re.sub(r"<!DOCTYPE[^>]*>", "", xml)
    root = ET.fromstring(xml)
    arch = (root.findtext("architecture") or "").strip()
    # <flags id="i386_eflags"> defines bit names; a <reg type="i386_eflags">
    # uses them. Collect every flags type in the document first.
    flag_types = {
        fl.get("id", ""): [(f.get("name", ""), int(f.get("start", 0)), int(f.get("end", 0)))
                           for f in fl.iter("field") if f.get("name")]
        for fl in root.iter("flags")
    }
    regs: list[Register] = []
    num = 0
    for reg in root.iter("reg"):
        # Registers are numbered in document order, starting at 0. A regnum
        # attribute resets the counter - later registers continue from it.
        if reg.get("regnum") is not None:
            num = int(reg.get("regnum", 0))
        rtype = reg.get("type", "int")
        regs.append(Register(reg.get("name", ""), num, int(reg.get("bitsize", 0)),
                             rtype, flag_types.get(rtype, [])))
        num += 1
    return arch, regs


def split_g(regs: list[Register], blob: bytes) -> dict[str, int]:
    """Cut the 'g' blob into registers, in regnum order, until it runs out."""
    out: dict[str, int] = {}
    offset = 0
    for r in sorted(regs, key=lambda r: r.regnum):
        n = r.bitsize // 8
        if offset + n > len(blob):
            break                              # the rest needs 'p'
        out[r.name] = int.from_bytes(blob[offset:offset + n], "little")
        offset += n
    return out


def describe_flags(reg: Register, value: int) -> str:
    names = []
    for name, start, end in reg.flags:
        width = end - start + 1
        bits = (value >> start) & ((1 << width) - 1)
        if bits:
            names.append(name if width == 1 else f"{name}={bits}")
    return " ".join(names)


def read_reg(rsp: RSP, regs: list[Register], name: str) -> int:
    """Read one register: from 'g' if it's in there, else with 'p<regnum>'."""
    reg = next(r for r in regs if r.name == name)
    g = split_g(regs, bytes.fromhex(rsp.request("g").decode()))
    if name in g:
        return g[name]
    return int.from_bytes(bytes.fromhex(rsp.request(f"p{reg.regnum:x}").decode()), "little")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--elf")
    ap.add_argument("--log")
    args = ap.parse_args()

    rsp = RSP(args.port)
    rsp.request("qSupported:xmlRegisters=i386")

    print("fetching the target description:")
    top = read_xfer(rsp, "target.xml")
    print(f"  target.xml is just: {top[top.find('<target>'):]}")
    arch, regs = parse_layout(expand_includes(rsp, top))
    print(f"architecture {arch!r}, {len(regs)} registers")
    assert arch == "i386"
    assert any(r.name == "eip" for r in regs)

    blob = bytes.fromhex(rsp.request("g").decode())
    g = split_g(regs, blob)
    print(f"'g' returned {len(blob)} bytes = the first {len(g)} registers; "
          f"the other {len(regs) - len(g)} need 'p'")

    print("\n  regnum  name       bits  value")
    for r in regs[:10]:
        print(f"  {r.regnum:>6}  {r.name:<9} {r.bitsize:>5}  {g[r.name]:#010x}")

    eflags = next(r for r in regs if r.name == "eflags")
    cr0 = next(r for r in regs if r.name == "cr0")
    print(f"\nat reset:  eip    = {g['eip']:#x}")
    print(f"           eflags = {g['eflags']:#010x}  [{describe_flags(eflags, g['eflags'])}]")
    cr0_reset = read_reg(rsp, regs, "cr0")
    print(f"           cr0    = {cr0_reset:#010x}  [{describe_flags(cr0, cr0_reset)}]"
          f"  <- PE clear: real mode")
    assert g["eip"] == 0xFFF0
    assert "PE" not in describe_flags(cr0, cr0_reset).split()

    # Let the kernel run for a moment, then halt it. 'c' (continue) has no
    # reply until the target stops; the out-of-band byte 0x03 forces a stop.
    # Module 04 explains this properly - here it just gets the CPU into the
    # kernel so CR0 has something to show.
    rsp.send("c")
    time.sleep(1.0)
    rsp.sock.sendall(b"\x03")
    stop = rsp.read_packet()
    print(f"\nran for 1s, interrupted: {stop.decode()}")

    cr0_now = read_reg(rsp, regs, "cr0")
    eip_now = read_reg(rsp, regs, "eip")
    print(f"in kernel: eip    = {eip_now:#x}")
    print(f"           cr0    = {cr0_now:#010x}  [{describe_flags(cr0, cr0_now)}]"
          f"  <- PE set: protected mode")
    assert "PE" in describe_flags(cr0, cr0_now).split()
    assert 0x100000 <= eip_now < 0x200000, "the kernel is linked at 1 MiB"

    rsp.sock.close()
    print("OK")


if __name__ == "__main__":
    main()
