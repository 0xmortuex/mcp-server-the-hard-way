"""Module 08: crash the kernel on purpose, then explain the crash from QEMU's log.

    python main.py --port P --elf E --log L

QEMU must be running kernel/kernel.elf halted (-S) with its gdbstub on P and
`-d int,cpu_reset -D L -no-reboot`. We:

  1. break in kmain, then set the kernel's `trigger` variable to 1, which makes
     it load an empty IDT and execute ud2 (see kernel/kernel.c)
  2. continue, and watch QEMU exit: with -no-reboot a triple fault ends the VM
  3. parse L - QEMU's interrupt log - and turn it into a root-cause explanation
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import time

from elftools.elf.elffile import ELFFile

# ---------------------------------------------------------------------------
# Part 1: just enough RSP to set up the crash (Modules 02 and 04 explain it)
# ---------------------------------------------------------------------------


class Stub:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.buf = b""

    def send(self, payload: str) -> None:
        data = payload.encode()
        self.sock.sendall(b"$" + data + b"#%02x" % (sum(data) % 256))
        while b"+" not in self.buf:          # QEMU acks every packet
            self.buf += self.sock.recv(4096)
        self.buf = self.buf[self.buf.index(b"+") + 1:]

    def recv(self, timeout: float = 10.0) -> str:
        self.sock.settimeout(timeout)
        while True:
            s, e = self.buf.find(b"$"), self.buf.find(b"#", max(self.buf.find(b"$"), 0))
            if s >= 0 and e > s and len(self.buf) >= e + 3:
                payload, self.buf = self.buf[s + 1:e], self.buf[e + 3:]
                self.sock.sendall(b"+")
                return payload.decode()
            self.buf += self.sock.recv(65536)

    def ask(self, payload: str, timeout: float = 10.0) -> str:
        self.send(payload)
        return self.recv(timeout)


# ---------------------------------------------------------------------------
# Part 2: symbols, so the explanation says "triple_fault+0xa", not "0x10009a"
# ---------------------------------------------------------------------------


def load_functions(path: str) -> list[tuple[int, int, str]]:
    with open(path, "rb") as fh:
        symtab = ELFFile(fh).get_section_by_name(".symtab")
        return sorted(
            (s["st_value"], s["st_size"], s.name)
            for s in symtab.iter_symbols()
            if s["st_info"]["type"] in ("STT_FUNC", "STT_OBJECT") and s.name
        )


def symbolize(funcs: list[tuple[int, int, str]], addr: int) -> str:
    for base, size, name in funcs:
        if base <= addr < base + max(size, 1):
            return name if addr == base else f"{name}+{addr - base:#x}"
    return f"{addr:#x}"


def address_of(funcs: list[tuple[int, int, str]], name: str) -> int:
    return next(base for base, _, n in funcs if n == name)


# ---------------------------------------------------------------------------
# Part 3: the log parser - the real subject of this module
# ---------------------------------------------------------------------------

EXCEPTIONS = {
    0x00: "#DE divide error", 0x06: "#UD invalid opcode", 0x08: "#DF double fault",
    0x0A: "#TS invalid TSS", 0x0B: "#NP segment not present", 0x0C: "#SS stack fault",
    0x0D: "#GP general protection fault", 0x0E: "#PF page fault",
}

# "   0: v=06 e=0000 i=0 cpl=0 IP=0008:0010009a pc=0010009a SP=0010:..."
EVENT = re.compile(r"^\s*(\d+): v=([0-9a-f]+) e=([0-9a-f]+) i=(\d)(.*)$")


def parse(log: str) -> tuple[list[dict[str, int]], bool, int]:
    """Return (exceptions, triple_fault_seen, total_interrupt_lines)."""
    events: list[dict[str, int]] = []
    total = 0
    for line in log.splitlines():
        m = EVENT.match(line)
        if not m:
            continue
        total += 1
        vector, software = int(m.group(2), 16), m.group(4) == "1"
        # Vectors >= 0x20 are hardware IRQs (the timer fires ~18x a second);
        # i=1 means a software `int N`. Neither is a fault - skip them.
        if vector >= 0x20 or software:
            continue
        pc = re.search(r"pc=([0-9a-f]+)", m.group(5))
        events.append({"seq": int(m.group(1)), "vector": vector,
                       "error": int(m.group(3), 16), "pc": int(pc.group(1), 16) if pc else -1})
    return events, "Triple fault" in log, total


def decode_error(vector: int, code: int) -> str:
    if vector == 0x0E:
        return ", ".join([
            "protection violation" if code & 1 else "page not present",
            "write" if code & 2 else "read",
            "user" if code & 4 else "kernel",
        ])
    if vector in (0x0A, 0x0B, 0x0C, 0x0D) and code:
        # Selector error code: bit 0 EXT, bit 1 IDT, bit 2 TI (LDT), bits 3+ index.
        if code & 2:
            idx = code >> 3
            return f"IDT entry {idx} ({EXCEPTIONS.get(idx, 'vector ' + hex(idx))})"
        return f"{'LDT' if code & 4 else 'GDT'} entry {code >> 3}"
    return ""


def explain(events: list[dict[str, int]], triple: bool,
            funcs: list[tuple[int, int, str]]) -> str:
    out = []
    for ev in events:
        line = f"[{ev['seq']}] {EXCEPTIONS.get(ev['vector'], hex(ev['vector']))} at {symbolize(funcs, ev['pc'])}"
        detail = decode_error(ev["vector"], ev["error"])
        out.append(line + (f"\n      {detail}" if detail else ""))
    if triple and events:
        root = events[0]
        out.append(f"\nTRIPLE FAULT. Root cause: {EXCEPTIONS[root['vector']]} at "
                   f"{symbolize(funcs, root['pc'])}.")
        # Every #GP/#NP whose error code has the IDT bit names a handler the
        # CPU tried and failed to reach. That's why the fault escalated.
        missing = [ev["error"] >> 3 for ev in events if ev["vector"] in (0x0B, 0x0D) and ev["error"] & 2]
        if missing:
            out.append(f"It escalated because IDT entry {missing[0]} has no usable gate: "
                       "the CPU couldn't run the handler, faulted again, then gave up.")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Part 4: the demo
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--elf", default=os.path.join("..", "kernel", "kernel.elf"))
    ap.add_argument("--log", required=True, help="the file QEMU's -D points at")
    args = ap.parse_args()

    funcs = load_functions(args.elf)
    stub = Stub(args.port)

    # 1. Run to kmain. A hardware breakpoint (Z1), so nothing is patched into memory.
    kmain = address_of(funcs, "kmain")
    assert stub.ask(f"Z1,{kmain:x},1") == "OK"
    stop = stub.ask("c", timeout=20)
    print(f"stopped: {stop}  (at kmain)")
    assert stub.ask(f"z1,{kmain:x},1") == "OK"

    # 2. Pull the trigger: kmain checks `trigger` every loop iteration.
    trigger = address_of(funcs, "trigger")
    assert stub.ask(f"M{trigger:x},4:01000000") == "OK"
    print(f"wrote 1 to trigger @ {trigger:#x}; continuing into the crash...")

    # 3. With -no-reboot, the triple fault ends QEMU; the stub's last words
    #    are W00 ("process exited, status 0").
    final = stub.ask("c", timeout=20)
    print(f"stub says: {final}")
    assert final.startswith("W"), f"expected the VM to exit, got {final!r}"

    # QEMU flushes the log on exit; give the file a moment to be complete.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with open(args.log, encoding="utf-8", errors="replace") as fh:
            log = fh.read()
        if "Triple fault" in log:
            break
        time.sleep(0.1)

    events, triple, total = parse(log)
    print(f"\nlog: {len(log.splitlines())} lines, {total} interrupt entries, "
          f"{len(events)} CPU exceptions\n")
    print(explain(events, triple, funcs))

    # The chain this kernel must produce: #UD -> #GP (IDT 6 missing) -> #DF -> reset.
    assert triple, "QEMU should have logged 'Triple fault'"
    assert [e["vector"] for e in events] == [0x06, 0x0D, 0x08], events
    assert symbolize(funcs, events[0]["pc"]).startswith("triple_fault")
    assert events[1]["error"] >> 3 == 6 and events[1]["error"] & 2
    print("\nmodule 08: all checks passed")


if __name__ == "__main__":
    main()
