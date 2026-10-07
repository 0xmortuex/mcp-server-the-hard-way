"""Module 09: test an RSP client against a fake stub on a REAL socket, then
against real QEMU.

    python main.py --port P --elf E --log L

Part A needs nothing: it starts a fake gdbstub on a background thread and
checks the client's ack/nack/resend handling - including a reply whose
checksum is deliberately wrong. Part B talks to the real QEMU on port P to
prove the same client works against the genuine article.
"""

from __future__ import annotations

import argparse
import socket
import threading
import time


def frame(payload: bytes) -> bytes:
    return b"$" + payload + b"#%02x" % (sum(payload) % 256)


# ---------------------------------------------------------------------------
# The client under test: ack mode with checksum verification
# ---------------------------------------------------------------------------


class Client:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buf = b""
        self.nacks_sent = 0

    def _read_ack(self) -> None:
        while not self.buf:
            self.buf += self.sock.recv(4096)
        ch, self.buf = self.buf[:1], self.buf[1:]
        assert ch == b"+", f"expected ack, got {ch!r}"

    def _read_packet(self) -> bytes:
        while True:
            s = self.buf.find(b"$")
            e = self.buf.find(b"#", s) if s >= 0 else -1
            if s >= 0 and e >= 0 and len(self.buf) >= e + 3:
                payload, sent = self.buf[s + 1:e], self.buf[e + 1:e + 3]
                self.buf = self.buf[e + 3:]
                if int(sent, 16) != sum(payload) % 256:
                    # Corrupted in transit: say '-' and the stub resends.
                    self.sock.sendall(b"-")
                    self.nacks_sent += 1
                    continue
                self.sock.sendall(b"+")
                return payload
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("stub closed the connection")
            self.buf += chunk

    def ask(self, payload: bytes) -> bytes:
        self.sock.sendall(frame(payload))
        self._read_ack()
        return self._read_packet()

    def close(self) -> None:
        self.sock.close()


# ---------------------------------------------------------------------------
# The fake stub: real TCP, real framing, scripted misbehaviour
# ---------------------------------------------------------------------------


class FakeStub:
    """Answers a few packets the way QEMU does, on a real localhost socket.

    Why not mock socket.recv? Because the bugs live in the bytes: a reply
    split across two recv() calls, a '+' arriving glued to a packet, a nack
    that must trigger a resend. A mock returns whatever you assumed; a socket
    returns what TCP actually delivers.
    """

    def __init__(self, corrupt_first_reply: bool):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.corrupt = corrupt_first_reply
        self.resends = 0
        self.seen: list[bytes] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _reply(self, conn: socket.socket, payload: bytes) -> None:
        good = frame(payload)
        if self.corrupt:
            self.corrupt = False
            conn.sendall(good[:-2] + b"00")      # wrong checksum
            # Wait for the client's verdict, as a real stub does.
            verdict = conn.recv(1)
            assert verdict == b"-", f"client accepted a corrupt packet ({verdict!r})"
            self.resends += 1
        # Split the good packet across two sends: the client must reassemble.
        conn.sendall(good[:3])
        time.sleep(0.01)
        conn.sendall(good[3:])

    def _serve(self) -> None:
        conn, _ = self.srv.accept()
        buf = b""
        while True:
            data = conn.recv(4096)
            if not data:
                return
            buf += data
            while True:
                s, e = buf.find(b"$"), buf.find(b"#")
                if s < 0 or e < 0 or len(buf) < e + 3:
                    break
                payload, buf = buf[s + 1:e], buf[e + 3:]
                self.seen.append(payload)
                conn.sendall(b"+")               # ack their packet
                if payload == b"?":
                    self._reply(conn, b"T05thread:01;")
                elif payload == b"g":
                    self._reply(conn, b"2a000000" + b"0" * 64)
                else:
                    self._reply(conn, b"")       # "unsupported" is an empty reply
            # Swallow the client's acks for our packets.
            buf = buf.lstrip(b"+")


def part_a() -> None:
    print("A. fake stub, clean line")
    stub = FakeStub(corrupt_first_reply=False)
    c = Client(stub.port)
    assert c.ask(b"?") == b"T05thread:01;"
    assert c.ask(b"g").startswith(b"2a000000")
    assert c.ask(b"vMustReplyEmpty") == b""
    assert c.nacks_sent == 0 and stub.seen == [b"?", b"g", b"vMustReplyEmpty"]
    c.close()
    print("   ok: framing, acks, split packets, empty replies")

    print("A. fake stub, corrupted checksum on the first reply")
    stub = FakeStub(corrupt_first_reply=True)
    c = Client(stub.port)
    assert c.ask(b"?") == b"T05thread:01;"
    assert c.nacks_sent == 1 and stub.resends == 1
    c.close()
    print("   ok: client sent '-' and accepted the resend")


def part_b(port: int) -> None:
    print(f"B. real QEMU gdbstub on :{port}")
    c = Client(port)
    sup = c.ask(b"qSupported:xmlRegisters=i386").decode()
    print(f"   qSupported -> {sup}")
    assert "qXfer:features:read+" in sup
    # Worth asserting: QEMU does NOT offer QStartNoAckMode, so a client that
    # only implements no-ack mode cannot talk to it at all.
    assert "QStartNoAckMode+" not in sup
    stop = c.ask(b"?").decode()
    print(f"   ?          -> {stop}")
    assert stop.startswith("T05")
    c.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--elf", default=None, help="unused; accepted for verify.py")
    ap.add_argument("--log", default=None, help="unused; accepted for verify.py")
    args = ap.parse_args()
    part_a()
    part_b(args.port)
    print("\nmodule 09: all checks passed")


if __name__ == "__main__":
    main()
