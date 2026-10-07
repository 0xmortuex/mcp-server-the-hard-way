"""Module 04: memory, breakpoints, watchpoints - and time.

Run against a halted QEMU (see the course README):

    python main.py --port 1234 --elf ../kernel/kernel.elf --log int.log

What it does, in order:
  1. reads memory in chunks and checks it against the ELF's own bytes
  2. writes memory and reads it back
  3. sets a HARDWARE breakpoint on add(), continues, and lands on it
  4. sets a WRITE watchpoint on `counter` and catches the write
  5. continues with no breakpoints, waits 1s (still running), then interrupts

Every step asserts what it expects, so `python verify.py 04` fails if any of
this stops being true.

The RSP client below is a compact copy of Module 02's, extended with the
running/halted state this module is about. Each module carries its own copy
on purpose so you can read (and run) any module on its own.
"""

from __future__ import annotations

import argparse
import socket
import time

from elftools.elf.elffile import ELFFile


# --------------------------------------------------------------------------
# Compact RSP client (framing from Module 02, plus execution control)
# --------------------------------------------------------------------------

def frame(payload: bytes) -> bytes:
    # $<payload>#<two hex digits: sum of payload bytes mod 256>
    return b"$" + payload + b"#%02x" % (sum(payload) % 256)


def unescape(data: bytes) -> bytes:
    # Undo '}' escaping and '*' run-length encoding (see Module 02).
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x7D:                       # '}': next byte is XORed with 0x20
            i += 1
            out.append(data[i] ^ 0x20)
        elif b == 0x2A:                     # '*': repeat previous byte (n - 29) more times
            i += 1
            out.extend(out[-1:] * (data[i] - 29))
        else:
            out.append(b)
        i += 1
    return bytes(out)


class RSP:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.buf = b""
        self.no_ack = False
        # The one piece of state that makes execution control hard: while the
        # CPU runs, the stub will answer NOTHING until it stops. We track it.
        self.running = False
        feats = self.request("qSupported:swbreak+;hwbreak+")
        if b"QStartNoAckMode+" in feats and self.request("QStartNoAckMode") == b"OK":
            self.no_ack = True

    def _read_packet(self, timeout: float) -> bytes:
        """One packet's payload, or TimeoutError if none arrives in time."""
        deadline = time.monotonic() + timeout
        while True:
            start = self.buf.find(b"$")                 # skip stray '+' acks
            end = self.buf.find(b"#", start)
            if start >= 0 and end >= 0 and len(self.buf) >= end + 3:
                payload = self.buf[start + 1:end]
                self.buf = self.buf[end + 3:]
                if not self.no_ack:
                    self.sock.sendall(b"+")             # acknowledge it
                return unescape(payload)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                raise TimeoutError from None
            if not chunk:
                raise ConnectionError("stub closed the connection")
            self.buf += chunk

    def request(self, packet: str) -> bytes:
        # Rule #1 of execution control: a running target gets no packets.
        # gdb enforces the same rule; QEMU would simply never answer.
        if self.running:
            raise RuntimeError("target is running - interrupt it or wait for it to stop")
        self.sock.sendall(frame(packet.encode()))
        reply = self._read_packet(10.0)
        if len(reply) == 3 and reply.startswith(b"E"):
            raise RuntimeError(f"stub error {reply.decode()} for {packet[:30]!r}")
        return reply

    # -- memory ------------------------------------------------------------

    def read_memory(self, addr: int, length: int, chunk: int = 0x800) -> bytes:
        # 'm addr,len' returns hex. Replies are capped by the stub's
        # PacketSize, so big reads are split into chunks.
        out = bytearray()
        while len(out) < length:
            n = min(chunk, length - len(out))
            out += bytes.fromhex(self.request(f"m{addr + len(out):x},{n:x}").decode())
        return bytes(out)

    def write_memory(self, addr: int, data: bytes) -> None:
        assert self.request(f"M{addr:x},{len(data):x}:{data.hex()}") == b"OK"

    def eip(self) -> int:
        # 'g' returns all general registers; on i386 eip is the 9th 32-bit
        # register (eax ecx edx ebx esp ebp esi edi EIP). Module 03 derives
        # this from target.xml instead of hard-coding it.
        regs = bytes.fromhex(self.request("g").decode())
        return int.from_bytes(regs[32:36], "little")

    # -- breakpoints -------------------------------------------------------

    def z(self, insert: bool, kind: int, addr: int, length: int) -> None:
        # Z0 software bp, Z1 hardware bp, Z2 write / Z3 read / Z4 access watch.
        reply = self.request(f"{'Z' if insert else 'z'}{kind},{addr:x},{length:x}")
        if reply == b"":
            raise RuntimeError(f"stub doesn't support Z{kind}")
        assert reply == b"OK", reply

    # -- time --------------------------------------------------------------

    def resume(self) -> None:
        # 'c' has no immediate reply: the stop reply comes when the CPU stops.
        # So we send it and return at once, remembering that we're running.
        self.sock.sendall(frame(b"c"))
        self.running = True

    def wait_stop(self, timeout: float) -> bytes | None:
        # None means "still running" - a normal answer, not an error.
        try:
            reply = self._read_packet(timeout)
        except TimeoutError:
            return None
        self.running = False
        return reply

    def interrupt(self) -> bytes:
        # 0x03 is sent raw, outside any packet - like Ctrl-C on a serial line.
        self.sock.sendall(b"\x03")
        reply = self.wait_stop(5.0)
        assert reply is not None, "target ignored the interrupt"
        return reply


def symbol(elf_path: str, name: str) -> int:
    with open(elf_path, "rb") as fh:
        for sym in ELFFile(fh).get_section_by_name(".symtab").iter_symbols():
            if sym.name == name:
                return int(sym["st_value"])
    raise KeyError(name)


def text_bytes(elf_path: str, addr: int, length: int) -> bytes:
    with open(elf_path, "rb") as fh:
        sec = ELFFile(fh).get_section_by_name(".text")
        return sec.data()[addr - sec["sh_addr"]:addr - sec["sh_addr"] + length]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--log")  # unused here; accepted so every module runs the same way
    args = ap.parse_args()

    add = symbol(args.elf, "add")
    counter = symbol(args.elf, "counter")
    rsp = RSP(args.port)
    print(f"connected; add() at {add:#x}, counter at {counter:#x}")

    # 1. Chunked read: 0x1000 bytes = two 0x800-byte 'm' packets. QEMU has
    #    loaded the kernel, so memory must equal the ELF's .text bytes.
    mem = rsp.read_memory(add, 0x40)
    assert mem == text_bytes(args.elf, add, 0x40), "memory differs from the ELF"
    big = rsp.read_memory(0x100000, 0x1000)
    assert len(big) == 0x1000
    print(f"[1] read add()'s code from memory, matches the ELF: {mem[:8].hex(' ')} ...")

    # 2. Write and read back (counter is in .bss, zero until kmain runs).
    rsp.write_memory(counter, (0x12345678).to_bytes(4, "little"))
    assert rsp.read_memory(counter, 4) == bytes.fromhex("78563412")
    rsp.write_memory(counter, bytes(4))
    print("[2] wrote 0x12345678 to counter and read it back")

    # 3. Hardware breakpoint. A software breakpoint patches an int3 into
    #    memory; a hardware one uses the CPU's debug registers (DR0-DR3), so
    #    it works on code that isn't loaded yet or whose page isn't mapped.
    rsp.z(True, 1, add, 1)
    rsp.resume()
    stop = rsp.wait_stop(20)
    assert stop is not None and stop.startswith(b"T05"), stop
    assert rsp.eip() == add
    rsp.z(False, 1, add, 1)
    print(f"[3] hw breakpoint hit: {stop.decode()}  eip={rsp.eip():#x}")

    # 4. Write watchpoint on counter (4 bytes). The CPU stops just AFTER the
    #    instruction that wrote it, and the stop reply names the address.
    rsp.z(True, 2, counter, 4)
    rsp.resume()
    stop = rsp.wait_stop(20)
    assert stop is not None, "watchpoint never fired"
    fields = dict(f.split(":", 1) for f in stop[3:].decode().split(";") if ":" in f)
    assert int(fields["watch"], 16) == counter, stop
    rsp.z(False, 2, counter, 4)
    print(f"[4] watchpoint hit: {stop.decode()}  (wrote counter, eip={rsp.eip():#x})")

    # 5. Time. No breakpoints left: kmain loops forever, so 'c' never stops.
    rsp.resume()
    assert rsp.wait_stop(1.0) is None
    try:
        rsp.request("g")
        raise AssertionError("request while running should be refused")
    except RuntimeError as e:
        print(f"[5] after 1s: still running; asking for registers -> {e}")
    stop = rsp.interrupt()
    assert stop.startswith(b"T02"), stop          # signal 2 = SIGINT
    print(f"    interrupted: {stop.decode()}  eip={rsp.eip():#x}")

    print("\nall checks passed")


if __name__ == "__main__":
    main()
