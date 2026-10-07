"""Module 05: symbols and source lines.

    python main.py --port 1234 --elf ../kernel/kernel.elf --log int.log

Offline (just the ELF):
  1. load the symbol table - and show the SHN_ABS bug, then fix it
  2. symbolize addresses as func+offset with a binary search
  3. decode the DWARF line program into (address, file, line) rows
  4. show the "data address gets a source line" bug, then fix it
  5. resolve kernel.c:10 to addresses (one line -> several addresses)
Live (QEMU):
  6. break on kernel.c:10, continue, and turn eip back into add+0x9 at kernel.c:10

The RSP client is a compact copy of the one in Module 04 (each module
carries its own so it runs standalone).
"""

from __future__ import annotations

import argparse
import bisect
import socket
import time

from elftools.elf.elffile import ELFFile


# --------------------------------------------------------------------------
# Symbols
# --------------------------------------------------------------------------

def load_symbols(path: str, skip_absolute: bool) -> list[tuple[int, int, str, str]]:
    """(address, size, name, kind) for every named symbol, sorted by address."""
    out = []
    with open(path, "rb") as fh:
        for sym in ELFFile(fh).get_section_by_name(".symtab").iter_symbols():
            stype = sym["st_info"]["type"]
            if not sym.name or stype in ("STT_SECTION", "STT_FILE"):
                continue
            # The fix for the bug in step 1: an SHN_ABS symbol is a CONSTANT
            # (from `.set MAGIC, 0x1BADB002`), not a place in memory.
            if skip_absolute and sym["st_shndx"] == "SHN_ABS":
                continue
            kind = {"STT_FUNC": "func", "STT_OBJECT": "object"}.get(stype, "label")
            out.append((sym["st_value"], sym["st_size"], sym.name, kind))
    return sorted(out)


def symbolize(symbols: list[tuple[int, int, str, str]], addr: int) -> str:
    """'name+0xoff' for the closest symbol at or below addr."""
    starts = [s[0] for s in symbols]
    i = bisect.bisect_right(starts, addr) - 1      # last symbol starting <= addr
    if i >= 0:
        start, size, name, _ = symbols[i]
        off = addr - start
        # Sized symbols (functions, objects) must actually cover addr; size-0
        # labels (like _start in assembly) get the benefit of the doubt.
        if off == 0:
            return name
        if size == 0 or off < size:
            return f"{name}+{off:#x}"
    return f"{addr:#x}"


# --------------------------------------------------------------------------
# DWARF line table
# --------------------------------------------------------------------------

def load_lines(path: str) -> list[tuple[int, str, int]]:
    """Run every CU's line-number program; keep (address, file, line) rows."""
    rows = []
    with open(path, "rb") as fh:
        elf = ELFFile(fh)
        dwarf = elf.get_dwarf_info()
        for cu in dwarf.iter_CUs():
            prog = dwarf.line_program_for_CU(cu)
            version = prog.header["version"]
            files = prog.header["file_entry"]
            for entry in prog.get_entries():
                st = entry.state             # None for non-row opcodes
                if st is None or st.end_sequence:
                    continue                 # end_sequence marks one-past-the-end
                # DWARF 5 numbers files from 0, DWARF 2-4 from 1.
                idx = st.file if version >= 5 else st.file - 1
                rows.append((st.address, files[idx].name.decode(), st.line))
    return sorted(rows)


def code_ranges(path: str) -> list[tuple[int, int]]:
    with open(path, "rb") as fh:
        return [(s["sh_addr"], s["sh_addr"] + s["sh_size"])
                for s in ELFFile(fh).iter_sections()
                if s["sh_flags"] & 0x4]      # SHF_EXECINSTR


def line_for(rows, addr: int, ranges=None):
    # The fix for the bug in step 4: only code has source lines.
    if ranges is not None and not any(lo <= addr < hi for lo, hi in ranges):
        return None
    i = bisect.bisect_right([r[0] for r in rows], addr) - 1
    return rows[i] if i >= 0 else None


def addresses_for_line(rows, file: str, line: int) -> list[int]:
    return sorted({a for a, f, ln in rows if ln == line and f.endswith(file)})


# --------------------------------------------------------------------------
# Compact RSP client (see Module 04 for the commented version)
# --------------------------------------------------------------------------

class RSP:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.buf = b""
        self.no_ack = False
        if b"QStartNoAckMode+" in self.request("qSupported:swbreak+;hwbreak+"):
            self.no_ack = self.request("QStartNoAckMode") == b"OK"

    def _read(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            s, e = self.buf.find(b"$"), self.buf.find(b"#", max(self.buf.find(b"$"), 0))
            if s >= 0 and e >= 0 and len(self.buf) >= e + 3:
                payload, self.buf = self.buf[s + 1:e], self.buf[e + 3:]
                if not self.no_ack:
                    self.sock.sendall(b"+")
                return payload           # no RLE in the replies we use here
            self.sock.settimeout(max(deadline - time.monotonic(), 0.01))
            self.buf += self.sock.recv(65536)

    def request(self, pkt: str, timeout: float = 10.0) -> bytes:
        p = pkt.encode()
        self.sock.sendall(b"$" + p + b"#%02x" % (sum(p) % 256))
        return self._read(timeout)

    def eip(self) -> int:
        return int.from_bytes(bytes.fromhex(self.request("g").decode())[32:36], "little")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--log")
    args = ap.parse_args()

    # 1. The SHN_ABS bug. When the CPU sits at the reset vector (0xfff0) the
    #    naive table labels it relative to FLAGS, a constant whose value is 0.
    naive = load_symbols(args.elf, skip_absolute=False)
    fixed = load_symbols(args.elf, skip_absolute=True)
    print(f"[1] naive table: {symbolize(naive, 0xfff0)}    fixed table: {symbolize(fixed, 0xfff0)}")
    assert symbolize(naive, 0xfff0) == "FLAGS+0xfff0"
    assert symbolize(fixed, 0xfff0) == "0xfff0"

    # 2. Symbolization.
    add = next(s for s in fixed if s[2] == "add")
    print(f"[2] {add[0]:#x} -> {symbolize(fixed, add[0])}, "
          f"{add[0] + 9:#x} -> {symbolize(fixed, add[0] + 9)}, "
          f"{add[0] + add[1]:#x} -> {symbolize(fixed, add[0] + add[1])}")
    assert symbolize(fixed, add[0] + 9) == "add+0x9"
    assert not symbolize(fixed, add[0] + add[1]).startswith("add")   # past the end

    # 3. Line table.
    rows = load_lines(args.elf)
    print(f"[3] {len(rows)} line rows; first: {rows[0][0]:#x} {rows[0][1]}:{rows[0][2]}")
    assert any(f == "kernel.c" for _, f, _ in rows) and any(f == "boot.s" for _, f, _ in rows)

    # 4. The data-address bug: `counter` lives in .bss, past every code row,
    #    so a plain bisect hands it the LAST line of code.
    counter = next(s for s in fixed if s[2] == "counter")[0]
    ranges = code_ranges(args.elf)
    wrong = line_for(rows, counter)
    print(f"[4] counter {counter:#x}: naive -> {wrong[1]}:{wrong[2]}, "
          f"fixed -> {line_for(rows, counter, ranges)}")
    assert wrong is not None and line_for(rows, counter, ranges) is None

    # 5. One source line, several addresses: `return a + b;` compiles to
    #    three instructions, and the line program emits a row for each.
    addrs = addresses_for_line(rows, "kernel.c", 10)
    print(f"[5] kernel.c:10 -> {', '.join(hex(a) for a in addrs)}")
    assert len(addrs) > 1 and all(symbolize(fixed, a).startswith("add") for a in addrs)

    # 6. Live: break on the first address of kernel.c:10 and come back.
    rsp = RSP(args.port)
    assert rsp.request(f"Z0,{addrs[0]:x},1") == b"OK"
    rsp.sock.sendall(b"$c#63")
    stop = rsp._read(20)
    assert stop.startswith(b"T05"), stop
    eip = rsp.eip()
    where = line_for(rows, eip, ranges)
    print(f"[6] stopped: eip={eip:#x} = {symbolize(fixed, eip)} at {where[1]}:{where[2]}")
    assert symbolize(fixed, eip) == "add+0x9"
    assert where[1:] == ("kernel.c", 10)

    print("\nall checks passed")


if __name__ == "__main__":
    main()
