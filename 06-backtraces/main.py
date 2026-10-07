"""Module 06: backtraces.

    python main.py --port 1234 --elf ../kernel/kernel.elf --log int.log

Breaks on the FIRST instruction of add() - the hardest place to unwind
from - then builds the call stack three ways:

  1. naive frame-pointer walk     -> add, kmain, _start      (compute is MISSING)
  2. prologue-aware walk          -> add, compute, kmain, _start
  3. stack scan (no frame pointers needed) -> finds compute and kmain too

Then steps past the prologue and shows the naive walk is right again there.

The RSP client is a compact copy of Module 04's.
"""

from __future__ import annotations

import argparse
import bisect
import socket
import time

import capstone
from elftools.elf.elffile import ELFFile

WORD = 4  # i386: every stack slot and pointer is 4 bytes


# --------------------------------------------------------------------------
# Compact RSP client
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
            s = self.buf.find(b"$")
            e = self.buf.find(b"#", max(s, 0))
            if s >= 0 and e >= 0 and len(self.buf) >= e + 3:
                payload, self.buf = self.buf[s + 1:e], self.buf[e + 3:]
                if not self.no_ack:
                    self.sock.sendall(b"+")
                return payload
            self.sock.settimeout(max(deadline - time.monotonic(), 0.01))
            self.buf += self.sock.recv(65536)

    def request(self, pkt: str, timeout: float = 10.0) -> bytes:
        p = pkt.encode()
        self.sock.sendall(b"$" + p + b"#%02x" % (sum(p) % 256))
        return self._read(timeout)

    def regs(self) -> dict[str, int]:
        # i386 'g' order: eax ecx edx ebx esp ebp esi edi eip (Module 03).
        raw = bytes.fromhex(self.request("g").decode())
        names = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi", "eip"]
        return {n: int.from_bytes(raw[i * 4:i * 4 + 4], "little") for i, n in enumerate(names)}

    def read(self, addr: int, n: int) -> bytes:
        return bytes.fromhex(self.request(f"m{addr:x},{n:x}").decode())

    def word(self, addr: int) -> int:
        return int.from_bytes(self.read(addr, WORD), "little")

    def cont(self) -> bytes:
        self.sock.sendall(b"$c#63")
        return self._read(20)

    def step(self) -> bytes:
        self.sock.sendall(b"$s#73")
        return self._read(10)


# --------------------------------------------------------------------------
# Symbols (Module 05, functions only)
# --------------------------------------------------------------------------

class Funcs:
    def __init__(self, path: str):
        with open(path, "rb") as fh:
            elf = ELFFile(fh)
            self.funcs = sorted(
                (s["st_value"], s["st_size"], s.name)
                for s in elf.get_section_by_name(".symtab").iter_symbols()
                if s["st_info"]["type"] == "STT_FUNC"
            )
            text = elf.get_section_by_name(".text")
            self.text_base, self.text = text["sh_addr"], text.data()
        self.starts = [f[0] for f in self.funcs]

    def find(self, addr: int):
        i = bisect.bisect_right(self.starts, addr) - 1
        if i < 0:
            return None
        start, size, name = self.funcs[i]
        return (start, name) if size == 0 or addr < start + size else None

    def name(self, addr: int) -> str:
        f = self.find(addr)
        if f is None:
            return f"{addr:#x}"
        return f[1] if addr == f[0] else f"{f[1]}+{addr - f[0]:#x}"

    def code(self, addr: int, n: int) -> bytes:
        off = addr - self.text_base
        return self.text[off:off + n] if 0 <= off < len(self.text) else b""


# --------------------------------------------------------------------------
# Unwinders
# --------------------------------------------------------------------------

def fp_walk(rsp: RSP, funcs: Funcs, ebp: int, limit: int = 16) -> list[int]:
    """Follow the saved-ebp chain. Each frame looks like:

        [ebp + 4]  return address into the caller
        [ebp + 0]  caller's saved ebp   <- ebp points here
    """
    out = []
    while ebp and len(out) < limit:
        ret = rsp.word(ebp + WORD)
        if funcs.find(ret) is None:       # not inside any function: chain ended
            break
        out.append(ret)
        nxt = rsp.word(ebp)
        if nxt <= ebp:                     # stack grows down; callers are higher
            break
        ebp = nxt
    return out


X86 = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)


def unframed_return(rsp: RSP, funcs: Funcs, eip: int, esp: int) -> int | None:
    """The fix: if eip is inside a prologue (or on a ret), ebp still belongs
    to the caller, so the current return address is found from esp instead.

    Disassemble from the function start up to eip and see how much of
    `push ebp; mov ebp, esp` has already run.
    """
    f = funcs.find(eip)
    if f is None:
        return None
    pushed = False
    for insn in X86.disasm(funcs.code(f[0], eip - f[0] + 16), f[0]):
        if insn.address == eip:
            if insn.mnemonic == "ret":
                return rsp.word(esp)            # epilogue done: ret addr on top
            # before push ebp: ret at [esp]; after it: ebp sits on top of ret
            return rsp.word(esp + WORD) if pushed else rsp.word(esp)
        if insn.mnemonic == "push" and insn.op_str == "ebp":
            pushed = True
        elif insn.mnemonic == "mov" and insn.op_str == "ebp, esp":
            return None                         # frame is set up: fp walk is right
        else:
            return None                         # not a standard prologue
    return None


def preceded_by_call(funcs: Funcs, addr: int) -> bool:
    """Is the instruction ending exactly at addr a call? Try the common x86
    call lengths (5 = call rel32, 2/3/6/7 = indirect forms)."""
    for back in (5, 6, 2, 3, 7):
        code = funcs.code(addr - back, back)
        insns = list(X86.disasm(code, addr - back)) if len(code) == back else []
        if len(insns) == 1 and insns[0].mnemonic == "call" and insns[0].size == back:
            return True
    return False


def stack_scan(rsp: RSP, funcs: Funcs, esp: int, top: int) -> list[int]:
    """Heuristic: every stack word that points just after a call instruction
    inside a known function is *probably* a return address."""
    data = rsp.read(esp, top - esp)
    out = []
    for off in range(0, len(data) - WORD + 1, WORD):
        val = int.from_bytes(data[off:off + WORD], "little")
        if funcs.find(val) is not None and preceded_by_call(funcs, val):
            out.append(val)
    return out


def names(funcs: Funcs, frames: list[int]) -> list[str]:
    return [funcs.name(a).split("+")[0] for a in frames]


def show(title: str, funcs: Funcs, frames: list[int]) -> None:
    print(title)
    for i, a in enumerate(frames):
        print(f"    #{i}  {a:#x} <{funcs.name(a)}>")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--log")
    args = ap.parse_args()

    funcs = Funcs(args.elf)
    add = next(f[0] for f in funcs.funcs if f[2] == "add")
    with open(args.elf, "rb") as fh:
        stack_top = next(s["st_value"] for s in ELFFile(fh).get_section_by_name(".symtab")
                         .iter_symbols() if s.name == "stack_top")

    rsp = RSP(args.port)
    assert rsp.request(f"Z0,{add:x},1") == b"OK"
    assert rsp.cont().startswith(b"T05")
    r = rsp.regs()
    assert r["eip"] == add, "should be on add()'s first instruction"
    print(f"stopped at {funcs.name(r['eip'])}: esp={r['esp']:#x} ebp={r['ebp']:#x}\n")

    # 1. Naive: trust ebp. But add() hasn't run `push ebp; mov ebp, esp`
    #    yet, so ebp is still COMPUTE's frame - and its [ebp+4] is the
    #    return into kmain. compute itself is skipped.
    naive = [r["eip"]] + fp_walk(rsp, funcs, r["ebp"])
    show("[1] naive frame-pointer walk:", funcs, naive)
    assert names(funcs, naive) == ["add", "kmain", "_start"], names(funcs, naive)

    # 2. Fixed: get add()'s own return address from esp, then walk from ebp.
    ret = unframed_return(rsp, funcs, r["eip"], r["esp"])
    fixed = [r["eip"]] + ([ret] if ret is not None else []) + fp_walk(rsp, funcs, r["ebp"])
    show("[2] prologue-aware walk:", funcs, fixed)
    assert names(funcs, fixed) == ["add", "compute", "kmain", "_start"], names(funcs, fixed)

    # 3. Stack scan: needs no frame pointers at all - this is what works on
    #    an -O2 kernel. It can also pick up stale return addresses left in
    #    dead stack slots; here the stack is small and clean.
    scanned = [r["eip"]] + stack_scan(rsp, funcs, r["esp"], stack_top)
    show("[3] stack scan:", funcs, scanned)
    assert {"compute", "kmain"} <= set(names(funcs, scanned))

    # 4. Two steps in, `push ebp; mov ebp, esp` has run: ebp is add()'s own
    #    frame now, the naive walk is correct, and the fix steps aside.
    rsp.step()
    rsp.step()
    r = rsp.regs()
    assert unframed_return(rsp, funcs, r["eip"], r["esp"]) is None
    after = [r["eip"]] + fp_walk(rsp, funcs, r["ebp"])
    show(f"[4] after the prologue (eip={funcs.name(r['eip'])}), naive walk:", funcs, after)
    assert names(funcs, after) == ["add", "compute", "kmain", "_start"]

    print("\nall checks passed")


if __name__ == "__main__":
    main()
