"""Module 02: the GDB Remote Serial Protocol, from raw bytes.

Connects to QEMU's gdbstub, prints every byte that crosses the wire for the
first exchange, then asks three questions:

    qSupported   what can you do?
    ?            why are you stopped?
    g            what's in the general registers?

Usage: python main.py --port P [--elf E --log L]   (elf/log unused here)
"""

from __future__ import annotations

import argparse
import socket
import time


class RSPError(RuntimeError):
    """Anything that goes wrong on the wire: errors, timeouts, bad packets."""


# --- framing: the pure functions ---------------------------------------------

def checksum(payload: bytes) -> int:
    # The checksum is the sum of the payload bytes, modulo 256. That's all.
    # It catches a flipped byte on a serial line; it isn't cryptography.
    return sum(payload) % 256


def frame(payload: bytes) -> bytes:
    # A packet is: '$' + payload + '#' + checksum as two lowercase hex digits.
    # b"%02x" % n formats an int as exactly two hex digits (0x7 -> b"07").
    return b"$" + payload + b"#%02x" % checksum(payload)


def unescape(data: bytes) -> bytes:
    """Decode the two transformations a stub may apply to a reply payload.

    1. Escaping: '}' (0x7d) means "the next byte is XOR 0x20". It lets binary
       data contain '$', '#', '}' and '*' without breaking the framing.
    2. Run-length encoding: 'X*N' means "X, then (ord(N) - 29) more copies
       of X". QEMU uses it for long runs of zeros in register dumps.
    """
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x7D:                       # '}' escape
            i += 1
            if i >= len(data):
                raise RSPError("truncated escape sequence")
            out.append(data[i] ^ 0x20)      # undo the XOR
        elif b == 0x2A:                     # '*' run-length marker
            i += 1
            if i >= len(data) or not out:
                raise RSPError("malformed run-length encoding")
            # Repeat the previous output byte (count - 29) more times. The
            # odd offset keeps the count byte printable: ' ' (32) means 3.
            out.extend(out[-1:] * (data[i] - 29))
        else:
            out.append(b)                   # ordinary byte
        i += 1
    return bytes(out)


# --- the connection ------------------------------------------------------------

class RSP:
    def __init__(self, port: int, trace: bool = False):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buf = b""          # bytes received but not yet consumed
        self.no_ack = False     # until negotiated, every packet gets a +/-
        self.trace = trace      # print raw wire traffic

    def _send_raw(self, data: bytes) -> None:
        if self.trace:
            print(f"  -> {data!r}")
        self.sock.sendall(data)

    def _recv_more(self) -> None:
        chunk = self.sock.recv(65536)
        if not chunk:
            raise RSPError("stub closed the connection")
        if self.trace:
            print(f"  <- {chunk!r}")
        self.buf += chunk

    def _read_ack(self) -> bytes:
        # After we send a packet the stub replies '+' (got it) or '-' (bad
        # checksum, send again). Anything else before it is skipped.
        while True:
            while not self.buf:
                self._recv_more()
            ch, self.buf = self.buf[:1], self.buf[1:]
            if ch in (b"+", b"-"):
                return ch

    def _read_packet(self) -> bytes:
        # Find '$', then '#', then two checksum digits. Data may arrive split
        # across recv() calls, so keep reading until a whole packet is here.
        while True:
            start = self.buf.find(b"$")
            end = self.buf.find(b"#", start) if start >= 0 else -1
            if start >= 0 and end >= 0 and len(self.buf) >= end + 3:
                payload = self.buf[start + 1:end]
                sent = int(self.buf[end + 1:end + 3], 16)
                self.buf = self.buf[end + 3:]
                if not self.no_ack:
                    # Acknowledge: '-' asks the stub to resend.
                    if sent != checksum(payload):
                        self._send_raw(b"-")
                        continue
                    self._send_raw(b"+")
                return unescape(payload)
            self._recv_more()

    def request(self, payload: str) -> bytes:
        """Send one packet, wait for its reply, decode it."""
        packet = frame(payload.encode())
        for _attempt in range(5):
            self._send_raw(packet)
            if self.no_ack or self._read_ack() == b"+":
                break
        else:
            raise RSPError(f"stub rejected {payload!r} five times")
        reply = self._read_packet()
        # Errors are 'E' followed by two hex digits, e.g. E14. (An empty
        # reply is different: it means "I don't support that packet".)
        if len(reply) == 3 and reply.startswith(b"E"):
            raise RSPError(f"stub returned {reply.decode()} for {payload!r}")
        return reply


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--elf")
    ap.add_argument("--log")
    args = ap.parse_args()

    # 1. The framing rules, checked against known values before touching
    #    the network. 'g' is 0x67, so its checksum is 0x67.
    assert checksum(b"g") == 0x67 and frame(b"g") == b"$g#67"
    assert frame(b"") == b"$#00"
    assert unescape(b"}\x03") == b"#"          # 0x03 ^ 0x20 == '#'
    assert unescape(b"0* ") == b"0000"         # ' ' = 32 -> 3 extra copies
    print("framing self-checks passed")

    # 2. A traced exchange, so every byte is visible.
    rsp = RSP(args.port, trace=True)
    # Tell the stub what *we* support; it answers with what it supports.
    print("qSupported:")
    reply = rsp.request("qSupported:multiprocess+;swbreak+;hwbreak+;xmlRegisters=i386")
    rsp.trace = False
    features = reply.decode().split(";")
    print(f"  features: {features}")
    assert any(f.startswith("PacketSize=") for f in features)
    assert "qXfer:features:read+" in features      # Module 03 depends on this

    # 3. No-ack mode drops the +/- after every packet. TCP already guarantees
    #    delivery, so the acks are a serial-line leftover. Only ask for it if
    #    the stub offered it - some QEMU versions don't.
    if "QStartNoAckMode+" in features:
        assert rsp.request("QStartNoAckMode") == b"OK"
        rsp.no_ack = True
    print(f"  no-ack mode: {rsp.no_ack}")

    # 4. '?' - why is the target stopped? -S halted it at reset, which a stub
    #    reports as signal 5 (SIGTRAP): 'T05' plus key:value pairs.
    stop = rsp.request("?")
    print(f"? -> {stop.decode()}")
    assert stop.startswith(b"T05"), stop

    # 5. 'g' - every general register, as one hex string in register order.
    #    Without knowing the layout it's just bytes; Module 03 fixes that.
    regs = bytes.fromhex(rsp.request("g").decode())
    print(f"g -> {len(regs)} bytes: {regs[:40].hex()}...")
    # The 9th 32-bit little-endian value is EIP on i386. At reset it's
    # 0xfff0 - the CPU starts at F000:FFF0, the top of the BIOS.
    eip = int.from_bytes(regs[32:36], "little")
    print(f"   bytes 32..36 as little-endian u32 = {eip:#x}  (eip, at the reset vector)")
    assert eip == 0xFFF0

    # 6. An unsupported packet gets an empty reply, not an error.
    assert rsp.request("qThisDoesNotExist") == b""
    print("unknown packet -> empty reply (unsupported)")

    rsp.sock.close()
    time.sleep(0.1)
    print("OK")


if __name__ == "__main__":
    main()
